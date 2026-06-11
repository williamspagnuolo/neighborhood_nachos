import requests
import os
import datetime as dt
import json
from google.cloud import storage


def call_rentals_and_upload(api_key, bucket_name):
    query_date = str(dt.date.today())
    
    url = "https://api.rentcast.io/v1/listings/rental/long-term?city=San%20Francisco&state=CA&status=Active&limit=500"
    
    headers = {
        "accept": "application/json",
        "X-Api-Key": api_key
    }

    response = requests.get(url, headers=headers)
    items = response.json()

    client = storage.Client()
    bucket = client.bucket(bucket_name)

    json_data = json.dumps(items, indent=4)
    blob = bucket.blob(f'{query_date}_rentals_api_call')
    blob.upload_from_string(json_data)

    return "rentals called and uploaded successfully"

if __name__ == '__main__':
    rent_api_key = os.environ.get("RENALS_API_KEY")
    bucket_name = os.environ.get("GCS_BUCKET_NAME")

    call_rentals_and_upload(rent_api_key, bucket_name)