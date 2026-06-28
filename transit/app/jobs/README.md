# Transit Jobs Layout

This folder keeps job-specific runtime config in one place while using one shared container image from `transit/app/Dockerfile`.

## Files

- `ingest.env.yaml`: env vars for minute-level ingest (`upload_transit_to_bucket.py`)
- `parse_tripupdates.env.yaml`: env vars for TripUpdates parse (`parse_tripupdates_day_to_parquet.py`)
- `parse_vehiclepositions.env.yaml`: env vars for VehiclePositions latest parse (`parse_vehiclepositions_day_to_parquet.py`)
- `join_tripupdates_vehiclepositions.env.yaml`: env vars for daily join job (`join_tripupdates_vehiclepositions_day_to_parquet.py`)
- `upsert_joined_to_bigquery.env.yaml`: env vars for daily BigQuery upsert (`upsert_joined_day_to_bigquery.py`)
- `deploy_job.sh`: helper to create/update a Cloud Run Job for a specific script
- `run_job.sh`: helper to execute a Cloud Run Job with optional CLI args override

## Recommended Pattern

1. Build one image from `transit/app`.
2. Create/update each Cloud Run Job with script-specific env + args.
3. Schedule jobs independently (or orchestrate in Workflow).

## Example

```bash
# Build one shared image for all transit jobs
export PROJECT_ID="neighboorhood-nachos"
export IMAGE="gcr.io/$PROJECT_ID/transit-jobs:$(date +%Y%m%d%H%M%S)"
gcloud builds submit "transit/app" --project "$PROJECT_ID" --tag "$IMAGE"

# Deploy ingest
bash transit/app/jobs/deploy_job.sh \
  --project "$PROJECT_ID" \
  --region "us-central1" \
  --job "transit-minute-job" \
  --image "$IMAGE" \
  --script "upload_transit_to_bucket.py" \
  --env-file "transit/app/jobs/ingest.env.yaml"

# Deploy parse
bash transit/app/jobs/deploy_job.sh \
  --project "$PROJECT_ID" \
  --region "us-central1" \
  --job "tripupdates-parse-day" \
  --image "$IMAGE" \
  --script "parse_tripupdates_day_to_parquet.py" \
  --env-file "transit/app/jobs/parse_tripupdates.env.yaml"

# Deploy daily join (TripUpdates + VehiclePositions -> latest/joined/<service-date>/)
bash transit/app/jobs/deploy_job.sh \
  --project "$PROJECT_ID" \
  --region "us-central1" \
  --job "tripupdates-vp-join-day" \
  --image "$IMAGE" \
  --script "join_tripupdates_vehiclepositions_day_to_parquet.py" \
  --env-file "transit/app/jobs/join_tripupdates_vehiclepositions.env.yaml" \
  --args-csv "--bucket=511_transit_data,--agency=muni,--service-date=2026-06-23,--tripupdates-prefix=latest/TripUpdates,--vehiclepositions-prefix=latest/VehiclePositions,--output-gcs-bucket=511_transit_data,--output-gcs-prefix=latest/joined,--output-shards=16"

# Deploy daily BigQuery upsert (latest/joined/<service-date>/ -> BigQuery)
bash transit/app/jobs/deploy_job.sh \
  --project "$PROJECT_ID" \
  --region "us-central1" \
  --job "joined-upsert-bigquery-day" \
  --image "$IMAGE" \
  --script "upsert_joined_day_to_bigquery.py" \
  --env-file "transit/app/jobs/upsert_joined_to_bigquery.env.yaml" \
  --args-csv "--gcs-bucket=511_transit_data,--source-gcs-prefix=latest/joined,--service-date=2026-06-23,--bq-project=neighboorhood-nachos,--bq-dataset=transit,--bq-table=tripupdates_vehiclepositions_joined,--bq-location=US"
```
