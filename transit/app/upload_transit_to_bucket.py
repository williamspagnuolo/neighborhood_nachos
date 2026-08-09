import os
import datetime as dt

import requests
from google.cloud import storage

FEEDS = {
    "TripUpdates": "https://api.511.org/transit/tripupdates",
    "VehiclePositions": "https://api.511.org/transit/vehiclepositions",
}

BASE_CALL_INTERVAL_SECONDS = 60
RAW_ROOT_PREFIX = "raw"
LATEST_ROOT_PREFIX = "latest"

AGENCIES = {
    "muni": "SF",
    "bart": "BA",
}


def parse_api_keys(raw_keys):
    if not raw_keys:
        return []
    return [key.strip() for key in raw_keys.split(",") if key.strip()]


def env_bool(name, default=False):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{name} must be one of true/false, 1/0, yes/no, or on/off"
    )


def select_api_key(api_keys, fetched_at):
    # Rotate keys deterministically by base interval slot for stateless runs.
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

    try:
        response = requests.get(url, params=params, headers=headers, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        status_text = f" (HTTP {status_code})" if status_code is not None else ""
        # requests exceptions can include the prepared URL, including api_key.
        raise RuntimeError(
            f"511 {feed_name} request failed for agency {agency_id}{status_text}"
        ) from None
    return response.content


def upload_feed_to_bucket(bucket_name, blob_name, data):
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(data, content_type="application/x-protobuf")


def copy_blob_between_buckets(
    source_bucket_name, source_blob_name, destination_bucket_name, destination_blob_name
):
    client = storage.Client()
    source_bucket = client.bucket(source_bucket_name)
    source_blob = source_bucket.blob(source_blob_name)
    destination_bucket = client.bucket(destination_bucket_name)
    source_bucket.copy_blob(source_blob, destination_bucket, new_name=destination_blob_name)


def build_raw_blob_name(feed_name, agency_name, fetched_at, raw_root_prefix):
    query_date = fetched_at.strftime("%Y-%m-%d")
    timestamp = fetched_at.strftime("%Y-%m-%dT%H-%M-%S.%fZ")
    return f"{raw_root_prefix}/{feed_name}/{agency_name}/{query_date}/{timestamp}.pb"


def call_transit_and_upload(
    feed_agency_api_keys,
    agency_intervals,
    raw_bucket_name,
    raw_root_prefix,
    latest_bucket_name,
    latest_root_prefix,
    copy_raw_to_latest=False,
):
    uploaded_raw = []
    copied_latest = []
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
            raw_blob_name = build_raw_blob_name(
                feed_name, agency_name, fetched_at, raw_root_prefix
            )
            # Write immutable historical snapshot to raw/.
            upload_feed_to_bucket(raw_bucket_name, raw_blob_name, data)
            uploaded_raw.append(raw_blob_name)

            # Optional compatibility path for external consumers of the legacy
            # flat latest/<Feed>/<agency>/<timestamp>.pb layout. The daily
            # pipeline reads protobuf only from raw/ and leaves this disabled.
            if copy_raw_to_latest:
                latest_blob_name = raw_blob_name.replace(
                    f"{raw_root_prefix}/",
                    f"{latest_root_prefix}/",
                    1,
                )
                latest_blob_name = latest_blob_name.replace(
                    f"/{fetched_at.strftime('%Y-%m-%d')}/",
                    "/",
                    1,
                )
                copy_blob_between_buckets(
                    raw_bucket_name,
                    raw_blob_name,
                    latest_bucket_name,
                    latest_blob_name,
                )
                copied_latest.append(latest_blob_name)

    uploaded_raw_msg = ", ".join(uploaded_raw) if uploaded_raw else "none"
    copied_latest_msg = (
        ", ".join(copied_latest)
        if copied_latest
        else "none" if copy_raw_to_latest else "disabled"
    )
    skipped_msg = ", ".join(skipped) if skipped else "none"
    return (
        "transit feed run complete "
        f"(raw uploaded: {uploaded_raw_msg}; "
        f"latest copied: {copied_latest_msg}; "
        f"skipped by interval: {skipped_msg})"
    )


def load_env():
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def build_config_from_env():
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
            os.environ.get("BART_MIN_FETCH_INTERVAL_SECONDS", BASE_CALL_INTERVAL_SECONDS)
        ),
    }

    # Prefer explicit raw bucket naming, but keep backward compatibility.
    raw_bucket_name = os.environ.get("GCS_RAW_BUCKET_NAME") or os.environ.get(
        "GCS_BUCKET_NAME"
    )
    raw_root_prefix = os.environ.get("TRANSIT_RAW_ROOT_PREFIX", RAW_ROOT_PREFIX).strip("/")
    latest_bucket_name = os.environ.get("GCS_LATEST_BUCKET_NAME") or raw_bucket_name
    latest_root_prefix = os.environ.get(
        "TRANSIT_LATEST_ROOT_PREFIX", LATEST_ROOT_PREFIX
    ).strip("/")
    copy_raw_to_latest = env_bool("TRANSIT_COPY_RAW_TO_LATEST", default=False)

    for feed_name, agency_config in feed_agency_api_keys.items():
        for agency_name, keys in agency_config.items():
            env_var_name = f"{feed_name}_{agency_name}".upper() + "_API_KEYS"
            if not keys:
                raise ValueError(
                    f"Missing API keys for {feed_name} {agency_name}. "
                    f"Provide {env_var_name} through the environment "
                    "(Secret Manager in Cloud Run)."
                )
    if not raw_bucket_name:
        raise ValueError(
            "GCS_RAW_BUCKET_NAME (or legacy GCS_BUCKET_NAME) environment variable is required"
        )
    if copy_raw_to_latest and not latest_bucket_name:
        raise ValueError(
            "GCS_LATEST_BUCKET_NAME (or GCS_RAW_BUCKET_NAME/GCS_BUCKET_NAME "
            "fallback) is required when TRANSIT_COPY_RAW_TO_LATEST=true"
        )

    return (
        feed_agency_api_keys,
        agency_intervals,
        raw_bucket_name,
        raw_root_prefix,
        latest_bucket_name,
        latest_root_prefix,
        copy_raw_to_latest,
    )


def run_once_from_env():
    load_env()
    (
        feed_agency_api_keys,
        agency_intervals,
        raw_bucket_name,
        raw_root_prefix,
        latest_bucket_name,
        latest_root_prefix,
        copy_raw_to_latest,
    ) = build_config_from_env()
    return call_transit_and_upload(
        feed_agency_api_keys,
        agency_intervals,
        raw_bucket_name,
        raw_root_prefix,
        latest_bucket_name,
        latest_root_prefix,
        copy_raw_to_latest,
    )


if __name__ == "__main__":
    print(run_once_from_env())
