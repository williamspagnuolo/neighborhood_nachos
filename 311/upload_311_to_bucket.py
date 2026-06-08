import requests
import os
import datetime as dt
import json
from dotenv import load_dotenv
from google.cloud import storage

load_dotenv()
bucket_name = os.environ.get("GCS_BUCKET_NAME")
service_acct = os.environ.get("GCS_BUCKET_CREDENTIALS")


def call_311_and_upload():
    query_date = str(dt.date.today() - dt.timedelta(days= 2))

    url = f"https://data.sfgov.org/resource/vw6y-z8j6.json?$where=updated_datetime>='{query_date}'&$limit=999999"
    
    response = requests.get(url)
    items = response.json()

    client = storage.Client.from_service_account_json(service_acct)
    bucket = client.bucket(bucket_name)

    json_data = json.dumps(items, indent=4)
    blob = bucket.blob(f'{query_date}_311_api_call')
    blob.upload_from_string(json_data)
    
    return "311 called and uploaded successfully"

call_311_and_upload()