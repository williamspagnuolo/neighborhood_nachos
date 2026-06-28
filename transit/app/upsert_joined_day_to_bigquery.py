import argparse
import datetime as dt
import json
import os
from dataclasses import asdict, dataclass

from google.api_core.exceptions import NotFound
from google.cloud import bigquery
from google.cloud import storage

JOIN_KEYS = [
    "agency_id",
    "trip_id",
    "direction_id",
    "stop_sequence",
    "trip_start_date",
]

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
    source_shard_count: int = 0
    stage_row_count: int = 0
    target_row_count_after_merge: int = 0
    merge_job_id: str = ""


def default_service_date_utc() -> str:
    return (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Load joined daily parquet shards from GCS into BigQuery staging and "
            "upsert into a target table."
        )
    )
    parser.add_argument("--gcs-bucket", required=True, help="Source GCS bucket name.")
    parser.add_argument(
        "--source-gcs-prefix",
        default="latest/joined",
        help="Source prefix before /<service-date>/ in GCS.",
    )
    parser.add_argument(
        "--service-date",
        default=default_service_date_utc(),
        help="Service date (YYYY-MM-DD). Reads from <source-gcs-prefix>/<service-date>/",
    )
    parser.add_argument(
        "--bq-project",
        default=os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        help="BigQuery project ID (defaults to GOOGLE_CLOUD_PROJECT).",
    )
    parser.add_argument("--bq-dataset", required=True, help="BigQuery dataset name.")
    parser.add_argument("--bq-table", required=True, help="BigQuery target table name.")
    parser.add_argument(
        "--bq-staging-table",
        default="",
        help="BigQuery staging table name (defaults to <bq-table>__stage).",
    )
    parser.add_argument(
        "--bq-location",
        default="US",
        help="BigQuery location for load/query jobs, e.g. US or us-central1.",
    )
    parser.add_argument(
        "--drop-staging-after-merge",
        action="store_true",
        help="Delete staging table after successful merge.",
    )
    return parser


def _quote_table(project: str, dataset: str, table: str) -> str:
    return f"`{project}.{dataset}.{table}`"


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
WHEN MATCHED THEN
  UPDATE SET
    {update_clause}
WHEN NOT MATCHED THEN
  INSERT ({insert_columns_sql})
  VALUES ({insert_values_sql})
""".strip()


def main() -> None:
    args = build_parser().parse_args()
    if not args.bq_project:
        raise ValueError("Missing --bq-project and GOOGLE_CLOUD_PROJECT is not set.")

    source_prefix = f"{args.source_gcs_prefix.strip('/')}/{args.service_date}/"
    source_uri = f"gs://{args.gcs_bucket}/{source_prefix}*.parquet"
    staging_table = args.bq_staging_table.strip() or f"{args.bq_table}__stage"

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
