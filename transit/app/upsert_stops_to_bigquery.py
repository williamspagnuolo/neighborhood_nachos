import argparse
import json
import os
from dataclasses import asdict, dataclass
from typing import Any

import requests
from google.cloud import bigquery

STOPS_ENDPOINT = "https://api.511.org/transit/stops"


@dataclass
class StopUpsertStats:
    agencies_requested: int = 0
    raw_stop_candidates: int = 0
    normalized_stops: int = 0
    stage_rows: int = 0
    merged_rows: int = 0
    fk_updated_rows: int = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch 511 transit stops by operator, upsert into BigQuery stops table, "
            "and populate neighborhood/police district foreign keys."
        )
    )
    parser.add_argument(
        "--api-key",
        default=(
            os.environ.get("STOPS_LOCATION_API_KEY", "")
            or os.environ.get("TRANSIT_511_API_KEY", "")
        ),
        help=(
            "511 API key. Defaults to STOPS_LOCATION_API_KEY (or "
            "TRANSIT_511_API_KEY) env var."
        ),
    )
    parser.add_argument(
        "--agencies",
        default="muni:SF,bart:BA",
        help=(
            "Comma-separated agency:operator_id mappings, e.g. "
            "muni:SF,bart:BA"
        ),
    )
    parser.add_argument(
        "--bq-project",
        default=os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        help="BigQuery project id (default GOOGLE_CLOUD_PROJECT).",
    )
    parser.add_argument("--bq-dataset", required=True, help="BigQuery dataset.")
    parser.add_argument("--bq-table", required=True, help="Target stops table.")
    parser.add_argument(
        "--bq-staging-table",
        default="",
        help="Optional staging table (default: <bq-table>__stage).",
    )
    parser.add_argument(
        "--bq-location",
        default="us-central1",
        help="BigQuery location, e.g. us-central1.",
    )
    parser.add_argument(
        "--neighborhoods-table",
        default="neighborhoods",
        help="Neighborhood polygon dimension table name.",
    )
    parser.add_argument(
        "--police-districts-table",
        default="police_districts",
        help="Police district polygon dimension table name.",
    )
    parser.add_argument(
        "--drop-staging-after-merge",
        action="store_true",
        help="Delete staging table after successful merge.",
    )
    return parser


def _parse_agency_mappings(text: str) -> list[tuple[str, str]]:
    mappings: list[tuple[str, str]] = []
    for token in [t.strip() for t in text.split(",") if t.strip()]:
        if ":" not in token:
            raise ValueError(f"Invalid agency mapping '{token}'. Expected agency:operator_id")
        agency, operator_id = token.split(":", 1)
        agency = agency.strip()
        operator_id = operator_id.strip()
        if not agency or not operator_id:
            raise ValueError(f"Invalid agency mapping '{token}'. Empty side not allowed.")
        mappings.append((agency, operator_id))
    if not mappings:
        raise ValueError("No valid agency mappings provided.")
    return mappings


def _get_ci(dct: dict[str, Any], key: str, default: Any = None) -> Any:
    for k, v in dct.items():
        if k.lower() == key.lower():
            return v
    return default


def _extract_stop_candidates(node: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(node, dict):
        location = _get_ci(node, "Location")
        if isinstance(location, dict):
            if _get_ci(node, "id") is not None and (
                _get_ci(location, "Longitude") is not None
                or _get_ci(location, "Latitude") is not None
            ):
                out.append(node)
        for value in node.values():
            _extract_stop_candidates(value, out)
        return
    if isinstance(node, list):
        for item in node:
            _extract_stop_candidates(item, out)


def _fetch_stops_json(api_key: str, operator_id: str) -> dict[str, Any]:
    params = {"api_key": api_key, "operator_id": operator_id, "format": "json"}
    response = requests.get(STOPS_ENDPOINT, params=params, timeout=120)
    response.raise_for_status()
    # 511 can return JSON with UTF-8 BOM; decode with utf-8-sig to avoid
    # JSONDecodeError: Unexpected UTF-8 BOM.
    return json.loads(response.content.decode("utf-8-sig"))


def _normalize_stop(stop_obj: dict[str, Any], agency_id: str) -> dict[str, Any] | None:
    stop_id = _get_ci(stop_obj, "id")
    if stop_id is None:
        return None

    location = _get_ci(stop_obj, "Location")
    if not isinstance(location, dict):
        return None

    lon_raw = _get_ci(location, "Longitude")
    lat_raw = _get_ci(location, "Latitude")
    if lon_raw is None or lat_raw is None:
        return None

    try:
        lon = float(lon_raw)
        lat = float(lat_raw)
    except (TypeError, ValueError):
        return None

    stop_name = _get_ci(stop_obj, "Name")
    return {
        "stop_id": str(stop_id),
        "agency_id": agency_id,
        "stop_name": str(stop_name) if stop_name is not None else None,
        "lat": lat,
        "lon": lon,
        "stop_point": f"POINT({lon} {lat})",
    }


def _load_stage_rows(
    bq: bigquery.Client,
    rows: list[dict[str, Any]],
    stage_table_id: str,
) -> int:
    schema = [
        bigquery.SchemaField("stop_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("agency_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("stop_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("lat", "FLOAT", mode="REQUIRED"),
        bigquery.SchemaField("lon", "FLOAT", mode="REQUIRED"),
        bigquery.SchemaField("stop_point", "GEOGRAPHY", mode="REQUIRED"),
    ]
    load_job = bq.load_table_from_json(
        json_rows=rows,
        destination=stage_table_id,
        job_config=bigquery.LoadJobConfig(
            schema=schema,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            create_disposition=bigquery.CreateDisposition.CREATE_IF_NEEDED,
        ),
    )
    load_job.result()
    return bq.get_table(stage_table_id).num_rows


def _merge_stage_into_target(
    bq: bigquery.Client,
    project: str,
    dataset: str,
    target_table: str,
    stage_table: str,
) -> int:
    target_id = f"`{project}.{dataset}.{target_table}`"
    stage_id = f"`{project}.{dataset}.{stage_table}`"
    merge_sql = f"""
MERGE {target_id} T
USING {stage_id} S
ON T.agency_id = S.agency_id AND T.stop_id = S.stop_id
WHEN MATCHED THEN
  UPDATE SET
    T.stop_name = S.stop_name,
    T.lat = S.lat,
    T.lon = S.lon,
    T.stop_point = S.stop_point
WHEN NOT MATCHED THEN
  INSERT (
    stop_id,
    agency_id,
    stop_name,
    lat,
    lon,
    stop_point
  )
  VALUES (
    S.stop_id,
    S.agency_id,
    S.stop_name,
    S.lat,
    S.lon,
    S.stop_point
  )
"""
    job = bq.query(merge_sql)
    job.result()
    return job.num_dml_affected_rows or 0


def _populate_fk_columns(
    bq: bigquery.Client,
    project: str,
    dataset: str,
    target_table: str,
    neighborhoods_table: str,
    police_districts_table: str,
    agencies: list[str],
) -> int:
    target_id = f"`{project}.{dataset}.{target_table}`"
    nbhd_id = f"`{project}.{dataset}.{neighborhoods_table}`"
    district_id = f"`{project}.{dataset}.{police_districts_table}`"
    agency_list_sql = ", ".join([f"'{a}'" for a in agencies])

    merge_sql = f"""
MERGE {target_id} T
USING (
  SELECT
    S.agency_id,
    S.stop_id,
    ANY_VALUE(N.id) AS neighborhood_id,
    ANY_VALUE(P.id) AS police_district_id
  FROM {target_id} S
  LEFT JOIN {nbhd_id} N
    ON ST_CONTAINS(ST_GEOGFROMTEXT(N.geometry), S.stop_point)
  LEFT JOIN {district_id} P
    ON ST_CONTAINS(ST_GEOGFROMTEXT(P.geometry), S.stop_point)
  WHERE S.agency_id IN ({agency_list_sql})
    AND S.stop_point IS NOT NULL
  GROUP BY S.agency_id, S.stop_id
) F
ON T.agency_id = F.agency_id AND T.stop_id = F.stop_id
WHEN MATCHED THEN
  UPDATE SET
    T.neighborhood_id = F.neighborhood_id,
    T.police_district_id = F.police_district_id
"""
    job = bq.query(merge_sql)
    job.result()
    return job.num_dml_affected_rows or 0


def main() -> None:
    args = build_parser().parse_args()
    if not args.api_key:
        raise ValueError("Missing API key. Provide --api-key or TRANSIT_511_API_KEY.")
    if not args.bq_project:
        raise ValueError("Missing --bq-project and GOOGLE_CLOUD_PROJECT is not set.")

    agency_mappings = _parse_agency_mappings(args.agencies)
    bq = bigquery.Client(project=args.bq_project, location=args.bq_location)
    stage_table = args.bq_staging_table.strip() or f"{args.bq_table}__stage"
    stage_table_id = f"{args.bq_project}.{args.bq_dataset}.{stage_table}"

    candidates: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    for agency_name, operator_id in agency_mappings:
        payload = _fetch_stops_json(api_key=args.api_key, operator_id=operator_id)
        local_candidates: list[dict[str, Any]] = []
        _extract_stop_candidates(payload, local_candidates)
        candidates.extend(local_candidates)
        for candidate in local_candidates:
            row = _normalize_stop(candidate, agency_name)
            if row is not None:
                normalized.append(row)

    deduped_map: dict[tuple[str, str], dict[str, Any]] = {}
    for row in normalized:
        deduped_map[(row["agency_id"], row["stop_id"])] = row
    deduped_rows = list(deduped_map.values())
    if not deduped_rows:
        raise ValueError("No valid stop rows parsed from API responses.")

    stage_row_count = _load_stage_rows(bq=bq, rows=deduped_rows, stage_table_id=stage_table_id)
    merged_count = _merge_stage_into_target(
        bq=bq,
        project=args.bq_project,
        dataset=args.bq_dataset,
        target_table=args.bq_table,
        stage_table=stage_table,
    )
    fk_updated_count = _populate_fk_columns(
        bq=bq,
        project=args.bq_project,
        dataset=args.bq_dataset,
        target_table=args.bq_table,
        neighborhoods_table=args.neighborhoods_table,
        police_districts_table=args.police_districts_table,
        agencies=[a for a, _ in agency_mappings],
    )

    if args.drop_staging_after_merge:
        bq.delete_table(stage_table_id, not_found_ok=True)
        print(f"Dropped staging table: {stage_table_id}")

    stats = StopUpsertStats(
        agencies_requested=len(agency_mappings),
        raw_stop_candidates=len(candidates),
        normalized_stops=len(deduped_rows),
        stage_rows=stage_row_count,
        merged_rows=merged_count,
        fk_updated_rows=fk_updated_count,
    )
    print("Stops upsert complete.")
    print(json.dumps(asdict(stats), indent=2))


if __name__ == "__main__":
    main()
