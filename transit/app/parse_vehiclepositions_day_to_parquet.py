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

from transit_job_config import (
    default_source_date_utc,
    env_bool,
    env_int,
    env_value,
    validate_job_args,
)
from transit_gcs_paths import clear_derived_date_prefix
from transit_row_identity import CANONICAL_ROW_KEY, assert_unique_canonical_keys

STAGE_COLUMNS = [
    "agency_id",
    "blob_order",
    "blob_name",
    "vp_snapshot_ts",
    "vp_snapshot_ts_source",
    "position_timestamp",
    "trip_id",
    "trip_start_date_raw",
    "trip_start_date",
    "trip_start_time",
    "route_id",
    "direction_id",
    "stop_sequence",
    "vp_stop_id",
    "current_status",
    "vehicle_id",
    "vehicle_label",
    "latitude",
    "longitude",
    "bearing",
    "speed",
]


@dataclass
class ParseStats:
    agency: str = ""
    source_date: str = ""
    blobs_total: int = 0
    blobs_selected: int = 0
    blobs_parsed: int = 0
    blobs_failed: int = 0
    rows_raw: int = 0
    rows_latest: int = 0
    rows_removed_by_dedupe: int = 0
    snapshot_header_count: int = 0
    snapshot_filename_fallback_count: int = 0


def parse_snapshot_ts_from_blob_name(blob_name: str) -> pd.Timestamp:
    ts_text = blob_name.rsplit("/", 1)[-1].replace(".pb", "")
    return pd.to_datetime(
        ts_text, format="%Y-%m-%dT%H-%M-%S.%fZ", utc=True, errors="coerce"
    )


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _has_field(message, field_name: str) -> bool:
    """Check protobuf field presence safely across proto2/proto3 runtime behavior."""
    try:
        return message.HasField(field_name)
    except ValueError:
        # Fallback for scalar fields where HasField is disallowed by runtime.
        return any(fd.name == field_name for fd, _ in message.ListFields())


def _latest_sort_key(vp_snapshot_ts: pd.Timestamp, blob_order: int, blob_name: str) -> tuple:
    return (vp_snapshot_ts, blob_order, blob_name)


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


def _finalize_latest_df(df_stage: pd.DataFrame) -> pd.DataFrame:
    if df_stage.empty:
        return pd.DataFrame()

    vp_for_latest = df_stage.dropna(
        subset=["trip_id", "stop_sequence", "vp_snapshot_ts"]
    ).copy()
    return (
        vp_for_latest.sort_values(
            ["vp_snapshot_ts", "blob_order", "blob_name"], ascending=[True, True, True]
        )
        .drop_duplicates(subset=CANONICAL_ROW_KEY, keep="last")
        .reset_index(drop=True)
    )


def _raise_for_blob_failures(stats: ParseStats) -> None:
    if not stats.blobs_failed:
        return
    raise RuntimeError(
        f"VehiclePositions parsing failed for {stats.blobs_failed:,} blob(s) "
        f"for agency={stats.agency}, source_date={stats.source_date}."
    )


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


def _write_and_upload_shards(
    df: pd.DataFrame,
    output_dir: Path,
    output_key: str,
    output_bucket_name: str,
    output_gcs_prefix: str,
    agency: str,
    output_shards: int,
    clear_source_date_prefix: bool = False,
) -> list[str]:
    if df.empty:
        return []

    client = storage.Client()
    bucket = client.bucket(output_bucket_name)
    shard_count = max(1, int(output_shards))

    if clear_source_date_prefix:
        cleared_prefix, deleted_count = clear_derived_date_prefix(
            storage_client=client,
            bucket_name=output_bucket_name,
            root=output_gcs_prefix,
            agency=agency,
            source_date=output_key,
        )
        print(
            f"Cleared {deleted_count:,} existing derived object(s) under "
            f"gs://{output_bucket_name}/{cleared_prefix}"
        )

    if shard_count == 1:
        local_path = output_dir / f"{output_key}.parquet"
        df.to_parquet(local_path, index=False)
        gcs_blob_path = f"{output_gcs_prefix.strip('/')}/{agency}/{output_key}.parquet"
        bucket.blob(gcs_blob_path).upload_from_filename(
            str(local_path), content_type="application/octet-stream"
        )
        return [gcs_blob_path]

    uploaded_paths: list[str] = []
    run_prefix = f"{output_gcs_prefix.strip('/')}/{agency}/{output_key}"
    for shard_idx in range(shard_count):
        shard_df = df.iloc[shard_idx::shard_count]
        if shard_df.empty:
            continue
        shard_name = f"part-{shard_idx:05d}.parquet"
        local_shard_path = output_dir / f"{output_key}_{shard_name}"
        shard_df.to_parquet(local_shard_path, index=False)
        gcs_blob_path = f"{run_prefix}/{shard_name}"
        bucket.blob(gcs_blob_path).upload_from_filename(
            str(local_shard_path), content_type="application/octet-stream"
        )
        uploaded_paths.append(gcs_blob_path)

    return uploaded_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Parse one day of VehiclePositions blobs, keep latest observation per "
            "composite key, and upload parquet output."
        )
    )
    parser.add_argument(
        "--bucket",
        default=env_value("TRANSIT_BUCKET"),
        help="GCS bucket name (default TRANSIT_BUCKET)",
    )
    parser.add_argument(
        "--agency",
        default=env_value("TRANSIT_AGENCY", "muni"),
        help="Agency folder name (default TRANSIT_AGENCY or muni)",
    )
    parser.add_argument(
        "--source-root-prefix",
        default=env_value(
            "TRANSIT_VEHICLEPOSITIONS_RAW_PREFIX", "raw/VehiclePositions"
        ),
        help=(
            "Source root path before agency/date, e.g. raw/VehiclePositions "
            "or VehiclePositions for legacy layout"
        ),
    )
    parser.add_argument(
        "--service-date",
        default=env_value("TRANSIT_SOURCE_DATE", default_source_date_utc()),
        help=(
            "UTC source folder date (YYYY-MM-DD). Defaults to TRANSIT_SOURCE_DATE "
            "or yesterday UTC."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=env_value(
            "TRANSIT_VEHICLEPOSITIONS_OUTPUT_DIR", "/tmp/vehiclepositions_latest"
        ),
        help="Local working directory for temporary parquet and reports",
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
        "--output-gcs-bucket",
        default=env_value("TRANSIT_OUTPUT_BUCKET"),
        help=(
            "Optional destination bucket for uploaded parquet. "
            "If omitted, defaults to --bucket."
        ),
    )
    parser.add_argument(
        "--output-gcs-prefix",
        default=env_value(
            "TRANSIT_VEHICLEPOSITIONS_PARQUET_PREFIX", "latest/VehiclePositions"
        ),
        help="Destination prefix before agency/timestamp or date folder.",
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
        default=env_bool("TRANSIT_USE_SOURCE_DATE_FOLDER"),
        help=(
            "Use service date (YYYY-MM-DD) as output folder/file key instead of run "
            "timestamp."
        ),
    )
    parser.add_argument(
        "--output-shards",
        type=int,
        default=env_int("TRANSIT_VEHICLEPOSITIONS_OUTPUT_SHARDS", 1),
        help="Number of parquet files to upload. Use >1 for sharded output.",
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
        default=env_int("TRANSIT_PARALLEL_FINALIZE_TIMEOUT_SECONDS", 1800),
        help="How long leader task waits for all stage files in parallel mode.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    return validate_job_args(
        parser,
        args,
        required=[
            ("bucket", "--bucket/TRANSIT_BUCKET"),
            ("source_root_prefix", "--source-root-prefix"),
            ("output_dir", "--output-dir"),
            ("output_gcs_prefix", "--output-gcs-prefix"),
        ],
    )


def main() -> None:
    args = parse_args()
    output_bucket_name = args.output_gcs_bucket.strip() or args.bucket
    task_index, task_count = _task_index_and_count(args.task_index, args.task_count)
    run_id = _run_id_from_context(args.output_file_timestamp)

    output_dir = os.path.abspath(args.output_dir)
    ensure_dir(output_dir)

    source_root_prefix = args.source_root_prefix.strip("/")
    prefix = f"{source_root_prefix}/{args.agency}/{args.service_date}/"
    service_date_obj = pd.to_datetime(args.service_date).date()

    client = storage.Client()
    blobs = list(client.list_blobs(args.bucket, prefix=prefix))

    stats = ParseStats(
        agency=args.agency,
        source_date=args.service_date,
        blobs_total=len(blobs),
        blobs_selected=len(blobs),
    )
    if not blobs:
        raise ValueError(f"No VehiclePositions blobs found for prefix: {prefix}")

    manifest = pd.DataFrame(
        {"blob_order": range(len(blobs)), "blob_name": [b.name for b in blobs]}
    )
    if args.sample_every_k and args.sample_every_k > 1:
        manifest = manifest.iloc[:: args.sample_every_k].copy()
    if args.sample_n_blobs and args.sample_n_blobs > 0:
        manifest = manifest.head(args.sample_n_blobs).copy()

    parse_blob_orders = manifest["blob_order"].tolist()
    parse_blobs = [blobs[i] for i in parse_blob_orders]
    if task_count > 1:
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

    latest_rows_by_key: dict[tuple, tuple[tuple, dict]] = {}
    failed_blobs: list[dict] = []

    for i, (blob_order, blob) in enumerate(zip(parse_blob_orders, parse_blobs), start=1):
        try:
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(blob.download_as_bytes())

            filename_snapshot_ts = parse_snapshot_ts_from_blob_name(blob.name)
            header_snapshot_ts = pd.NaT
            if _has_field(feed, "header") and _has_field(feed.header, "timestamp"):
                header_snapshot_ts = pd.to_datetime(
                    feed.header.timestamp, unit="s", utc=True, errors="coerce"
                )

            if pd.notna(header_snapshot_ts):
                vp_snapshot_ts = header_snapshot_ts
                vp_snapshot_ts_source = "header"
                stats.snapshot_header_count += 1
            else:
                vp_snapshot_ts = filename_snapshot_ts
                vp_snapshot_ts_source = "filename_fallback"
                stats.snapshot_filename_fallback_count += 1

            for entity in feed.entity:
                if not entity.HasField("vehicle"):
                    continue

                vp = entity.vehicle
                trip = vp.trip
                vehicle = vp.vehicle
                position = vp.position

                trip_id = trip.trip_id if trip.trip_id else None
                route_id = trip.route_id if trip.route_id else None
                trip_start_date_raw = trip.start_date if trip.start_date else None
                trip_start_time = trip.start_time if trip.start_time else None
                trip_start_date = pd.to_datetime(
                    trip_start_date_raw, format="%Y%m%d", errors="coerce"
                )
                trip_start_date = (
                    service_date_obj if pd.isna(trip_start_date) else trip_start_date.date()
                )
                direction_id = trip.direction_id if _has_field(trip, "direction_id") else None
                stop_sequence = (
                    int(vp.current_stop_sequence)
                    if _has_field(vp, "current_stop_sequence")
                    else None
                )
                vp_stop_id = vp.stop_id if vp.stop_id else None
                current_status = (
                    int(vp.current_status) if _has_field(vp, "current_status") else None
                )
                vehicle_id = vehicle.id if vehicle.id else None
                vehicle_label = vehicle.label if vehicle.label else None

                position_timestamp = pd.NaT
                if _has_field(vp, "timestamp"):
                    position_timestamp = pd.to_datetime(
                        vp.timestamp, unit="s", utc=True, errors="coerce"
                    )

                latitude: Optional[float] = None
                longitude: Optional[float] = None
                bearing: Optional[float] = None
                speed: Optional[float] = None
                if _has_field(vp, "position"):
                    latitude = (
                        float(position.latitude)
                        if _has_field(position, "latitude")
                        else None
                    )
                    longitude = (
                        float(position.longitude)
                        if _has_field(position, "longitude")
                        else None
                    )
                    bearing = (
                        float(position.bearing) if _has_field(position, "bearing") else None
                    )
                    speed = float(position.speed) if _has_field(position, "speed") else None

                row = {
                    "agency_id": args.agency,
                    "blob_order": blob_order,
                    "blob_name": blob.name,
                    "vp_snapshot_ts": vp_snapshot_ts,
                    "vp_snapshot_ts_source": vp_snapshot_ts_source,
                    "position_timestamp": position_timestamp,
                    "trip_id": trip_id,
                    "trip_start_date_raw": trip_start_date_raw,
                    "trip_start_date": trip_start_date,
                    "trip_start_time": trip_start_time,
                    "route_id": route_id,
                    "direction_id": direction_id,
                    "stop_sequence": stop_sequence,
                    "vp_stop_id": vp_stop_id,
                    "current_status": current_status,
                    "vehicle_id": vehicle_id,
                    "vehicle_label": vehicle_label,
                    "latitude": latitude,
                    "longitude": longitude,
                    "bearing": bearing,
                    "speed": speed,
                }

                if (
                    row["trip_id"] is not None
                    and row["stop_sequence"] is not None
                    and pd.notna(row["vp_snapshot_ts"])
                ):
                    dedupe_key = tuple(row[column] for column in CANONICAL_ROW_KEY)
                    current_sort_key = _latest_sort_key(
                        row["vp_snapshot_ts"], row["blob_order"], row["blob_name"]
                    )
                    existing = latest_rows_by_key.get(dedupe_key)
                    if existing is None or current_sort_key > existing[0]:
                        latest_rows_by_key[dedupe_key] = (current_sort_key, row)

            stats.blobs_parsed += 1
        except Exception as exc:
            stats.blobs_failed += 1
            if stats.blobs_failed <= 5:
                print(f"Blob parse failure [{blob_order}] {blob.name}: {exc!r}")
            failed_blobs.append(
                {"blob_order": blob_order, "blob_name": blob.name, "error": str(exc)}
            )

        if i % 50 == 0 or i == stats.blobs_selected:
            print(
                f"Progress: {i:,}/{stats.blobs_selected:,} blobs, "
                f"latest_keys={len(latest_rows_by_key):,}, failed={stats.blobs_failed:,}"
            )

    if stats.blobs_failed:
        print(json.dumps(asdict(stats), indent=2))
        _raise_for_blob_failures(stats)

    task_latest_rows = [v[1] for v in latest_rows_by_key.values()]
    task_stage_df = pd.DataFrame(task_latest_rows, columns=STAGE_COLUMNS)
    stats.rows_raw = len(task_stage_df)

    if task_count > 1:
        stage_prefix = _stage_prefix(args.output_gcs_prefix, args.agency, run_id)
        task_stage_local = Path(output_dir) / f"task-{task_index:05d}.parquet"
        task_stage_df.to_parquet(task_stage_local, index=False)
        stage_blob_path = f"{stage_prefix}/task-{task_index:05d}.parquet"
        stage_bucket = client.bucket(output_bucket_name)
        stage_bucket.blob(stage_blob_path).upload_from_filename(
            str(task_stage_local), content_type="application/octet-stream"
        )
        print(f"Uploaded task stage parquet: gs://{output_bucket_name}/{stage_blob_path}")

        if task_index != 0:
            stats.rows_latest = len(task_stage_df)
            stats.rows_removed_by_dedupe = 0
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
        df_vp_latest = _finalize_latest_df(stage_df)
        stats.rows_latest = len(df_vp_latest)
        stats.rows_removed_by_dedupe = len(stage_df) - len(df_vp_latest)
    else:
        df_vp_latest = _finalize_latest_df(task_stage_df)
        stats.rows_latest = len(df_vp_latest)
        stats.rows_removed_by_dedupe = len(task_stage_df) - len(df_vp_latest)

    if df_vp_latest.empty:
        raise ValueError(
            f"VehiclePositions parser produced zero rows for agency={args.agency}, "
            f"source_date={args.service_date}."
        )
    assert_unique_canonical_keys(df_vp_latest, "VehiclePositions parser output")

    if args.output_use_service_date_folder:
        output_key = args.service_date
    else:
        output_key = args.output_file_timestamp.strip() or dt.datetime.now(
            dt.timezone.utc
        ).strftime("%Y-%m-%dT%H-%M-%S.%fZ")

    uploaded_paths = _write_and_upload_shards(
        df=df_vp_latest,
        output_dir=Path(output_dir),
        output_key=output_key,
        output_bucket_name=output_bucket_name,
        output_gcs_prefix=args.output_gcs_prefix,
        agency=args.agency,
        output_shards=args.output_shards,
        clear_source_date_prefix=args.output_use_service_date_folder,
    )

    report_path = Path(output_dir) / f"{output_key}_vehiclepositions_parse_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(asdict(stats), f, indent=2)

    print("VehiclePositions latest parse complete.")
    if len(uploaded_paths) == 1:
        print(f"Uploaded single parquet: gs://{output_bucket_name}/{uploaded_paths[0]}")
    else:
        print(
            f"Uploaded {len(uploaded_paths):,} parquet shards under "
            f"gs://{output_bucket_name}/{args.output_gcs_prefix.strip('/')}/{args.agency}/"
        )
    print(f"Local report: {report_path}")
    print(json.dumps(asdict(stats), indent=2))


if __name__ == "__main__":
    main()
