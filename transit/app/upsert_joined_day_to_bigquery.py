import argparse
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass

from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from google.cloud import storage

from transit_job_config import (
    default_source_date_utc,
    env_bool,
    env_value,
    validate_job_args,
)
from transit_gcs_paths import derived_date_prefix
from transit_row_identity import CANONICAL_ROW_KEY

JOIN_KEYS = CANONICAL_ROW_KEY

TARGET_COLUMN_TYPES = {
    "agency_id": "STRING",
    "trip_id": "STRING",
    "trip_start_date": "DATE",
    "trip_start_time": "STRING",
    "route_id": "STRING",
    "direction_id": "INT64",
    "stop_sequence": "INT64",
    "stop_id": "STRING",
    "latest_snapshot_ts": "TIMESTAMP",
    "arrival_time_predicted": "TIMESTAMP",
    "departure_time_predicted": "TIMESTAMP",
    "arrival_delay_sec": "FLOAT64",
    "departure_delay_sec": "FLOAT64",
    "vp_snapshot_ts": "TIMESTAMP",
    "vp_timestamp": "TIMESTAMP",
    "vp_stop_id": "STRING",
    "vehicle_current_status": "FLOAT64",
    "vehicle_id": "STRING",
    "vehicle_label": "STRING",
    "vehicle_latitude": "FLOAT64",
    "vehicle_longitude": "FLOAT64",
    "vehicle_bearing": "FLOAT64",
    "vehicle_speed": "FLOAT64",
    "tu_vp_snapshot_delta_sec": "FLOAT64",
}
TARGET_COLUMNS = list(TARGET_COLUMN_TYPES.keys())
PK_COLUMN = "trip_stop_id"
SOURCE_TO_TARGET = {
    "agency_id": "agency_id",
    "trip_id": "trip_id",
    "trip_start_date": "trip_start_date",
    "trip_start_time": "trip_start_time",
    "route_id": "route_id",
    "direction_id": "direction_id",
    "stop_sequence": "stop_sequence",
    "stop_id": "stop_id",
    "latest_snapshot_ts": "latest_snapshot_ts",
    "arrival_time_predicted": "arrival_time_predicted",
    "departure_time_predicted": "departure_time_predicted",
    "arrival_delay_sec": "arrival_delay_sec",
    "departure_delay_sec": "departure_delay_sec",
    "vp_snapshot_ts": "vp_snapshot_ts",
    "vp_timestamp": "position_timestamp",
    "vp_stop_id": "vp_stop_id",
    "vehicle_current_status": "current_status",
    "vehicle_id": "vehicle_id",
    "vehicle_label": "vehicle_label",
    "vehicle_latitude": "latitude",
    "vehicle_longitude": "longitude",
    "vehicle_bearing": "bearing",
    "vehicle_speed": "speed",
    "tu_vp_snapshot_delta_sec": "tu_vp_snapshot_delta_sec",
}


@dataclass
class UpsertStats:
    agency: str = ""
    source_date: str = ""
    staging_table: str = ""
    source_shard_count: int = 0
    stage_row_count: int = 0
    target_row_count_after_merge: int = 0
    merge_job_id: str = ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Load joined daily parquet shards from GCS into BigQuery staging and "
            "upsert into a target table."
        )
    )
    parser.add_argument(
        "--gcs-bucket",
        default=env_value("TRANSIT_BUCKET"),
        help="Source GCS bucket name (default TRANSIT_BUCKET).",
    )
    parser.add_argument(
        "--agency",
        default=env_value("TRANSIT_AGENCY", "muni"),
        help="Agency name (default TRANSIT_AGENCY or muni).",
    )
    parser.add_argument(
        "--source-gcs-prefix",
        default=env_value("TRANSIT_JOINED_PREFIX", "latest/joined"),
        help="Source prefix before /<agency>/<service-date>/ in GCS.",
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
        "--bq-project",
        default=env_value(
            "TRANSIT_BQ_PROJECT", env_value("GOOGLE_CLOUD_PROJECT")
        ),
        help=(
            "BigQuery project ID (default TRANSIT_BQ_PROJECT or "
            "GOOGLE_CLOUD_PROJECT)."
        ),
    )
    parser.add_argument(
        "--bq-dataset",
        default=env_value("TRANSIT_BQ_DATASET"),
        help="BigQuery dataset name (default TRANSIT_BQ_DATASET).",
    )
    parser.add_argument(
        "--bq-table",
        default=env_value("TRANSIT_BQ_TABLE"),
        help="BigQuery target table name (default TRANSIT_BQ_TABLE).",
    )
    parser.add_argument(
        "--bq-location",
        default=env_value("TRANSIT_BQ_LOCATION", "US"),
        help="BigQuery location for load/query jobs, e.g. US or us-central1.",
    )
    parser.add_argument(
        "--drop-staging-after-merge",
        action="store_true",
        default=env_bool("TRANSIT_DROP_STAGING_AFTER_MERGE"),
        help="Delete staging table after successful merge.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    return validate_job_args(
        parser,
        args,
        required=[
            ("gcs_bucket", "--gcs-bucket/TRANSIT_BUCKET"),
            ("source_gcs_prefix", "--source-gcs-prefix"),
            ("bq_project", "--bq-project/TRANSIT_BQ_PROJECT"),
            ("bq_dataset", "--bq-dataset/TRANSIT_BQ_DATASET"),
            ("bq_table", "--bq-table/TRANSIT_BQ_TABLE"),
            ("bq_location", "--bq-location/TRANSIT_BQ_LOCATION"),
        ],
    )


def _quote_table(project: str, dataset: str, table: str) -> str:
    return f"`{project}.{dataset}.{table}`"


def _sanitize_table_component(value: str, fallback: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", value.strip())
    sanitized = sanitized.strip("_")
    if not sanitized:
        sanitized = fallback
    if sanitized[0].isdigit():
        sanitized = f"x_{sanitized}"
    return sanitized


def build_staging_table_name(
    target_table: str, agency: str, execution_id: str | None = None
) -> str:
    """Return one BigQuery-safe staging table name for a loader execution."""
    execution = (execution_id or os.environ.get("CLOUD_RUN_EXECUTION", "")).strip()
    if not execution:
        execution = uuid.uuid4().hex
    target_component = _sanitize_table_component(target_table, "transit")
    agency_component = _sanitize_table_component(agency, "agency")
    execution_component = _sanitize_table_component(execution, "execution")
    name = f"{target_component}__stage_{agency_component}_{execution_component}"
    return name[:1024]


def _ensure_source_parquet_exists(
    storage_client: storage.Client, bucket_name: str, prefix: str
) -> int:
    blobs = [
        b
        for b in storage_client.list_blobs(bucket_name, prefix=prefix)
        if b.name.endswith(".parquet")
    ]
    if not blobs:
        raise ValueError(f"No parquet shard files found in gs://{bucket_name}/{prefix}")
    return len(blobs)


def _ensure_target_table_exists(
    bq_client: bigquery.Client, project: str, dataset: str, target_table: str
) -> None:
    target_ref = f"{project}.{dataset}.{target_table}"
    try:
        target_obj = bq_client.get_table(target_ref)
        target_cols = {f.name for f in target_obj.schema}
        required_cols = set(TARGET_COLUMNS + [PK_COLUMN])
        missing_cols = sorted(required_cols - target_cols)
        if missing_cols:
            raise ValueError(
                f"Target table {target_ref} is missing required columns: {missing_cols}"
            )
        return
    except NotFound:
        raise ValueError(
            f"Target table not found: {target_ref}. Create it first with the expected schema."
        )


def _null_safe_join_predicate(keys: list[str]) -> str:
    return "\n  AND ".join(
        [
            f"(T.`{col}` = S.`{col}` OR (T.`{col}` IS NULL AND S.`{col}` IS NULL))"
            for col in keys
        ]
    )


def _newer_or_equal_snapshot_predicate() -> str:
    """Keep the target observation when an overlapping source date is older."""
    return """(
  T.`latest_snapshot_ts` IS NULL
  OR (
    S.`latest_snapshot_ts` IS NOT NULL
    AND S.`latest_snapshot_ts` >= T.`latest_snapshot_ts`
  )
)"""


def _build_duplicate_key_count_sql(
    project: str, dataset: str, stage_table: str, stage_columns: list[str]
) -> str:
    missing_keys = [key for key in JOIN_KEYS if key not in stage_columns]
    if missing_keys:
        raise ValueError(
            f"Stage table missing required columns for canonical key: {missing_keys}"
        )
    key_sql = ", ".join(f"`{key}`" for key in JOIN_KEYS)
    return f"""
SELECT COUNT(*) AS duplicate_key_group_count
FROM (
  SELECT {key_sql}
  FROM {_quote_table(project, dataset, stage_table)}
  GROUP BY {key_sql}
  HAVING COUNT(*) > 1
)
""".strip()


def _assert_stage_has_unique_canonical_keys(
    bq_client: bigquery.Client,
    project: str,
    dataset: str,
    stage_table: str,
    stage_columns: list[str],
) -> None:
    query_job = bq_client.query(
        _build_duplicate_key_count_sql(project, dataset, stage_table, stage_columns)
    )
    result = next(iter(query_job.result()))
    duplicate_key_group_count = int(result["duplicate_key_group_count"])
    if duplicate_key_group_count:
        raise ValueError(
            f"Stage table {project}.{dataset}.{stage_table} contains "
            f"{duplicate_key_group_count:,} duplicate canonical key group(s)."
        )


def _build_merge_sql(
    project: str,
    dataset: str,
    target_table: str,
    stage_table: str,
    stage_columns: list[str],
) -> str:
    source_columns = set(stage_columns)
    missing_required_sources = sorted(
        source
        for target, source in SOURCE_TO_TARGET.items()
        if target in JOIN_KEYS and source not in source_columns
    )
    if missing_required_sources:
        raise ValueError(
            "Stage table missing required source columns for merge keys: "
            f"{missing_required_sources}"
        )

    source_select_items = []
    for target_col in TARGET_COLUMNS:
        source_col = SOURCE_TO_TARGET[target_col]
        bq_type = TARGET_COLUMN_TYPES[target_col]
        if source_col in source_columns:
            source_expr = f"CAST(R.`{source_col}` AS {bq_type})"
        else:
            source_expr = f"CAST(NULL AS {bq_type})"
        source_select_items.append(f"{source_expr} AS `{target_col}`")
    source_select_sql = ",\n    ".join(source_select_items)

    missing_keys = [k for k in JOIN_KEYS if k not in TARGET_COLUMNS]
    if missing_keys:
        raise ValueError(f"Target column config missing merge keys: {missing_keys}")

    update_columns = [c for c in TARGET_COLUMNS if c not in JOIN_KEYS]
    update_assignments = [f"T.`{col}` = S.`{col}`" for col in update_columns]
    update_clause = ",\n    ".join(update_assignments)

    insert_columns = [PK_COLUMN] + TARGET_COLUMNS
    insert_values = ["GENERATE_UUID()"] + [f"S.`{col}`" for col in TARGET_COLUMNS]
    insert_columns_sql = ", ".join([f"`{col}`" for col in insert_columns])
    insert_values_sql = ", ".join(insert_values)

    return f"""
MERGE {_quote_table(project, dataset, target_table)} T
USING (
  SELECT
    {source_select_sql}
  FROM {_quote_table(project, dataset, stage_table)} R
) S
ON {_null_safe_join_predicate(JOIN_KEYS)}
    WHEN MATCHED AND {_newer_or_equal_snapshot_predicate()} THEN
  UPDATE SET
    {update_clause}
WHEN NOT MATCHED THEN
  INSERT ({insert_columns_sql})
  VALUES ({insert_values_sql})
""".strip()


def main() -> None:
    args = parse_args()

    source_prefix = derived_date_prefix(
        args.source_gcs_prefix, args.agency, args.service_date
    )
    source_uri = f"gs://{args.gcs_bucket}/{source_prefix}*.parquet"
    staging_table = build_staging_table_name(args.bq_table, args.agency)

    storage_client = storage.Client(project=args.bq_project)
    shard_count = _ensure_source_parquet_exists(
        storage_client=storage_client, bucket_name=args.gcs_bucket, prefix=source_prefix
    )
    print(f"Found {shard_count:,} source shard(s): gs://{args.gcs_bucket}/{source_prefix}")

    bq_client = bigquery.Client(project=args.bq_project, location=args.bq_location)
    stage_table_ref = f"{args.bq_project}.{args.bq_dataset}.{staging_table}"

    load_job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        autodetect=True,
    )
    load_job = bq_client.load_table_from_uri(
        source_uris=source_uri,
        destination=stage_table_ref,
        job_config=load_job_config,
    )
    load_job.result()
    stage_obj = bq_client.get_table(stage_table_ref)
    print(f"Loaded stage rows: {stage_obj.num_rows:,} into {stage_table_ref}")
    if stage_obj.num_rows == 0:
        raise ValueError("Stage table has zero rows after load; aborting merge.")

    _ensure_target_table_exists(
        bq_client=bq_client, project=args.bq_project, dataset=args.bq_dataset, target_table=args.bq_table
    )

    stage_columns = [field.name for field in stage_obj.schema]
    _assert_stage_has_unique_canonical_keys(
        bq_client=bq_client,
        project=args.bq_project,
        dataset=args.bq_dataset,
        stage_table=staging_table,
        stage_columns=stage_columns,
    )
    merge_sql = _build_merge_sql(
        project=args.bq_project,
        dataset=args.bq_dataset,
        target_table=args.bq_table,
        stage_table=staging_table,
        stage_columns=stage_columns,
    )
    merge_job = bq_client.query(merge_sql)
    merge_job.result()

    target_ref = f"{args.bq_project}.{args.bq_dataset}.{args.bq_table}"
    target_obj = bq_client.get_table(target_ref)
    stats = UpsertStats(
        agency=args.agency,
        source_date=args.service_date,
        staging_table=staging_table,
        source_shard_count=shard_count,
        stage_row_count=stage_obj.num_rows,
        target_row_count_after_merge=target_obj.num_rows,
        merge_job_id=merge_job.job_id,
    )

    if args.drop_staging_after_merge:
        bq_client.delete_table(stage_table_ref, not_found_ok=True)
        print(f"Dropped staging table: {stage_table_ref}")

    print("BigQuery upsert complete.")
    print(f"Source URI: {source_uri}")
    print(f"Target table: {target_ref}")
    print(json.dumps(asdict(stats), indent=2))


if __name__ == "__main__":
    main()
