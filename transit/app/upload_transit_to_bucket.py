import os
import datetime as dt

import requests
from google.cloud import storage

FEEDS = {
    "TripUpdates": "https://api.511.org/transit/tripupdates",
    "VehiclePositions": "https://api.511.org/transit/vehiclepositions",
}

BASE_CALL_INTERVAL_SECONDS = 40

AGENCIES = {
    "muni": "SF",
    "bart": "BA",
}


def parse_api_keys(raw_keys):
    if not raw_keys:
        return []
    return [key.strip() for key in raw_keys.split(",") if key.strip()]


def select_api_key(api_keys, fetched_at):
    # Rotate keys deterministically by 40-second slot for stateless runs.
    slot_index = int(fetched_at.timestamp() // BASE_CALL_INTERVAL_SECONDS)
    return api_keys[slot_index % len(api_keys)]


def should_fetch_agency(fetched_at, min_interval_seconds):
    if min_interval_seconds <= 0:
        min_interval_seconds = BASE_CALL_INTERVAL_SECONDS
    slots_between_calls = max(
        1,
        (min_interval_seconds + BASE_CALL_INTERVAL_SECONDS - 1)
        // BASE_CALL_INTERVAL_SECONDS,
    )
    slot_index = int(fetched_at.timestamp() // BASE_CALL_INTERVAL_SECONDS)
    return slot_index % slots_between_calls == 0


def fetch_gtfs_rt_feed(api_key, agency_id, feed_name):
    url = FEEDS[feed_name]
    params = {"api_key": api_key, "agency": agency_id}
    headers = {"Accept": "application/x-protobuf"}

    response = requests.get(url, params=params, headers=headers, timeout=60)
    response.raise_for_status()
    return response.content


def upload_feed_to_bucket(bucket_name, blob_name, data):
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(data, content_type="application/x-protobuf")


def build_blob_name(feed_name, agency_name, fetched_at):
    query_date = fetched_at.strftime("%Y-%m-%d")
    timestamp = fetched_at.strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    return f"{feed_name}/{agency_name}/{query_date}/{timestamp}.pb"


def call_transit_and_upload(feed_agency_api_keys, agency_intervals, bucket_name):
    uploaded = []
    skipped = []

    for feed_name in FEEDS:
        for agency_name, agency_id in AGENCIES.items():
            fetched_at = dt.datetime.now(dt.timezone.utc)
            if not should_fetch_agency(fetched_at, agency_intervals[agency_name]):
                skipped.append(f"{feed_name}/{agency_name}")
                continue

            api_key = select_api_key(
                feed_agency_api_keys[feed_name][agency_name], fetched_at
            )
            data = fetch_gtfs_rt_feed(api_key, agency_id, feed_name)
            blob_name = build_blob_name(feed_name, agency_name, fetched_at)
            upload_feed_to_bucket(bucket_name, blob_name, data)
            uploaded.append(blob_name)

    uploaded_msg = ", ".join(uploaded) if uploaded else "none"
    skipped_msg = ", ".join(skipped) if skipped else "none"
    return (
        "transit feed run complete "
        f"(uploaded: {uploaded_msg}; skipped by interval: {skipped_msg})"
    )


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    feed_agency_api_keys = {
        "TripUpdates": {
            "muni": parse_api_keys(os.environ.get("TRIP_UPDATES_MUNI_API_KEYS")),
            "bart": parse_api_keys(os.environ.get("TRIP_UPDATES_BART_API_KEYS")),
        },
        "VehiclePositions": {
            "muni": parse_api_keys(
                os.environ.get("VEHICLE_POSITIONS_MUNI_API_KEYS")
            ),
            "bart": parse_api_keys(
                os.environ.get("VEHICLE_POSITIONS_BART_API_KEYS")
            ),
        },
    }

    agency_intervals = {
        "muni": int(
            os.environ.get("MUNI_MIN_FETCH_INTERVAL_SECONDS", BASE_CALL_INTERVAL_SECONDS)
        ),
        "bart": int(
            os.environ.get("BART_MIN_FETCH_INTERVAL_SECONDS", BASE_CALL_INTERVAL_SECONDS * 2)
        ),
    }

    bucket_name = os.environ.get("GCS_BUCKET_NAME")

    for feed_name, agency_config in feed_agency_api_keys.items():
        for agency_name, keys in agency_config.items():
            env_var_name = f"{feed_name}_{agency_name}".upper() + "_API_KEYS"
            if not keys:
                raise ValueError(
                    f"Missing API keys for {feed_name} {agency_name}. "
                    f"Set {env_var_name} in .env."
                )
    if not bucket_name:
        raise ValueError("GCS_BUCKET_NAME environment variable is required")

    print(call_transit_and_upload(feed_agency_api_keys, agency_intervals, bucket_name))
