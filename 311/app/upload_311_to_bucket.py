import os
import datetime as dt
import json
import requests
from google.cloud import storage


def call_311_and_upload(bucket_name):
    query_date = str(dt.date.today() - dt.timedelta(days= 2))

    url = f"https://data.sfgov.org/resource/vw6y-z8j6.json?$where=updated_datetime>='{query_date}'&$limit=999999"
    
    response = requests.get(url)
    items = response.json()

    client = storage.Client()
    bucket = client.bucket(bucket_name)

    json_data = json.dumps(items, indent=4)
    blob = bucket.blob(f'{query_date}_311_api_call')
    blob.upload_from_string(json_data)
    
    return "311 called and uploaded successfully"

if __name__ == '__main__':
    bucket_name = os.environ.get("GCS_BUCKET_NAME")
    call_311_and_upload(bucket_name)