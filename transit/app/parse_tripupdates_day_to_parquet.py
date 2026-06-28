import argparse
import datetime as dt
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
from google.cloud import storage
from google.transit import gtfs_realtime_pb2


@dataclass
class ParseStats:
    blobs_total: int = 0
    blobs_selected: int = 0
    blobs_parsed: int = 0
    blobs_failed: int = 0
    rows_written: int = 0
    rows_skipped_missing_identity: int = 0
    snapshot_header_count: int = 0
    snapshot_filename_fallback_count: int = 0


def parse_snapshot_ts_from_blob_name(blob_name: str) -> pd.Timestamp:
    ts_text = blob_name.rsplit("/", 1)[-1].replace(".pb", "")
    return pd.to_datetime(
        ts_text, format="%Y-%m-%dT%H-%M-%S.%fZ", utc=True, errors="coerce"
    )


def _safe_arrival_departure_time(event, field_name: str) -> Optional[pd.Timestamp]:
    if not event.HasField(field_name):
        return None
    value = getattr(event, field_name)
    if value.HasField("time"):
        return pd.to_datetime(value.time, unit="s", utc=True)
    return None


def _safe_arrival_departure_delay(event, field_name: str) -> Optional[int]:
    if not event.HasField(field_name):
        return None
    value = getattr(event, field_name)
    if value.HasField("delay"):
        return int(value.delay)
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse one day of TripUpdates blobs into parquet chunks."
    )
    parser.add_argument("--bucket", required=True, help="GCS bucket name")
    parser.add_argument("--agency", default="muni", help="Agency folder name")
    parser.add_argument(
        "--source-root-prefix",
        default="raw/TripUpdates",
        help=(
            "Source root path before agency/date, e.g. raw/TripUpdates "
            "or TripUpdates for legacy layout"
        ),
    )
    parser.add_argument(
        "--service-date",
        default=(dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat(),
        help="Service date folder (YYYY-MM-DD), e.g. 2026-06-23",
    )
    parser.add_argument(
        "--output-dir",
        default="transit/data/parquet/tripupdates_raw",
        help="Directory where parquet chunks and reports will be written",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=250000,
        help="Rows per parquet chunk write",
    )
    parser.add_argument(
        "--sample-n-blobs",
        type=int,
        default=0,
        help="If > 0, parse only the first N blobs for quick testing",
    )
    parser.add_argument(
        "--sample-every-k",
        type=int,
        default=0,
        help="If > 1, parse every kth blob (time-spread sample)",
    )
    parser.add_argument(
        "--max-blob-seconds",
        type=float,
        default=0.0,
        help="If > 0, log blobs that take longer than this many seconds",
    )
    parser.add_argument(
        "--output-gcs-bucket",
        default="",
        help=(
            "Optional destination bucket for uploaded parquet. If omitted while "
            "--write-single-parquet-to-gcs is set, defaults to --bucket."
        ),
    )
    parser.add_argument(
        "--output-gcs-prefix",
        default="latest/TripUpdates",
        help=(
            "Destination prefix before agency/timestamp.parquet when using "
            "--output-gcs-bucket"
        ),
    )
    parser.add_argument(
        "--output-file-timestamp",
        default="",
        help=(
            "Optional output timestamp like 2026-06-25T19-00-00.000000Z. "
            "If omitted, current UTC timestamp is used."
        ),
    )
    parser.add_argument(
        "--output-use-service-date-folder",
        action="store_true",
        help=(
            "Use service date (YYYY-MM-DD) as output folder/file key instead of run "
            "timestamp. Useful for deterministic daily partitions."
        ),
    )
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help=(
            "When writing single parquet to GCS, condense to one latest record per "
            "trip-instance stop key before upload."
        ),
    )
    parser.add_argument(
        "--write-single-parquet-to-gcs",
        action="store_true",
        help=(
            "Upload a single parquet file to GCS. Uses --output-gcs-bucket when "
            "provided, otherwise falls back to --bucket."
        ),
    )
    parser.add_argument(
        "--output-shards",
        type=int,
        default=1,
        help=(
            "Number of parquet files to upload in GCS output mode. "
            "Use >1 for sharded output."
        ),
    )
    parser.add_argument(
        "--task-index",
        type=int,
        default=-1,
        help="Optional explicit task index override (for local testing).",
    )
    parser.add_argument(
        "--task-count",
        type=int,
        default=0,
        help="Optional explicit task count override (for local testing).",
    )
    parser.add_argument(
        "--parallel-finalize-timeout-seconds",
        type=int,
        default=1800,
        help="How long leader task waits for all stage files in parallel mode.",
    )
    return parser


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def flush_rows(rows: list[dict], output_dir: str, chunk_idx: int) -> int:
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    out_path = os.path.join(output_dir, f"part-{chunk_idx:05d}.parquet")
    df.to_parquet(out_path, index=False)
    return len(df)


def _latest_sort_key(snapshot_ts: pd.Timestamp, blob_order: int, blob_name: str) -> tuple:
    # Oldest->newest ordering to mirror notebook dedupe behavior with keep="last".
    return (snapshot_ts, blob_order, blob_name)


def _finalize_latest_frame(latest_rows: list[dict]) -> pd.DataFrame:
    if not latest_rows:
        return pd.DataFrame()

    df_final = pd.DataFrame(latest_rows).rename(columns={"snapshot_ts": "latest_snapshot_ts"})
    final_cols = [
        "agency_id",
        "trip_id",
        "trip_start_date",
        "trip_start_time",
        "route_id",
        "direction_id",
        "stop_sequence",
        "stop_id",
        "latest_snapshot_ts",
        "arrival_time_predicted",
        "departure_time_predicted",
        "arrival_delay_sec",
        "departure_delay_sec",
    ]
    return (
        df_final[final_cols]
        .sort_values(["trip_start_date", "trip_id", "stop_sequence"])
        .reset_index(drop=True)
    )


def _write_and_upload_shards(
    df: pd.DataFrame,
    output_dir: Path,
    output_ts: str,
    output_bucket_name: str,
    output_gcs_prefix: str,
    agency: str,
    output_shards: int,
) -> list[str]:
    if df.empty:
        return []

    client = storage.Client()
    bucket = client.bucket(output_bucket_name)
    shard_count = max(1, int(output_shards))

    if shard_count == 1:
        local_path = output_dir / f"{output_ts}.parquet"
        df.to_parquet(local_path, index=False)
        gcs_blob_path = f"{output_gcs_prefix.strip('/')}/{agency}/{output_ts}.parquet"
        bucket.blob(gcs_blob_path).upload_from_filename(
            str(local_path), content_type="application/octet-stream"
        )
        return [gcs_blob_path]

    uploaded_paths: list[str] = []
    run_prefix = f"{output_gcs_prefix.strip('/')}/{agency}/{output_ts}"
    for shard_idx in range(shard_count):
        shard_df = df.iloc[shard_idx::shard_count]
        if shard_df.empty:
            continue
        shard_name = f"part-{shard_idx:05d}.parquet"
        local_shard_path = output_dir / f"{output_ts}_{shard_name}"
        shard_df.to_parquet(local_shard_path, index=False)
        gcs_blob_path = f"{run_prefix}/{shard_name}"
        bucket.blob(gcs_blob_path).upload_from_filename(
            str(local_shard_path), content_type="application/octet-stream"
        )
        uploaded_paths.append(gcs_blob_path)

    return uploaded_paths


def _task_index_and_count(explicit_index: int, explicit_count: int) -> tuple[int, int]:
    if explicit_count > 0:
        count = explicit_count
        index = explicit_index if explicit_index >= 0 else 0
    else:
        count = int(os.environ.get("CLOUD_RUN_TASK_COUNT", "1"))
        index = int(os.environ.get("CLOUD_RUN_TASK_INDEX", "0"))
    return index, max(1, count)


def _run_id_from_context(output_file_timestamp: str) -> str:
    if output_file_timestamp.strip():
        return output_file_timestamp.strip()
    execution_id = os.environ.get("CLOUD_RUN_EXECUTION", "").strip()
    if execution_id:
        return execution_id
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ")


def _stage_prefix(output_gcs_prefix: str, agency: str, run_id: str) -> str:
    return f"{output_gcs_prefix.strip('/')}/{agency}/_parallel_stage/{run_id}"


def _global_latest_from_stage_df(df_stage: pd.DataFrame) -> pd.DataFrame:
    if df_stage.empty:
        return pd.DataFrame()

    trip_instance_stop_key = [
        "agency_id",
        "trip_id",
        "trip_start_date",
        "trip_start_time",
        "direction_id",
        "stop_sequence",
    ]
    df_latest = (
        df_stage.sort_values(
            ["snapshot_ts", "blob_order", "blob_name"], ascending=[True, True, True]
        )
        .drop_duplicates(subset=trip_instance_stop_key, keep="last")
        .copy()
    )
    return _finalize_latest_frame(df_latest.to_dict(orient="records"))


def _wait_and_load_stage_frames(
    client: storage.Client,
    bucket_name: str,
    stage_prefix: str,
    expected_tasks: int,
    wait_timeout_seconds: int,
    output_dir: Path,
) -> pd.DataFrame:
    deadline = time.time() + wait_timeout_seconds
    bucket = client.bucket(bucket_name)
    blobs = []
    while time.time() < deadline:
        blobs = [
            b
            for b in client.list_blobs(bucket_name, prefix=f"{stage_prefix}/")
            if b.name.endswith(".parquet")
        ]
        if len(blobs) >= expected_tasks:
            break
        print(
            f"Waiting for task stage files: {len(blobs)}/{expected_tasks} present under "
            f"gs://{bucket_name}/{stage_prefix}/"
        )
        time.sleep(10)

    if len(blobs) < expected_tasks:
        raise TimeoutError(
            "Timed out waiting for all task stage files. "
            f"Found {len(blobs)} of expected {expected_tasks}."
        )

    frames = []
    for blob in sorted(blobs, key=lambda b: b.name):
        local_path = output_dir / Path(blob.name).name
        bucket.blob(blob.name).download_to_filename(str(local_path))
        frames.append(pd.read_parquet(local_path))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    args = build_parser().parse_args()
    output_bucket_name = args.output_gcs_bucket.strip() or args.bucket
    write_single_parquet_to_gcs = bool(args.write_single_parquet_to_gcs)
    task_index, task_count = _task_index_and_count(args.task_index, args.task_count)
    run_id = _run_id_from_context(args.output_file_timestamp)

    output_dir = os.path.abspath(args.output_dir)
    ensure_dir(output_dir)

    source_root_prefix = args.source_root_prefix.strip("/")
    prefix = f"{source_root_prefix}/{args.agency}/{args.service_date}/"
    service_date_obj = pd.to_datetime(args.service_date).date()

    client = storage.Client()
    blobs = list(client.list_blobs(args.bucket, prefix=prefix))

    stats = ParseStats(blobs_total=len(blobs), blobs_selected=len(blobs))
    if not blobs:
        raise ValueError(f"No blobs found for prefix: {prefix}")

    blob_manifest = pd.DataFrame(
        {"blob_order": range(len(blobs)), "blob_name": [b.name for b in blobs]}
    )
    if args.sample_every_k and args.sample_every_k > 1:
        blob_manifest = blob_manifest.iloc[:: args.sample_every_k].copy()
    if args.sample_n_blobs and args.sample_n_blobs > 0:
        blob_manifest = blob_manifest.head(args.sample_n_blobs).copy()

    parse_blob_orders = blob_manifest["blob_order"].tolist()
    parse_blobs = [blobs[i] for i in parse_blob_orders]
    if task_count > 1:
        if not (write_single_parquet_to_gcs and args.latest_only):
            raise ValueError(
                "Parallel mode requires --write-single-parquet-to-gcs and --latest-only."
            )
        selected_pairs = [
            (bo, b)
            for bo, b in zip(parse_blob_orders, parse_blobs)
            if bo % task_count == task_index
        ]
        parse_blob_orders = [bo for bo, _ in selected_pairs]
        parse_blobs = [b for _, b in selected_pairs]

    stats.blobs_selected = len(parse_blobs)

    print(f"Found {stats.blobs_total:,} blobs under {prefix}")
    print(
        f"Selected {stats.blobs_selected:,} blobs for parse "
        f"(task_index={task_index}, task_count={task_count})"
    )
    if write_single_parquet_to_gcs:
        print(
            "GCS output mode enabled. Building a single parquet and uploading to "
            f"gs://{output_bucket_name}/{args.output_gcs_prefix.strip('/')}/"
        )
    else:
        print(f"Writing parquet chunks to: {output_dir}")

    failed_blobs: list[dict] = []
    slow_blobs: list[dict] = []
    rows: list[dict] = []
    latest_rows_by_key: dict[tuple, tuple[tuple, dict]] = {}
    chunk_idx = 0

    for i, (blob_order, blob) in enumerate(zip(parse_blob_orders, parse_blobs), start=1):
        t0 = time.perf_counter()
        try:
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(blob.download_as_bytes())

            filename_snapshot_ts = parse_snapshot_ts_from_blob_name(blob.name)
            header_snapshot_ts = pd.NaT
            if feed.HasField("header") and feed.header.HasField("timestamp"):
                header_snapshot_ts = pd.to_datetime(
                    feed.header.timestamp, unit="s", utc=True, errors="coerce"
                )

            if pd.notna(header_snapshot_ts):
                snapshot_ts = header_snapshot_ts
                snapshot_ts_source = "header"
                stats.snapshot_header_count += 1
            else:
                snapshot_ts = filename_snapshot_ts
                snapshot_ts_source = "filename_fallback"
                stats.snapshot_filename_fallback_count += 1

            for entity in feed.entity:
                if not entity.HasField("trip_update"):
                    continue

                tu = entity.trip_update
                trip = tu.trip

                trip_id = trip.trip_id if trip.trip_id else None
                route_id = trip.route_id if trip.route_id else None
                direction_id = trip.direction_id if trip.HasField("direction_id") else None
                trip_start_date_raw = trip.start_date if trip.start_date else None
                trip_start_time = trip.start_time if trip.start_time else None
                trip_start_date = pd.to_datetime(
                    trip_start_date_raw, format="%Y%m%d", errors="coerce"
                )
                trip_start_date = (
                    service_date_obj if pd.isna(trip_start_date) else trip_start_date.date()
                )

                for stu in tu.stop_time_update:
                    stop_sequence = (
                        int(stu.stop_sequence)
                        if stu.HasField("stop_sequence")
                        else None
                    )
                    stop_id = stu.stop_id if stu.stop_id else None

                    if not trip_id or stop_sequence is None or not stop_id:
                        stats.rows_skipped_missing_identity += 1
                        continue

                    row = {
                        "agency_id": args.agency,
                        "blob_order": blob_order,
                        "blob_name": blob.name,
                        "snapshot_ts": snapshot_ts,
                        "snapshot_ts_source": snapshot_ts_source,
                        "trip_id": trip_id,
                        "route_id": route_id,
                        "direction_id": direction_id,
                        "trip_start_date_raw": trip_start_date_raw,
                        "trip_start_date": trip_start_date,
                        "trip_start_time": trip_start_time,
                        "stop_sequence": stop_sequence,
                        "stop_id": stop_id,
                        "arrival_time_predicted": _safe_arrival_departure_time(
                            stu, "arrival"
                        ),
                        "departure_time_predicted": _safe_arrival_departure_time(
                            stu, "departure"
                        ),
                        "arrival_delay_sec": _safe_arrival_departure_delay(
                            stu, "arrival"
                        ),
                        "departure_delay_sec": _safe_arrival_departure_delay(
                            stu, "departure"
                        ),
                    }

                    if args.latest_only:
                        dedupe_key = (
                            row["agency_id"],
                            row["trip_id"],
                            row["trip_start_date"],
                            row["trip_start_time"],
                            row["direction_id"],
                            row["stop_sequence"],
                        )
                        current_sort_key = _latest_sort_key(
                            row["snapshot_ts"], row["blob_order"], row["blob_name"]
                        )
                        existing = latest_rows_by_key.get(dedupe_key)
                        if existing is None or current_sort_key > existing[0]:
                            latest_rows_by_key[dedupe_key] = (current_sort_key, row)
                    else:
                        rows.append(row)

                    if not write_single_parquet_to_gcs and len(rows) >= args.chunk_size:
                        stats.rows_written += flush_rows(rows, output_dir, chunk_idx)
                        chunk_idx += 1
                        rows = []

            stats.blobs_parsed += 1
        except Exception as exc:
            stats.blobs_failed += 1
            failed_blobs.append(
                {
                    "blob_order": blob_order,
                    "blob_name": blob.name,
                    "error": str(exc),
                }
            )

        elapsed = time.perf_counter() - t0
        if args.max_blob_seconds and elapsed > args.max_blob_seconds:
            slow_blobs.append(
                {
                    "blob_order": blob_order,
                    "blob_name": blob.name,
                    "elapsed_seconds": round(elapsed, 3),
                }
            )

        if i % 50 == 0 or i == stats.blobs_selected:
            in_memory_rows = (
                len(latest_rows_by_key)
                if args.latest_only
                else (stats.rows_written + len(rows))
            )
            print(
                f"Progress: {i:,}/{stats.blobs_selected:,} blobs, "
                f"rows_written={in_memory_rows:,}, "
                f"failed={stats.blobs_failed:,}"
            )

    if write_single_parquet_to_gcs:
        if args.latest_only:
            latest_rows = [v[1] for v in latest_rows_by_key.values()]
            if task_count > 1:
                stage_prefix = _stage_prefix(args.output_gcs_prefix, args.agency, run_id)
                task_stage_df = pd.DataFrame(latest_rows)
                task_stage_local = Path(output_dir) / f"task-{task_index:05d}.parquet"
                task_stage_df.to_parquet(task_stage_local, index=False)
                stage_bucket = client.bucket(output_bucket_name)
                stage_blob_path = f"{stage_prefix}/task-{task_index:05d}.parquet"
                stage_bucket.blob(stage_blob_path).upload_from_filename(
                    str(task_stage_local), content_type="application/octet-stream"
                )
                print(
                    f"Uploaded task stage parquet: gs://{output_bucket_name}/{stage_blob_path}"
                )

                if task_index != 0:
                    stats.rows_written = len(task_stage_df)
                    print("Non-leader task complete.")
                    print(json.dumps(asdict(stats), indent=2))
                    return

                print("Leader task waiting for all stage files...")
                stage_df = _wait_and_load_stage_frames(
                    client=client,
                    bucket_name=output_bucket_name,
                    stage_prefix=stage_prefix,
                    expected_tasks=task_count,
                    wait_timeout_seconds=args.parallel_finalize_timeout_seconds,
                    output_dir=Path(output_dir),
                )
                df_final = _global_latest_from_stage_df(stage_df)
            else:
                df_final = _finalize_latest_frame(latest_rows)
        else:
            df_final = pd.DataFrame(rows)

        if df_final.empty:
            raise ValueError("No parsed rows to upload.")

        if args.output_use_service_date_folder:
            output_ts = args.service_date
        else:
            output_ts = args.output_file_timestamp.strip() or dt.datetime.now(
                dt.timezone.utc
            ).strftime("%Y-%m-%dT%H-%M-%S.%fZ")
        uploaded_shards = _write_and_upload_shards(
            df=df_final,
            output_dir=Path(output_dir),
            output_ts=output_ts,
            output_bucket_name=output_bucket_name,
            output_gcs_prefix=args.output_gcs_prefix,
            agency=args.agency,
            output_shards=args.output_shards,
        )
        stats.rows_written = len(df_final)
    else:
        stats.rows_written += flush_rows(rows, output_dir, chunk_idx)

    failed_path = os.path.join(output_dir, "failed_blobs.csv")
    slow_path = os.path.join(output_dir, "slow_blobs.csv")
    report_path = os.path.join(output_dir, "parse_report.json")

    # pd.DataFrame(failed_blobs).to_csv(failed_path, index=False)
    # pd.DataFrame(slow_blobs).to_csv(slow_path, index=False)
    # with open(report_path, "w", encoding="utf-8") as f:
    #     json.dump(asdict(stats), f, indent=2)

    print("Parse complete.")
    print(json.dumps(asdict(stats), indent=2))
    if write_single_parquet_to_gcs:
        if len(uploaded_shards) == 1:
            print(f"Uploaded single parquet: gs://{output_bucket_name}/{uploaded_shards[0]}")
        else:
            print(
                f"Uploaded {len(uploaded_shards):,} parquet shards under "
                f"gs://{output_bucket_name}/{args.output_gcs_prefix.strip('/')}/{args.agency}/"
            )
    print(f"Report: {report_path}")
    print(f"Failed blobs: {failed_path}")
    print(f"Slow blobs: {slow_path}")


if __name__ == "__main__":
    main()
