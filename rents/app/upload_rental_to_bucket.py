import requests
import os
import datetime as dt
import json
from google.cloud import storage


def call_rentals_and_upload(api_key, bucket_name):
    query_date = str(dt.date.today())
    
    url = "https://api.rentcast.io/v1/listings/rental/long-term"
    
    headers = {
        "accept": "application/json",
        "X-Api-Key": api_key
    }

    all_items = []
    expected_total = None
    offset = 0
    limit = 500

    while True:
        params = {
                "city": "San Francisco",
                "state": "CA",
                "status": "Active",
                "limit": limit,
                "offset": offset,
                "includeTotalCount": "true",
            }
        
        response = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=60,
        )
        response.raise_for_status()

        page = response.json()
        page_total = int(response.headers['X-Total-Count'])

        if expected_total is None:
            expected_total = page_total

        if page_total != expected_total:
            raise RuntimeError(
                "RentCast total count changed during pagination:"
                f"{expected_total} -> {page_total}"
            )
        
        if not page and len(all_items) < expected_total:
            raise RuntimeError(
                f"RentCast returned an empty page at offset {offset} "
                f"before reaching expected total {expected_total:,}"
            )

        all_items.extend(page)

        if len(all_items) >= expected_total:
            break

        offset += limit

    rentcast_ids = [
    item["id"]
    for item in all_items
    if item.get("id")
    ]

    unique_ids = set(rentcast_ids)

    if len(all_items) != expected_total:
        raise RuntimeError(
            f"Incomplete RentCast pull: expected "
            f"{expected_total:,}, got {len(all_items):,}"
        )

    if len(unique_ids) != expected_total:
        raise RuntimeError(
            f"RentCast pagination returned "
            f"{len(unique_ids):,} unique IDs for "
            f"{expected_total:,} expected listings. "
            "Refusing to infer removals."
        )

    client = storage.Client()
    bucket = client.bucket(bucket_name)

    json_data = json.dumps(all_items, indent=4)
    blob = bucket.blob(f'{query_date}_rentals_api_call')
    blob.upload_from_string(json_data)

    return "rentals called and uploaded successfully"

if __name__ == '__main__':
    rent_api_key = os.environ.get("RENALS_API_KEY")
    bucket_name = os.environ.get("GCS_BUCKET_NAME")

    call_rentals_and_upload(rent_api_key, bucket_name)