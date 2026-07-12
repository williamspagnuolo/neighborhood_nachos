"""
SFPD incidents -> GCS, designed to run as a Cloud Run Job.

Pulls a window of Pacific-time dates from the SF Socrata API and writes
each day's incidents to a deterministic, date-partitioned GCS object.
Re-running for the same date overwrites the same object, so a rolling
LOOKBACK_DAYS window safely refreshes prior days as SFPD's reporting
lag fills in.

Environment variables:
    BUCKET_NAME       GCS bucket to write to (required)
    DATA_GOV_API_KEY  Socrata app token (required)
    LOOKBACK_DAYS     How many days to pull, ending at TARGET_DATE
                      (or yesterday Pacific). Default: 10.
    TARGET_DATE       Optional YYYY-MM-DD end date for the window.
                      Default: yesterday in Pacific time.

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
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "10"))
API_URL = "https://data.sfgov.org/resource/wg3w-h783.json"

pacific_tz = pendulum.timezone("America/Los_Angeles")


def get_end_date() -> str:
    """End-of-window date in Pacific time, unless TARGET_DATE is set."""
    override = os.environ.get("TARGET_DATE")
    if override:
        datetime.strptime(override, "%Y-%m-%d")
        return override
    yesterday = datetime.now(pacific_tz).date() - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")


def get_date_window(end_date: str, lookback_days: int) -> list[str]:
    """Chronological list of YYYY-MM-DD covering [end_date - lookback + 1, end_date]."""
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    return [
        (end - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(lookback_days - 1, -1, -1)
    ]


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


def process_one_day(target_date: str) -> None:
    print(f"--- {target_date} ---", file=sys.stderr)
    print(f"Pulling SFPD incidents for {target_date}", file=sys.stderr)
    data = extract(target_date)
    print(f"Pulled {len(data):,} bytes", file=sys.stderr)
    uri = load_to_gcs(data, target_date)
    print(f"Wrote {uri}", file=sys.stderr)


def main() -> None:
    end_date = get_end_date()
    dates = get_date_window(end_date, LOOKBACK_DAYS)
    print(
        f"Window: {dates[0]} .. {dates[-1]} ({LOOKBACK_DAYS} day(s))",
        file=sys.stderr,
    )
    for d in dates:
        process_one_day(d)


if __name__ == "__main__":
    main()
