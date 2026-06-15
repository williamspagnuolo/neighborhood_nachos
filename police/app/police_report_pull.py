"""
SFPD incidents -> GCS, designed to run as a Cloud Run Job.

Pulls one day of incidents (defaulting to "yesterday" in Pacific time) from
the SF Socrata API and writes them to a deterministic GCS object path.
Re-running for the same date overwrites the same object, so the job is
idempotent.

Environment variables:
    BUCKET_NAME        GCS bucket to write to (required)
    DATA_GOV_API_KEY   Socrata app token (required)
    TARGET_DATE        Optional YYYY-MM-DD override for backfills

Authentication uses Application Default Credentials: locally via
`gcloud auth application-default login`, on Cloud Run via the attached
service account.
"""

import os
import sys
from datetime import datetime, timedelta

import pendulum
import requests
from google.cloud import storage

BUCKET_NAME = os.environ["BUCKET_NAME"]
APP_TOKEN = os.environ["DATA_GOV_API_KEY"]
API_URL = "https://data.sfgov.org/resource/wg3w-h783.json"

pacific_tz = pendulum.timezone("America/Los_Angeles")


def get_target_date() -> str:
    """Yesterday's date in Pacific time, unless TARGET_DATE is set."""
    override = os.environ.get("TARGET_DATE")
    if override:
        datetime.strptime(override, "%Y-%m-%d")
        return override

    yesterday = datetime.now(pacific_tz).date() - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")


def extract(target_date: str) -> str:
    """Pull one day's incidents from the SFPD Socrata endpoint."""
    next_date = (
        datetime.strptime(target_date, "%Y-%m-%d").date() + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    response = requests.get(
        API_URL,
        headers={"X-App-Token": APP_TOKEN},
        params={
            "$limit": 50000,
            "$where": (
                f"incident_date >= '{target_date}T00:00:00' "
                f"AND incident_date < '{next_date}T00:00:00'"
            ),
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.text


def load_to_gcs(data: str, target_date: str) -> str:
    """Upload to a deterministic, partition-style GCS path."""
    object_name = (
        f"raw/sfpd_reports/"
        f"incident_date={target_date}/"
        f"sfpd_reports_{target_date}.json"
    )

    bucket = storage.Client().bucket(BUCKET_NAME)
    bucket.blob(object_name).upload_from_string(
        data, content_type="application/json"
    )
    return f"gs://{BUCKET_NAME}/{object_name}"


def main() -> None:
    target_date = get_target_date()
    print(f"Pulling SFPD incidents for {target_date}", file=sys.stderr)

    data = extract(target_date)
    print(f"Pulled {len(data):,} bytes", file=sys.stderr)

    uri = load_to_gcs(data, target_date)
    print(f"Wrote {uri}", file=sys.stderr)


if __name__ == "__main__":
    main()
