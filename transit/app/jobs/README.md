# Transit Runtime Layout

This folder keeps job-specific runtime config in one place. The minute-level
ingestion path is a Cloud Run service built from `Dockerfile.service`; the daily
transformations and stops loader are Cloud Run Jobs built from `Dockerfile`.

## Files

- `ingest.env.yaml`: non-secret settings and safe placeholders for minute-level ingest
- `parse_tripupdates.env.yaml`: env vars for TripUpdates parse (`parse_tripupdates_day_to_parquet.py`)
- `parse_vehiclepositions.env.yaml`: env vars for VehiclePositions latest parse (`parse_vehiclepositions_day_to_parquet.py`)
- `join_tripupdates_vehiclepositions.env.yaml`: env vars for daily join job (`join_tripupdates_vehiclepositions_day_to_parquet.py`)
- `upsert_joined_to_bigquery.env.yaml`: env vars for daily BigQuery upsert (`upsert_joined_day_to_bigquery.py`)
- `upsert_stops_to_bigquery.env.yaml`: env vars for stops dimension upsert (`upsert_stops_to_bigquery.py`)
- `deploy_job.sh`: helper to create/update a Cloud Run Job for a specific script
- `run_job.sh`: helper to execute a Cloud Run Job with optional CLI args override

## Recommended Pattern

1. Build one immutable image from `transit/app` for the four daily jobs.
2. Create/update each job with its script-specific env YAML; no static CLI args
   are required.
3. Override only `TRANSIT_SOURCE_DATE` and `TRANSIT_AGENCY` for an execution.
4. Orchestrate the four jobs with Workflow after their focused tests pass.

## Daily job configuration

The four daily scripts use the same runtime inputs:

- `TRANSIT_SOURCE_DATE`: optional UTC raw-folder date; empty/unset means
  yesterday UTC. Today, future dates, and invalid dates fail before cloud access.
- `TRANSIT_AGENCY`: `muni` or `bart`; any other value fails before cloud access.

An explicit `--service-date` or `--agency` CLI flag takes precedence for local
use and backward compatibility. Bucket, prefix, shard, and BigQuery settings
are stable values in the job-specific YAML files. The future Workflow should
override only the two runtime environment variables.

Daily derived outputs use separate agency/date prefixes:

```text
latest/TripUpdates/{agency}/{source_date}/part-*.parquet
latest/VehiclePositions/{agency}/{source_date}/part-*.parquet
latest/joined/{agency}/{source_date}/part-*.parquet
```

Immediately before a deterministic daily rewrite, each producing job removes
only its exact validated derived stage/agency/date prefix. The guard accepts
only `muni`/`bart`, a real historical UTC date, and roots equal to or ending in
`latest/TripUpdates`, `latest/VehiclePositions`, or `latest/joined`. It rejects
raw roots, bucket roots, broad wildcards, and unsafe paths. This prevents stale
shards from an earlier attempt from being included in a rerun. It never deletes
anything under `raw/`.

Do not overlap executions for the same stage, agency, and source date. The
initial design relies on sequential agencies and the documented operator check
rather than adding a distributed lock service.

The approved logical row key is `agency_id`, `trip_id`, `trip_start_date`,
`trip_start_time`, `direction_id`, and `stop_sequence`; nullable key values use
null-safe equality. Both parsers, the join, and the BigQuery merge use this
same key. A daily parser fails if any raw protobuf blob fails to parse, and the
join and BigQuery loader fail if their input/staging data contains duplicate
keys or no output rows.

The joined-data loader creates an execution-specific BigQuery staging table:
`<target>__stage_<agency>_<sanitized-cloud-run-execution-id>`. This prevents one
load from truncating another load's stage. When
`TRANSIT_DROP_STAGING_AFTER_MERGE=true`, it drops only its own staging table
after a successful merge; failed stages remain available for inspection.

The self-contained deployed command is:

```text
conda run --no-capture-output -n env_transit python <script>
```

To smoke-test the same entrypoint locally without cloud access:

```bash
conda run --no-capture-output -n env_transit \
  python transit/app/parse_tripupdates_day_to_parquet.py --help
```

## API keys and Secret Manager

Never put a 511 API key in an env YAML, command-line argument, workflow input,
build substitution, log message, or committed file. Empty API-key entries in
`ingest.env.yaml` are placeholders only.

The active minute ingestion path is:

```text
Cloud Scheduler transit-minute-ingest-poll
  -> POST /poll on Cloud Run service transit-minute-ingest
     -> upload_transit_to_bucket.run_once_from_env()
```

It uses these Secret Manager mappings:

| Environment variable | Secret name |
|---|---|
| `TRIP_UPDATES_MUNI_API_KEYS` | `transit-trip-updates-muni-api-keys` |
| `TRIP_UPDATES_BART_API_KEYS` | `transit-trip-updates-bart-api-keys` |
| `VEHICLE_POSITIONS_MUNI_API_KEYS` | `transit-vehicle-positions-muni-api-keys` |
| `VEHICLE_POSITIONS_BART_API_KEYS` | `transit-vehicle-positions-bart-api-keys` |

To attach or repair those mappings, first ensure the service account has
`roles/secretmanager.secretAccessor` on only those secrets. Then, with explicit
authorization to update the deployed service, run:

```bash
export PROJECT_ID="neighboorhood-nachos"
export REGION="us-central1"
export TRANSIT_SECRET_VERSION="1"  # Use the approved enabled numeric version.

gcloud run services update transit-minute-ingest \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --update-secrets \
"TRIP_UPDATES_MUNI_API_KEYS=transit-trip-updates-muni-api-keys:${TRANSIT_SECRET_VERSION},TRIP_UPDATES_BART_API_KEYS=transit-trip-updates-bart-api-keys:${TRANSIT_SECRET_VERSION},VEHICLE_POSITIONS_MUNI_API_KEYS=transit-vehicle-positions-muni-api-keys:${TRANSIT_SECRET_VERSION},VEHICLE_POSITIONS_BART_API_KEYS=transit-vehicle-positions-bart-api-keys:${TRANSIT_SECRET_VERSION}"
```

The stops job reads `TRANSIT_511_API_KEY` (and accepts the legacy
`STOPS_LOCATION_API_KEY` fallback). The tracked env YAML deliberately contains
neither value. Attach the existing `stops-location-api-key` secret only after
confirming the job service account can access it:

```bash
export PROJECT_ID="neighboorhood-nachos"
export REGION="us-central1"
export STOP_SECRET_VERSION="1"  # Use the approved enabled numeric version.

STOP_JOB_SERVICE_ACCOUNT="$(gcloud run jobs describe stops-upsert-bigquery \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --format='value(spec.template.spec.template.spec.serviceAccountName)')"

# IAM mutation: run only with explicit authorization.
gcloud secrets add-iam-policy-binding stops-location-api-key \
  --project "$PROJECT_ID" \
  --member "serviceAccount:${STOP_JOB_SERVICE_ACCOUNT}" \
  --role roles/secretmanager.secretAccessor

# Cloud Run Job mutation: run only with explicit authorization.
gcloud run jobs update stops-upsert-bigquery \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --remove-env-vars TRANSIT_511_API_KEY,STOPS_LOCATION_API_KEY \
  --update-secrets "TRANSIT_511_API_KEY=stops-location-api-key:${STOP_SECRET_VERSION}"
```

The paused legacy `transit-minute-job` still has literal API-key environment
values in its deployed revision. Do not resume or manually execute it until its
literal values have been removed and Secret Manager mappings attached, or until
the project owner separately authorizes retiring it. The active
`transit-minute-ingest` service does not depend on that job.

Removing a value from the current checkout does not remove it from Git history
or revoke it at 511. An authorized owner must confirm the exposed keys were
rotated. Coordinate any history rewrite with all repository collaborators; it
is not a substitute for rotation.

## Daily job deployment example

```bash
# Build one shared image for all transit jobs
export PROJECT_ID="neighboorhood-nachos"
export IMAGE="gcr.io/$PROJECT_ID/transit-jobs:$(date +%Y%m%d%H%M%S)"
gcloud builds submit "transit/app" --project "$PROJECT_ID" --tag "$IMAGE"

# Deploy TripUpdates parser
bash transit/app/jobs/deploy_job.sh \
  --project "$PROJECT_ID" \
  --region "us-central1" \
  --job "tripupdates-parse-day" \
  --image "$IMAGE" \
  --script "parse_tripupdates_day_to_parquet.py" \
  --env-file "transit/app/jobs/parse_tripupdates.env.yaml"

# Deploy VehiclePositions parser
bash transit/app/jobs/deploy_job.sh \
  --project "$PROJECT_ID" \
  --region "us-central1" \
  --job "vehiclepositions-parse-day" \
  --image "$IMAGE" \
  --script "parse_vehiclepositions_day_to_parquet.py" \
  --env-file "transit/app/jobs/parse_vehiclepositions.env.yaml"

# Deploy daily join
bash transit/app/jobs/deploy_job.sh \
  --project "$PROJECT_ID" \
  --region "us-central1" \
  --job "tripupdates-vp-join-day" \
  --image "$IMAGE" \
  --script "join_tripupdates_vehiclepositions_day_to_parquet.py" \
  --env-file "transit/app/jobs/join_tripupdates_vehiclepositions.env.yaml"

# Deploy daily BigQuery upsert
bash transit/app/jobs/deploy_job.sh \
  --project "$PROJECT_ID" \
  --region "us-central1" \
  --job "joined-upsert-bigquery-day" \
  --image "$IMAGE" \
  --script "upsert_joined_day_to_bigquery.py" \
  --env-file "transit/app/jobs/upsert_joined_to_bigquery.env.yaml"

# No deployment was performed by the coding agent. Review project, region,
# image, service accounts, and env YAML before running these mutation commands.
```

## Stops job deployment example

The stops job is separate from the daily workflow:

```bash
export PROJECT_ID="neighboorhood-nachos"
export IMAGE="gcr.io/$PROJECT_ID/transit-jobs:<immutable-tag>"

# Deploy stops dimension upsert (511 Stops API -> BigQuery)
bash transit/app/jobs/deploy_job.sh \
  --project "$PROJECT_ID" \
  --region "us-central1" \
  --job "stops-upsert-bigquery" \
  --image "$IMAGE" \
  --script "upsert_stops_to_bigquery.py" \
  --env-file "transit/app/jobs/upsert_stops_to_bigquery.env.yaml" \
  --args-csv "--agencies=muni:SF,bart:BA,--bq-project=neighboorhood-nachos,--bq-dataset=neighborhood_livability_data,--bq-table=stops,--bq-location=us-central1,--neighborhoods-table=neighborhoods,--police-districts-table=police_districts"

# After deploy, attach TRANSIT_511_API_KEY from Secret Manager as shown above.
```

If a later job update uses `--env-vars-file`, verify the secret mapping after
the update and reattach it if necessary. Never populate the YAML as a shortcut.
