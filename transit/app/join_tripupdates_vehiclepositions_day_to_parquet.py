import argparse
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
from google.cloud import storage
from google.cloud.storage.blob import Blob

from transit_job_config import (
    default_source_date_utc,
    env_int,
    env_value,
    validate_job_args,
)
from transit_gcs_paths import clear_derived_date_prefix, derived_date_prefix
from transit_row_identity import (
    CANONICAL_ROW_KEY,
    assert_unique_canonical_keys,
    require_columns,
)

JOIN_KEYS = CANONICAL_ROW_KEY

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
    agency: str = ""
    source_date: str = ""
    tripupdates_shards_found: int = 0
    vehiclepositions_shards_found: int = 0
    tripupdates_rows: int = 0
    vehiclepositions_rows: int = 0
    vehiclepositions_rows_after_key_dedupe: int = 0
    joined_rows: int = 0
    vehicle_match_rate: float = 0.0
    output_shards_written: int = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Join daily TripUpdates latest parquet with VehiclePositions latest parquet "
            "and upload sharded output."
        )
    )
    parser.add_argument(
        "--bucket",
        default=env_value("TRANSIT_BUCKET"),
        help="Input GCS bucket name (default TRANSIT_BUCKET)",
    )
    parser.add_argument(
        "--agency",
        default=env_value("TRANSIT_AGENCY", "muni"),
        help="Agency folder name (default TRANSIT_AGENCY or muni)",
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
        "--tripupdates-prefix",
        default=env_value(
            "TRANSIT_TRIPUPDATES_PARQUET_PREFIX", "latest/TripUpdates"
        ),
        help="Prefix before /<agency>/<service-date>/ for TripUpdates latest parquet",
    )
    parser.add_argument(
        "--vehiclepositions-prefix",
        default=env_value(
            "TRANSIT_VEHICLEPOSITIONS_PARQUET_PREFIX", "latest/VehiclePositions"
        ),
        help="Prefix before /<agency>/<service-date>/ for VehiclePositions latest parquet",
    )
    parser.add_argument(
        "--output-gcs-bucket",
        default=env_value("TRANSIT_OUTPUT_BUCKET"),
        help="Destination bucket for joined parquet output (defaults to --bucket).",
    )
    parser.add_argument(
        "--output-gcs-prefix",
        default=env_value("TRANSIT_JOINED_PREFIX", "latest/joined"),
        help="Destination prefix before /<agency>/<service-date>/ joined output.",
    )
    parser.add_argument(
        "--output-dir",
        default=env_value(
            "TRANSIT_JOIN_OUTPUT_DIR", "/tmp/tripupdates_vehiclepositions_join"
        ),
        help="Local temp directory for downloaded and generated parquet files.",
    )
    parser.add_argument(
        "--output-shards",
        type=int,
        default=env_int("TRANSIT_JOIN_OUTPUT_SHARDS", 16),
        help="Number of output parquet shards.",
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
            ("tripupdates_prefix", "--tripupdates-prefix"),
            ("vehiclepositions_prefix", "--vehiclepositions-prefix"),
            ("output_gcs_prefix", "--output-gcs-prefix"),
            ("output_dir", "--output-dir"),
        ],
    )


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
    out["trip_start_time"] = out["trip_start_time"].astype("string")
    return out


def _join_frames(df_tu: pd.DataFrame, df_vp: pd.DataFrame) -> pd.DataFrame:
    require_columns(df_tu, JOIN_KEYS, "TripUpdates input")
    require_columns(df_vp, JOIN_KEYS, "VehiclePositions input")

    df_tu = _normalize_join_key_types(df_tu)
    df_vp = _normalize_join_key_types(df_vp)
    assert_unique_canonical_keys(df_tu, "TripUpdates input")
    assert_unique_canonical_keys(df_vp, "VehiclePositions input")

    vp_cols_available = [column for column in VP_COLUMNS_TO_ADD if column in df_vp.columns]
    df_vp_joinable = df_vp[JOIN_KEYS + vp_cols_available].copy()
    df_joined = df_tu.merge(
        df_vp_joinable, on=JOIN_KEYS, how="left", validate="one_to_one"
    )
    if len(df_joined) != len(df_tu):
        raise RuntimeError(
            "Join row count does not match TripUpdates input: "
            f"{len(df_joined):,} joined rows vs {len(df_tu):,} TripUpdates rows."
        )
    assert_unique_canonical_keys(df_joined, "Joined output")
    return df_joined


def _list_parquet_blobs_for_day(
    client: storage.Client,
    bucket_name: str,
    prefix_root: str,
    agency: str,
    service_date: str,
) -> list[Blob]:
    bucket_prefix = f"{prefix_root.strip('/')}/{agency}/{service_date}/"
    blobs = [
        b
        for b in client.list_blobs(bucket_name, prefix=bucket_prefix)
        if b.name.endswith(".parquet")
    ]
    if blobs:
        return sorted(blobs, key=lambda b: b.name)

    # Compatibility fallback for single-file layout.
    single_file_path = f"{prefix_root.strip('/')}/{agency}/{service_date}.parquet"
    single_file_blobs = [
        b
        for b in client.list_blobs(bucket_name, prefix=single_file_path)
        if b.name == single_file_path
    ]
    return single_file_blobs


def _download_and_concat_parquet(
    client: storage.Client,
    bucket_name: str,
    blobs: list[Blob],
    local_dir: Path,
) -> pd.DataFrame:
    if not blobs:
        return pd.DataFrame()

    ensure_dir(str(local_dir))
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
    agency: str,
    service_date: str,
    output_shards: int,
) -> list[str]:
    if df.empty:
        return []

    client = storage.Client()
    bucket = client.bucket(output_bucket_name)
    shard_count = max(1, int(output_shards))
    run_prefix, deleted_count = clear_derived_date_prefix(
        storage_client=client,
        bucket_name=output_bucket_name,
        root=output_gcs_prefix,
        agency=agency,
        source_date=service_date,
    )
    print(
        f"Cleared {deleted_count:,} existing derived object(s) under "
        f"gs://{output_bucket_name}/{run_prefix}"
    )

    uploaded_paths: list[str] = []
    for shard_idx in range(shard_count):
        shard_df = df.iloc[shard_idx::shard_count]
        if shard_df.empty:
            continue
        shard_name = f"part-{shard_idx:05d}.parquet"
        local_path = output_dir / shard_name
        shard_df.to_parquet(local_path, index=False)
        gcs_blob_path = f"{run_prefix}{shard_name}"
        bucket.blob(gcs_blob_path).upload_from_filename(
            str(local_path), content_type="application/octet-stream"
        )
        uploaded_paths.append(gcs_blob_path)

    return uploaded_paths


def main() -> None:
    args = parse_args()
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

    df_joined = _join_frames(df_tu, df_vp)
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
        agency=args.agency,
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
        agency=args.agency,
        source_date=args.service_date,
        tripupdates_shards_found=len(tu_blobs),
        vehiclepositions_shards_found=len(vp_blobs),
        tripupdates_rows=len(df_tu),
        vehiclepositions_rows=len(df_vp),
        vehiclepositions_rows_after_key_dedupe=len(df_vp),
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
        f"gs://{output_bucket_name}/"
        f"{derived_date_prefix(args.output_gcs_prefix, args.agency, args.service_date)}"
    )
    print(f"Local report: {report_path}")
    print(json.dumps(asdict(stats), indent=2))


if __name__ == "__main__":
    main()
