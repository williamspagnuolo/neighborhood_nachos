# Transit Jobs Layout

This folder keeps job-specific runtime config in one place while using one shared container image from `transit/app/Dockerfile`.

## Files

- `ingest.env.yaml`: env vars for minute-level ingest (`upload_transit_to_bucket.py`)
- `parse_tripupdates.env.yaml`: env vars for TripUpdates parse (`parse_tripupdates_day_to_parquet.py`)
- `parse_vehiclepositions.env.yaml`: env vars for VehiclePositions latest parse (`parse_vehiclepositions_day_to_parquet.py`)
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
```
