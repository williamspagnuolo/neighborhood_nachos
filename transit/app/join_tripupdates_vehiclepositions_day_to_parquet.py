import argparse
import datetime as dt
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
from google.cloud import storage

JOIN_KEYS = [
    "agency_id",
    "trip_id",
    "direction_id",
    "stop_sequence",
    "trip_start_date",
]

VP_COLUMNS_TO_ADD = [
    "vp_snapshot_ts",
    "position_timestamp",
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
class JoinStats:
    tripupdates_shards_found: int = 0
    vehiclepositions_shards_found: int = 0
    tripupdates_rows: int = 0
    vehiclepositions_rows: int = 0
    vehiclepositions_rows_after_key_dedupe: int = 0
    joined_rows: int = 0
    vehicle_match_rate: float = 0.0
    output_shards_written: int = 0


def default_service_date_utc() -> str:
    return (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Join daily TripUpdates latest parquet with VehiclePositions latest parquet "
            "and upload sharded output."
        )
    )
    parser.add_argument("--bucket", required=True, help="Input GCS bucket name")
    parser.add_argument("--agency", default="muni", help="Agency folder name")
    parser.add_argument(
        "--service-date",
        default=default_service_date_utc(),
        help="Service date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--tripupdates-prefix",
        default="latest/TripUpdates",
        help="Prefix before /<agency>/<service-date>/ for TripUpdates latest parquet",
    )
    parser.add_argument(
        "--vehiclepositions-prefix",
        default="latest/VehiclePositions",
        help="Prefix before /<agency>/<service-date>/ for VehiclePositions latest parquet",
    )
    parser.add_argument(
        "--output-gcs-bucket",
        default="",
        help="Destination bucket for joined parquet output (defaults to --bucket).",
    )
    parser.add_argument(
        "--output-gcs-prefix",
        default="latest",
        help="Destination prefix for joined output (service date is appended).",
    )
    parser.add_argument(
        "--output-dir",
        default="/tmp/tripupdates_vehiclepositions_join",
        help="Local temp directory for downloaded and generated parquet files.",
    )
    parser.add_argument(
        "--output-shards",
        type=int,
        default=16,
        help="Number of output parquet shards.",
    )
    return parser


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _normalize_join_key_types(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["trip_id"] = out["trip_id"].astype("string")
    out["agency_id"] = out["agency_id"].astype("string")
    out["direction_id"] = pd.to_numeric(out["direction_id"], errors="coerce")
    out["stop_sequence"] = pd.to_numeric(out["stop_sequence"], errors="coerce")
    out["trip_start_date"] = pd.to_datetime(
        out["trip_start_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    return out


def _list_parquet_blobs_for_day(
    client: storage.Client, bucket_name: str, prefix_root: str, agency: str, service_date: str
) -> list[storage.Blob]:
    bucket_prefix = f"{prefix_root.strip('/')}/{agency}/{service_date}/"
    blobs = [
        b for b in client.list_blobs(bucket_name, prefix=bucket_prefix) if b.name.endswith(".parquet")
    ]
    if blobs:
        return sorted(blobs, key=lambda b: b.name)

    # Compatibility fallback for single-file layout.
    single_file_path = f"{prefix_root.strip('/')}/{agency}/{service_date}.parquet"
    single_file_blobs = [
        b for b in client.list_blobs(bucket_name, prefix=single_file_path) if b.name == single_file_path
    ]
    return single_file_blobs


def _download_and_concat_parquet(
    client: storage.Client, bucket_name: str, blobs: list[storage.Blob], local_dir: Path
) -> pd.DataFrame:
    if not blobs:
        return pd.DataFrame()

    bucket = client.bucket(bucket_name)
    local_paths: list[Path] = []
    for idx, blob in enumerate(blobs):
        local_name = f"{idx:05d}_{Path(blob.name).name}"
        local_path = local_dir / local_name
        bucket.blob(blob.name).download_to_filename(str(local_path))
        local_paths.append(local_path)

    return pd.concat((pd.read_parquet(p) for p in local_paths), ignore_index=True)


def _write_and_upload_shards(
    df: pd.DataFrame,
    output_dir: Path,
    output_bucket_name: str,
    output_gcs_prefix: str,
    service_date: str,
    output_shards: int,
) -> list[str]:
    if df.empty:
        return []

    client = storage.Client()
    bucket = client.bucket(output_bucket_name)
    shard_count = max(1, int(output_shards))
    run_prefix = f"{output_gcs_prefix.strip('/')}/{service_date}"

    uploaded_paths: list[str] = []
    for shard_idx in range(shard_count):
        shard_df = df.iloc[shard_idx::shard_count]
        if shard_df.empty:
            continue
        shard_name = f"part-{shard_idx:05d}.parquet"
        local_path = output_dir / shard_name
        shard_df.to_parquet(local_path, index=False)
        gcs_blob_path = f"{run_prefix}/{shard_name}"
        bucket.blob(gcs_blob_path).upload_from_filename(
            str(local_path), content_type="application/octet-stream"
        )
        uploaded_paths.append(gcs_blob_path)

    return uploaded_paths


def main() -> None:
    args = build_parser().parse_args()
    output_bucket_name = args.output_gcs_bucket.strip() or args.bucket

    output_dir = Path(os.path.abspath(args.output_dir))
    ensure_dir(str(output_dir))
    scratch_dir = Path(tempfile.mkdtemp(prefix="joined_daily_", dir=str(output_dir)))

    client = storage.Client()
    tu_blobs = _list_parquet_blobs_for_day(
        client=client,
        bucket_name=args.bucket,
        prefix_root=args.tripupdates_prefix,
        agency=args.agency,
        service_date=args.service_date,
    )
    vp_blobs = _list_parquet_blobs_for_day(
        client=client,
        bucket_name=args.bucket,
        prefix_root=args.vehiclepositions_prefix,
        agency=args.agency,
        service_date=args.service_date,
    )

    if not tu_blobs:
        raise ValueError(
            "No TripUpdates parquet files found for "
            f"gs://{args.bucket}/{args.tripupdates_prefix.strip('/')}/{args.agency}/{args.service_date}/"
        )
    if not vp_blobs:
        raise ValueError(
            "No VehiclePositions parquet files found for "
            f"gs://{args.bucket}/{args.vehiclepositions_prefix.strip('/')}/{args.agency}/{args.service_date}/"
        )

    print(f"TripUpdates shards: {len(tu_blobs):,}")
    print(f"VehiclePositions shards: {len(vp_blobs):,}")

    df_tu = _download_and_concat_parquet(
        client=client, bucket_name=args.bucket, blobs=tu_blobs, local_dir=scratch_dir / "tu"
    )
    df_vp = _download_and_concat_parquet(
        client=client, bucket_name=args.bucket, blobs=vp_blobs, local_dir=scratch_dir / "vp"
    )

    if df_tu.empty:
        raise ValueError("TripUpdates dataframe is empty after loading parquet inputs.")
    if df_vp.empty:
        raise ValueError("VehiclePositions dataframe is empty after loading parquet inputs.")

    missing_tu_cols = [c for c in JOIN_KEYS if c not in df_tu.columns]
    missing_vp_cols = [c for c in JOIN_KEYS if c not in df_vp.columns]
    if missing_tu_cols:
        raise ValueError(f"TripUpdates input missing join columns: {missing_tu_cols}")
    if missing_vp_cols:
        raise ValueError(f"VehiclePositions input missing join columns: {missing_vp_cols}")

    df_tu = _normalize_join_key_types(df_tu)
    df_vp = _normalize_join_key_types(df_vp)

    vp_cols_available = [c for c in VP_COLUMNS_TO_ADD if c in df_vp.columns]
    df_vp_joinable = df_vp[JOIN_KEYS + vp_cols_available].copy()
    if "vp_snapshot_ts" in df_vp_joinable.columns:
        df_vp_joinable["vp_snapshot_ts"] = pd.to_datetime(
            df_vp_joinable["vp_snapshot_ts"], utc=True, errors="coerce"
        )
    df_vp_joinable = (
        df_vp_joinable.sort_values(
            ["vp_snapshot_ts"] if "vp_snapshot_ts" in df_vp_joinable.columns else JOIN_KEYS
        )
        .drop_duplicates(subset=JOIN_KEYS, keep="last")
        .reset_index(drop=True)
    )

    df_joined = df_tu.merge(df_vp_joinable, on=JOIN_KEYS, how="left")
    if "latest_snapshot_ts" in df_joined.columns and "vp_snapshot_ts" in df_joined.columns:
        df_joined["latest_snapshot_ts"] = pd.to_datetime(
            df_joined["latest_snapshot_ts"], utc=True, errors="coerce"
        )
        df_joined["tu_vp_snapshot_delta_sec"] = (
            df_joined["latest_snapshot_ts"] - df_joined["vp_snapshot_ts"]
        ).dt.total_seconds()

    uploaded_paths = _write_and_upload_shards(
        df=df_joined,
        output_dir=output_dir,
        output_bucket_name=output_bucket_name,
        output_gcs_prefix=args.output_gcs_prefix,
        service_date=args.service_date,
        output_shards=args.output_shards,
    )
    if not uploaded_paths:
        raise ValueError("Joined dataframe is empty; nothing uploaded.")

    match_col = "vehicle_id" if "vehicle_id" in df_joined.columns else None
    match_rate = (
        float(df_joined[match_col].notna().mean())
        if match_col is not None and len(df_joined) > 0
        else 0.0
    )
    stats = JoinStats(
        tripupdates_shards_found=len(tu_blobs),
        vehiclepositions_shards_found=len(vp_blobs),
        tripupdates_rows=len(df_tu),
        vehiclepositions_rows=len(df_vp),
        vehiclepositions_rows_after_key_dedupe=len(df_vp_joinable),
        joined_rows=len(df_joined),
        vehicle_match_rate=match_rate,
        output_shards_written=len(uploaded_paths),
    )

    report_path = output_dir / f"{args.service_date}_tripupdates_vehiclepositions_join_report.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(asdict(stats), f, indent=2)

    print("Join complete.")
    print(
        f"Uploaded {len(uploaded_paths):,} shard(s) under "
        f"gs://{output_bucket_name}/{args.output_gcs_prefix.strip('/')}/{args.service_date}/"
    )
    print(f"Local report: {report_path}")
    print(json.dumps(asdict(stats), indent=2))


if __name__ == "__main__":
    main()
