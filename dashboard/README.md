# Interactive SF Dashboard

Plotly Dash app for exploring San Francisco livability metrics by boundary:

- `neighborhoods`
- `police_districts`

Click a polygon to load metrics for that boundary and a Pacific-time date window.

## Technical design note

This dashboard intentionally follows the locked MVP contract from the planning docs:

1. **Time contract**
   - UI date controls are Pacific (`America/Los_Angeles`).
   - Query filtering runs in UTC.
   - End date is treated as inclusive in UI and converted to an exclusive UTC upper bound (`next_day_midnight_pt`).
2. **Boundary contract**
   - Aggregation membership is ID-based only (`neighborhood_id` / `police_district_id`).
   - Null boundary IDs are excluded from boundary-level aggregates.
   - No point-in-polygon aggregation is performed in app callbacks.
3. **Geometry contract**
   - Geometry is used only for rendering/click interactions.
   - WKT conversion is performed in BigQuery via `SAFE.ST_GEOGFROMTEXT` + `ST_ASGEOJSON`.
4. **Transit metric contract**
   - Transit delay KPIs use `arrival_delay_sec` from GTFS-RT TripUpdates.
   - Primary KPI is median delay, displayed in minutes with early/late direction.
   - Secondary KPI is percent of delays over 5 minutes (`arrival_delay_sec > 300`).
   - Transit aggregation joins `trip_stops` to `stops` on `stop_id`.

## App structure

- `dashboard/app/app.py` - Dash layout + callbacks + interaction flow
- `dashboard/app/config.py` - environment-driven runtime config
- `dashboard/app/queries.py` - parameterized BigQuery query layer
- `dashboard/app/boundaries.py` - boundary feature loading and ID/name mapping
- `dashboard/app/figures.py` - Plotly map/histogram builders
- `dashboard/app/time_utils.py` - Pacific UI to UTC SQL time helpers
- `dashboard/app/cache.py` - in-memory TTL cache for metrics

## Required environment variables

At minimum:

- `DASH_BQ_PROJECT` (or `GOOGLE_CLOUD_PROJECT`)

Optional overrides:

- `DASH_BQ_DATASET` (default `neighborhood_livability_data`)
- `DASH_BQ_LOCATION` (default `US`)
- `DASH_BQ_TABLE_311` (default `311_incidents`)
- `DASH_BQ_TABLE_POLICE` (default `police_incidents`)
- `DASH_BQ_TABLE_TRIP_STOPS` (default `trip_stops`)
- `DASH_BQ_TABLE_STOPS` (default `stops`)
- `DASH_BQ_TABLE_NEIGHBORHOODS` (default `neighborhoods`)
- `DASH_BQ_TABLE_POLICE_DISTRICTS` (default `police_districts`)
- `DASH_CACHE_TTL_SECONDS` (default `300`)
- `DASH_CACHE_MAX_ENTRIES` (default `512`)

You also need Google Cloud credentials available in your environment (for example `GOOGLE_APPLICATION_CREDENTIALS`).

## Run locally

From repo root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r dashboard/app/requirements.txt

# Persist vars in dashboard/.env, then load them into the shell
set -a
source dashboard/.env
set +a

python -m dashboard.app.app
```

Open [http://localhost:8050](http://localhost:8050).

## Deploy to Cloud Run

This dashboard can be deployed as a containerized web service on Cloud Run.

### 1) One-time setup

```bash
export PROJECT_ID="<your-project-id>"
export REGION="us-central1"
export REPO_NAME="dash-apps"
export SERVICE_NAME="sf-livability-dashboard"
export SA_NAME="dashboard-runner"

gcloud config set project "$PROJECT_ID"

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  bigquery.googleapis.com

gcloud artifacts repositories create "$REPO_NAME" \
  --repository-format=docker \
  --location="$REGION" \
  --description="Dashboard containers" || true
```

### 2) Runtime service account and IAM

```bash
gcloud iam service-accounts create "$SA_NAME" \
  --display-name "Dashboard runtime SA" || true

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/bigquery.jobUser"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataViewer"
```

### 3) Build and push image

From repo root:

```bash
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:$(date +%Y%m%d%H%M%S)"

gcloud builds submit "dashboard" --tag "$IMAGE"
```

### 4) Deploy Cloud Run service

```bash
gcloud run deploy "$SERVICE_NAME" \
  --image "$IMAGE" \
  --region "$REGION" \
  --platform managed \
  --service-account "${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --allow-unauthenticated \
  --port 8080 \
  --set-env-vars "DASH_BQ_PROJECT=${PROJECT_ID},DASH_BQ_DATASET=neighborhood_livability_data,DASH_BQ_LOCATION=US"
```

### 5) Verify

```bash
gcloud run services describe "$SERVICE_NAME" --region "$REGION" --format='value(status.url)'
gcloud run services logs read "$SERVICE_NAME" --region "$REGION" --limit=100
```

Notes:
- Cloud Run injects `PORT`; the dashboard container serves with `gunicorn`.
- Add more env overrides in `--set-env-vars` if your dataset/table names differ.
- If you do not want a public endpoint, remove `--allow-unauthenticated` and configure IAM access.

## Validation checklist

- Toggle mode resets selected boundary.
- No-selection state renders placeholders.
- Date range applies to 311, police, and transit queries.
- UTC query filtering with Pacific UI dates.
- Null boundary IDs excluded from boundary aggregates.
- Transit join on `trip_stops.stop_id = stops.stop_id`.

## Known limitations (MVP)

- No persistent external cache (in-process cache only).
- Transit join on `stop_id` only may include cross-agency collisions if duplicate stop IDs exist across agencies.
- Error handling is user-readable but not yet integrated with centralized monitoring.
