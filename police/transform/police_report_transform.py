"""
SFPD raw JSON -> transformed JSON in GCS + BigQuery, designed to run as a
Cloud Run Job scheduled to fire shortly after police_report_pull.py.

Processes a window of Pacific dates (default just the latest one) so that
SFPD's reporting lag is captured: re-running an older date is a complete
idempotent rebuild via DELETE + LOAD + spatial-join MERGE.

Reads:
    gs://{BUCKET}/raw/sfpd_reports/incident_date={DATE}/sfpd_reports_{DATE}.json

Writes (NDJSON, one row per line; 13 columns, no FKs):
    gs://{BUCKET}/police_report_transformed/incident_date={DATE}/sfpd_reports_transformed_{DATE}.json

Loads + enriches in BigQuery (replace rows for {DATE} Pacific):
    {BQ_PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}

The neighborhood_id / police_district_id foreign keys are populated
post-load via spatial joins (ST_CONTAINS) against:
    {BQ_PROJECT_ID}.{BQ_DATASET}.{NEIGHBORHOODS_TABLE}
    {BQ_PROJECT_ID}.{BQ_DATASET}.{POLICE_DISTRICTS_TABLE}
Both dim tables have schema (id INT64, name STRING, geometry STRING WKT).

Environment variables:
    BUCKET_NAME       GCS bucket (required)
    BQ_PROJECT_ID     Target BigQuery project (required)
    BQ_DATASET        Target BigQuery dataset (required)
    BQ_TABLE          Target BigQuery table (required)
    LOOKBACK_DAYS     How many days to process, ending at TARGET_DATE
                      (or yesterday Pacific). Default: 1.
    TARGET_DATE       Optional YYYY-MM-DD end date for the window.
                      Default: yesterday in Pacific time.

Authentication uses Application Default Credentials.
"""

import json
import os
import sys
from datetime import datetime, timedelta

import pendulum
from google.cloud import bigquery, storage

BUCKET_NAME = os.environ["BUCKET_NAME"]
BQ_PROJECT_ID = os.environ["BQ_PROJECT_ID"]
BQ_DATASET = os.environ["BQ_DATASET"]
BQ_TABLE = os.environ["BQ_TABLE"]
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "1"))

NEIGHBORHOODS_TABLE = "neighborhoods"
POLICE_DISTRICTS_TABLE = "police_districts"

pacific_tz = pendulum.timezone("America/Los_Angeles")

KEEP_FIELDS: dict[str, str] = {
    "incident_id":              "police_incident_id",
    "incident_datetime":        "incident_datetime",
    "report_datetime":          "report_datetime",
    "report_type_code":         "report_type_code",
    "report_type_description":  "report_type_description",
    "incident_code":            "incident_code",
    "incident_category":        "incident_category",
    "incident_subcategory":     "incident_subcategory",
    "incident_description":     "incident_description",
    "resolution":               "resolution",
    "latitude":                 "lat",
    "longitude":                "long",
}

TIMESTAMP_SRC_FIELDS = {"incident_datetime", "report_datetime"}
FLOAT_DST_FIELDS = {"lat", "long"}


def get_end_date() -> str:
    """End-of-window date in Pacific time, unless TARGET_DATE is set."""
    override = os.environ.get("TARGET_DATE")
    if override:
        datetime.strptime(override, "%Y-%m-%d")
        return override
    return (datetime.now(pacific_tz).date() - timedelta(days=1)).strftime("%Y-%m-%d")


def get_date_window(end_date: str, lookback_days: int) -> list[str]:
    """Chronological list of YYYY-MM-DD covering [end_date - lookback + 1, end_date]."""
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    return [
        (end - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(lookback_days - 1, -1, -1)
    ]


def to_pacific_iso(value: str | None) -> str | None:
    """
    Socrata floating timestamps lack timezone info and are SFPD-local
    (Pacific). Re-emit as ISO 8601 with explicit offset so BigQuery
    stores the correct instant.
    """
    if not value:
        return None
    return pendulum.parse(value, tz=pacific_tz).to_iso8601_string()


def point_to_wkt(point: dict | None) -> str | None:
    """GeoJSON Point -> 'POINT(lon lat)' WKT for BigQuery GEOGRAPHY."""
    if not isinstance(point, dict) or point.get("type") != "Point":
        return None
    coords = point.get("coordinates") or []
    if len(coords) != 2:
        return None
    lon, lat = coords
    return f"POINT({lon} {lat})"


def transform_row(row: dict) -> dict:
    out: dict = {}
    for src, dst in KEEP_FIELDS.items():
        val = row.get(src)
        if val is None or val == "":
            out[dst] = None
        elif src in TIMESTAMP_SRC_FIELDS:
            out[dst] = to_pacific_iso(val)
        elif dst in FLOAT_DST_FIELDS:
            try:
                out[dst] = float(val)
            except (TypeError, ValueError):
                out[dst] = None
        else:
            out[dst] = val
    out["point"] = point_to_wkt(row.get("point"))
    return out


def read_raw(gcs: storage.Client, target_date: str) -> list[dict]:
    blob_name = (
        f"raw/sfpd_reports/"
        f"incident_date={target_date}/"
        f"sfpd_reports_{target_date}.json"
    )
    text = gcs.bucket(BUCKET_NAME).blob(blob_name).download_as_text()
    return json.loads(text)


def write_transformed(
    gcs: storage.Client, rows: list[dict], target_date: str
) -> str:
    blob_name = (
        f"police_report_transformed/"
        f"incident_date={target_date}/"
        f"sfpd_reports_transformed_{target_date}.json"
    )
    ndjson = "\n".join(json.dumps(r) for r in rows)
    if ndjson:
        ndjson += "\n"
    gcs.bucket(BUCKET_NAME).blob(blob_name).upload_from_string(
        ndjson, content_type="application/x-ndjson"
    )
    return f"gs://{BUCKET_NAME}/{blob_name}"


def load_to_bigquery(
    bq: bigquery.Client, gcs_uri: str, rows: list[dict], target_date: str
) -> str:
    """
    Idempotent load: DELETE existing rows for target_date (Pacific), then
    append from the GCS NDJSON. Destination columns not present in the
    NDJSON (neighborhood_id, police_district_id) come in as NULL and are
    populated by populate_fk_columns() afterward.
    """
    table_id = f"{BQ_PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}"

    delete_sql = f"""
        DELETE FROM `{table_id}`
        WHERE DATE(incident_datetime, "America/Los_Angeles") = @target_date
    """
    bq.query(
        delete_sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("target_date", "DATE", target_date)
            ]
        ),
    ).result()

    if not rows:
        return table_id

    load_job = bq.load_table_from_uri(
        gcs_uri,
        table_id,
        job_config=bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        ),
    )
    load_job.result()
    return table_id


def populate_fk_columns(bq: bigquery.Client, target_date: str) -> int:
    """
    Spatial-join the just-loaded rows against the dimension polygons
    and fill in neighborhood_id and police_district_id. Returns the
    number of rows the MERGE updated.

    The GROUP BY + ANY_VALUE pattern collapses any accidental duplicate
    matches (overlapping polygons / on-boundary points) to one ID per
    incident, picked arbitrarily but deterministically per row.
    """
    target_table = f"{BQ_PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}"
    nbhd_table = f"{BQ_PROJECT_ID}.{BQ_DATASET}.{NEIGHBORHOODS_TABLE}"
    district_table = f"{BQ_PROJECT_ID}.{BQ_DATASET}.{POLICE_DISTRICTS_TABLE}"

    merge_sql = f"""
        MERGE `{target_table}` T
        USING (
          SELECT
            I.police_incident_id,
            ANY_VALUE(N.id) AS neighborhood_id,
            ANY_VALUE(P.id) AS police_district_id
          FROM `{target_table}` I
          LEFT JOIN `{nbhd_table}` N
            ON ST_CONTAINS(ST_GEOGFROMTEXT(N.geometry), I.point)
          LEFT JOIN `{district_table}` P
            ON ST_CONTAINS(ST_GEOGFROMTEXT(P.geometry), I.point)
          WHERE DATE(I.incident_datetime, "America/Los_Angeles") = @target_date
            AND I.point IS NOT NULL
          GROUP BY I.police_incident_id
        ) S
        ON T.police_incident_id = S.police_incident_id
        WHEN MATCHED THEN UPDATE SET
          T.neighborhood_id = S.neighborhood_id,
          T.police_district_id = S.police_district_id
    """
    job = bq.query(
        merge_sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("target_date", "DATE", target_date)
            ]
        ),
    )
    job.result()
    return job.num_dml_affected_rows or 0


def process_one_day(
    gcs: storage.Client, bq: bigquery.Client, target_date: str
) -> None:
    print(f"--- {target_date} ---", file=sys.stderr)
    print(f"Transforming SFPD incidents for {target_date}", file=sys.stderr)

    raw_rows = read_raw(gcs, target_date)
    print(f"Read {len(raw_rows):,} raw rows from GCS", file=sys.stderr)

    transformed = [transform_row(r) for r in raw_rows]
    print(f"Transformed {len(transformed):,} rows", file=sys.stderr)

    gcs_uri = write_transformed(gcs, transformed, target_date)
    print(f"Wrote {gcs_uri}", file=sys.stderr)

    table_id = load_to_bigquery(bq, gcs_uri, transformed, target_date)
    print(f"Loaded into {table_id}", file=sys.stderr)

    fk_updated = populate_fk_columns(bq, target_date)
    print(
        f"FK lookup: spatially joined {fk_updated:,} rows against "
        f"{NEIGHBORHOODS_TABLE} and {POLICE_DISTRICTS_TABLE}",
        file=sys.stderr,
    )


def main() -> None:
    end_date = get_end_date()
    dates = get_date_window(end_date, LOOKBACK_DAYS)
    print(
        f"Window: {dates[0]} .. {dates[-1]} ({LOOKBACK_DAYS} day(s))",
        file=sys.stderr,
    )

    gcs = storage.Client()
    bq = bigquery.Client(project=BQ_PROJECT_ID)

    for d in dates:
        process_one_day(gcs, bq, d)


if __name__ == "__main__":
    main()
