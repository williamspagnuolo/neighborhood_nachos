# Interactive SF Dashboard

Plotly Dash app for exploring San Francisco livability metrics by boundary:

- `neighborhoods`
- `police_districts`

Click a polygon to load metrics for that boundary and a Pacific-time date window.

The app has two tabs:

1. **Dashboard** — the map + KPI + histogram view described below.
2. **Ask a question** — a Gemini-powered text-to-SQL agent that answers
   plain-English questions against the curated
   `neighborhood_livability_gold` dataset. Every generated query is shown
   to the user with a dry-run byte estimate before it runs. See
   [Text-to-SQL agent](#text-to-sql-agent) below.

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

- `DASH_BQ_DATASET` (default `neighborhood_livability_gold`)
- `DASH_BQ_LOCATION` (default `us-central1`)
- `DASH_BQ_TABLE_DASHBOARD_EVENTS` (default `gold_dashboard_events`)
- `DASH_BQ_TABLE_BOUNDARIES` (default `gold_boundaries`)
- `DASH_BQ_TABLE_RENTAL_LISTINGS` (default `gold_rental_listings`)
- `DASH_CACHE_TTL_SECONDS` (default `300`)
- `DASH_CACHE_MAX_ENTRIES` (default `512`)

Text-to-SQL agent overrides:

- `DASH_AGENT_ENABLED` (default `true`, set to `false` to hide the tab)
- `DASH_AGENT_DATASET` (default `neighborhood_livability_gold`)
- `DASH_AGENT_SEMANTICS_FILE` (default: `dashboard/app/gold_semantics.md`
  inside the container). Optional absolute path override for the
  analyst-editable markdown file the agent folds into its system prompt.
- `DASH_LLM_PROJECT` (default same as `DASH_BQ_PROJECT`)
- `DASH_LLM_LOCATION` (default `us-central1`) — Vertex AI region
- `DASH_LLM_MODEL` (default `gemini-2.5-flash`)
- `DASH_LLM_MAX_BYTES_BILLED` (default `5368709120`, i.e. 5 GB)
- `DASH_LLM_ROW_LIMIT` (default `1000`) — outer LIMIT applied to any
  generated query that doesn't already specify one
- `DASH_LLM_TIMEOUT_SECONDS` (default `30`)

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
  bigquery.googleapis.com \
  aiplatform.googleapis.com

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

# Text-to-SQL agent: allow the runtime SA to call Vertex AI Gemini.
# Only needed if you plan to enable the "Ask a question" tab.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
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
  --set-env-vars "DASH_BQ_PROJECT=${PROJECT_ID},DASH_BQ_DATASET=neighborhood_livability_data,DASH_BQ_LOCATION=US,DASH_AGENT_DATASET=neighborhood_livability_gold,DASH_LLM_LOCATION=us-central1,DASH_LLM_MODEL=gemini-2.5-flash"
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

## Text-to-SQL agent

The **Ask a question** tab uses Vertex AI Gemini to translate a
plain-English question into a BigQuery `SELECT` against the
`neighborhood_livability_gold` dataset. See
[`docs/text_to_sql_agent_plan.md`](docs/text_to_sql_agent_plan.md) for the
full design.

Flow:

1. User types a question (e.g. "Which neighborhoods had the most 311
   requests in the last 7 days?").
2. **Generate SQL** — the agent introspects the gold dataset (schema,
   per-table row counts, dataset / table / column descriptions from BQ
   metadata, plus the `dashboard/app/gold_semantics.md` file), prompts
   Gemini, parses the returned JSON, validates the SQL, applies a row
   cap if needed, and runs a BigQuery dry-run to estimate bytes scanned.
3. The generated SQL and byte estimate are shown to the user, along with
   the model's own short explanation.
4. **Run query** — the validated SQL is executed with
   `maximum_bytes_billed=DASH_LLM_MAX_BYTES_BILLED` and results appear in
   a paginated table.

### How to improve the agent's answers

Two channels, both take effect on the next container restart — no
Python changes needed:

- **BigQuery metadata** (preferred for per-table / per-column facts):
  ```sql
  ALTER TABLE `neighboorhood-nachos.neighborhood_livability_gold.<table>`
  SET OPTIONS (description = 'One row per ...');

  ALTER TABLE `neighboorhood-nachos.neighborhood_livability_gold.<table>`
  ALTER COLUMN <col> SET OPTIONS (description = '...');
  ```
  The agent picks these up automatically via
  `INFORMATION_SCHEMA.COLUMN_FIELD_PATHS` and `TABLE_OPTIONS`.

- **`dashboard/app/gold_semantics.md`** (preferred for cross-table
  conventions, business definitions, and query patterns): edit this
  markdown file in a normal PR. It ships in the container image and gets
  folded verbatim into the agent's system prompt.

Guardrails:

- Only `SELECT` / `WITH` statements are accepted (validated with
  `sqlglot` plus a keyword sweep).
- If the model omits a `LIMIT`, the executor wraps the statement in
  `SELECT * FROM (…) LIMIT DASH_LLM_ROW_LIMIT`.
- Every query is dry-run first; queries projected to scan more than
  `DASH_LLM_MAX_BYTES_BILLED` are rejected before any real billing.
- Runtime SA only holds `roles/bigquery.dataViewer`, so even if a
  guardrail were bypassed BigQuery would refuse writes.

Local requirements:

- `pip install -r dashboard/app/requirements.txt` (already picks up
  `google-genai` and `sqlglot`).
- Vertex AI API enabled and `roles/aiplatform.user` on your local
  credentials (see IAM section above).

To hide the agent tab entirely (e.g. for a demo without Vertex access):

```bash
export DASH_AGENT_ENABLED=false
```

## Known limitations (MVP)

- No persistent external cache (in-process cache only).
- Transit join on `stop_id` only may include cross-agency collisions if duplicate stop IDs exist across agencies.
- Error handling is user-readable but not yet integrated with centralized monitoring.
- Text-to-SQL agent has no per-user rate limit; expensive-question guards
  rely on `DASH_LLM_MAX_BYTES_BILLED` and `DASH_LLM_ROW_LIMIT`.
- Text-to-SQL agent doesn't remember prior questions in a session — each
  ask is stateless.
