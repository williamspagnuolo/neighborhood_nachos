# Transit workflow deployment and IAM

This guide covers one shared daily-job image, four Cloud Run Jobs, one
Workflow, and one Scheduler trigger. The minute ingestion service is separate.
All commands below that create or update a resource are **mutations** and need
explicit authorization. Do not use them against production without review.

## Confirmed values and preflight

```text
PROJECT_ID=neighboorhood-nachos
REGION=us-central1
BUCKET=511_transit_data
DATASET=neighborhood_livability_data
TARGET_TABLE=trip_stops
```

The daily cron is intentionally a placeholder: the plans do not establish when
the Muni UTC raw-feed folders are reliably complete. Choose and approve that time
before creating Scheduler.

Read-only checks:

```bash
gcloud run jobs list --project "$PROJECT_ID" --region "$REGION"
gcloud storage ls "gs://$BUCKET/raw/TripUpdates/"
gcloud storage ls "gs://$BUCKET/raw/VehiclePositions/"
bq --project_id="$PROJECT_ID" show "$DATASET.$TARGET_TABLE"
```

## Intended least-privilege identities

| Identity | Resource scope | Role |
|---|---|---|
| Transform account (both parsers and join) | bucket | `roles/storage.objectAdmin` so it can read raw/derived objects and replace only its exact derived prefixes. |
| Loader account | bucket | `roles/storage.objectViewer`. |
| Loader account | project | `roles/bigquery.jobUser`. |
| Loader account | target dataset | `roles/bigquery.dataEditor` for its unique staging table and target merge. |
| Workflow account | each of the four daily jobs | `roles/run.jobsExecutorWithOverrides`. |
| Workflow account | project | `roles/run.viewer` to poll the long-running Cloud Run operations returned when it starts those jobs. |
| Scheduler account | one workflow | `roles/workflows.invoker`. |

Do not retain broad project `Editor` or default Compute Engine identities for
these jobs. `roles/run.jobsExecutorWithOverrides` is required because the
workflow overrides only `TRANSIT_SOURCE_DATE` and `TRANSIT_AGENCY`.

The following is an **IAM mutation** template. It contains no credentials.

```bash
export PROJECT_ID="<approved-project>"
export REGION="us-central1"
export BUCKET="511_transit_data"
export DATASET="neighborhood_livability_data"
export TRANSFORM_SA="transit-transform@${PROJECT_ID}.iam.gserviceaccount.com"
export LOADER_SA="transit-loader@${PROJECT_ID}.iam.gserviceaccount.com"
export WORKFLOW_SA="transit-workflow@${PROJECT_ID}.iam.gserviceaccount.com"
export SCHEDULER_SA="transit-scheduler@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud iam service-accounts create transit-transform --project "$PROJECT_ID"
gcloud iam service-accounts create transit-loader --project "$PROJECT_ID"
gcloud iam service-accounts create transit-workflow --project "$PROJECT_ID"
gcloud iam service-accounts create transit-scheduler --project "$PROJECT_ID"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$TRANSFORM_SA" --role roles/storage.objectAdmin
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$LOADER_SA" --role roles/storage.objectViewer
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$LOADER_SA" --role roles/bigquery.jobUser
bq --project_id="$PROJECT_ID" add-iam-policy-binding --member "serviceAccount:$LOADER_SA" --role roles/bigquery.dataEditor "$DATASET"
for job in tripupdates-parse-day vehiclepositions-parse-day tripupdates-vp-join-day joined-upsert-bigquery-day; do
  gcloud run jobs add-iam-policy-binding "$job" --project "$PROJECT_ID" --region "$REGION" --member "serviceAccount:$WORKFLOW_SA" --role roles/run.jobsExecutorWithOverrides
done
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$WORKFLOW_SA" --role roles/run.viewer
```

## Build and deploy daily jobs

This is a **Cloud Build and Cloud Run mutation**. Use an approved immutable
image reference; never use an unreviewed `latest` tag.

```bash
export IMAGE="<approved-immutable-image-reference>"
gcloud builds submit transit/app --project "$PROJECT_ID" --tag "$IMAGE"

bash transit/app/jobs/deploy_job.sh --project "$PROJECT_ID" --region "$REGION" --job tripupdates-parse-day --image "$IMAGE" --script parse_tripupdates_day_to_parquet.py --env-file transit/app/jobs/parse_tripupdates.env.yaml --service-account "$TRANSFORM_SA"
bash transit/app/jobs/deploy_job.sh --project "$PROJECT_ID" --region "$REGION" --job vehiclepositions-parse-day --image "$IMAGE" --script parse_vehiclepositions_day_to_parquet.py --env-file transit/app/jobs/parse_vehiclepositions.env.yaml --service-account "$TRANSFORM_SA"
bash transit/app/jobs/deploy_job.sh --project "$PROJECT_ID" --region "$REGION" --job tripupdates-vp-join-day --image "$IMAGE" --script join_tripupdates_vehiclepositions_day_to_parquet.py --env-file transit/app/jobs/join_tripupdates_vehiclepositions.env.yaml --service-account "$TRANSFORM_SA"
bash transit/app/jobs/deploy_job.sh --project "$PROJECT_ID" --region "$REGION" --job joined-upsert-bigquery-day --image "$IMAGE" --script upsert_joined_day_to_bigquery.py --env-file transit/app/jobs/upsert_joined_to_bigquery.env.yaml --service-account "$LOADER_SA"
```

`deploy_job.sh` creates a missing job and updates an existing one; it does not
execute a job.

## Deploy workflow and Scheduler

Enable Workflows only in the approved development project first. This is an
**API mutation**:

```bash
gcloud services enable workflows.googleapis.com --project "$PROJECT_ID"
```

Deploying is a **Workflow mutation**:

```bash
export WORKFLOW_NAME="transit-daily"
bash transit/orchestration/deploy_workflow.sh --project "$PROJECT_ID" --region "$REGION" --workflow "$WORKFLOW_NAME" --source transit/orchestration/workflow.yaml --service-account "$WORKFLOW_SA"
gcloud workflows add-iam-policy-binding "$WORKFLOW_NAME" --project "$PROJECT_ID" --location "$REGION" --member "serviceAccount:$SCHEDULER_SA" --role roles/workflows.invoker
```

After a successful development run for Muni, create one Scheduler job. The
workflow defaults to Muni and rejects other agencies; BART processing is deferred
because its VehiclePositions feed did not provide rows compatible with the
approved canonical key during the initial integration run. This is a
**Scheduler mutation**. Supply an approved UTC cron, not a guess.

```bash
export SCHEDULE_CRON_UTC="<approved-daily-cron>"
export SCHEDULER_JOB="transit-daily-trigger"
export WORKFLOW_EXECUTIONS_URI="https://workflowexecutions.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/workflows/${WORKFLOW_NAME}/executions"
SCHEDULER_ARGS=(--project "$PROJECT_ID" --location "$REGION" --schedule "$SCHEDULE_CRON_UTC" --time-zone UTC --uri "$WORKFLOW_EXECUTIONS_URI" --http-method POST --oauth-service-account-email "$SCHEDULER_SA" --oauth-token-scope https://www.googleapis.com/auth/cloud-platform --headers Content-Type=application/json --message-body '{}')
if gcloud scheduler jobs describe "$SCHEDULER_JOB" --project "$PROJECT_ID" --location "$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SCHEDULER_JOB" "${SCHEDULER_ARGS[@]}"
else
  gcloud scheduler jobs create http "$SCHEDULER_JOB" "${SCHEDULER_ARGS[@]}"
fi
```

Read-only post-deployment checks:

```bash
gcloud workflows describe "$WORKFLOW_NAME" --project "$PROJECT_ID" --location "$REGION"
gcloud scheduler jobs describe "$SCHEDULER_JOB" --project "$PROJECT_ID" --location "$REGION"
gcloud run jobs list --project "$PROJECT_ID" --region "$REGION"
```

Before enabling Scheduler, run one historical date manually in development and
follow `transit/plans/BACKFILL_RUNBOOK.md`.
