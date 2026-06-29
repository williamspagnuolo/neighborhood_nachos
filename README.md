# Neighborhood Nachos

A multi-source GCP data pipeline that ingests San Francisco civic datasets
into BigQuery for neighborhood-livability analytics.

Each data source lives in its own top-level subfolder with an independent
deployment. Shared infrastructure (project, dataset, IAM patterns) is listed
below.

## Repository layout

```
.
├── 311/          SF 311 service requests pipeline
├── police/       SFPD incident reports pipeline
├── rents/        Rental listings pipeline
├── .gitignore
└── README.md     (this file)
```

Each subproject is structured roughly as:

```
<source>/
├── app/                routine producing the source's raw data in GCS
│   ├── Dockerfile
│   ├── requirements.txt (or environment.yaml)
│   └── <source>_*.py
├── transform/          (optional) routine cleaning + loading to BigQuery
└── notebooks/          (optional) exploratory analysis
```

## Shared GCP infrastructure

| Resource | Identifier |
|---|---|
| GCP project | `neighboorhood-nachos` |
| Default region | `us-east1` |
| BigQuery dataset | `neighborhood_livability_data` |

All Cloud Run Jobs authenticate via attached service accounts and
Application Default Credentials. No service account key files are
committed to the repo; secrets live in Secret Manager and are mounted
as env vars at runtime.

## Subprojects

### `police/` — SFPD Incident Reports

Daily pull from the SF Socrata API (`data.sfgov.org/resource/wg3w-h783.json`)
into GCS, then transformation into BigQuery `police_incidents` with
post-load spatial enrichment against neighborhood and police-district
polygon dimension tables.

Two Cloud Run Jobs:

- **`police-report-pull`** — scheduled at 11:00 PT daily. Pulls a rolling
  7-day window of incidents from the Socrata API and writes one JSON file
  per Pacific date to `gs://police_report_data/raw/sfpd_reports/...`.
- **`police-report-transform`** — scheduled at 11:15 PT daily. For each date
  in the window: filters / re-keys fields, converts the GeoJSON `point` to
  WKT, writes NDJSON to `gs://police_report_data/police_report_transformed/...`,
  and idempotently rebuilds the BigQuery rows (`DELETE` + `WRITE_APPEND`
  keyed on Pacific `incident_date`). A post-load `MERGE` populates the
  `neighborhood_id` and `police_district_id` foreign keys via
  `ST_CONTAINS(polygon, point)` against the dim tables.

Both jobs are fully idempotent — re-running any date produces an identical
result, and the 7-day rolling window captures late-arriving SFPD reports
without manual intervention.

### `311/` — 311 Service Requests

SF 311 service request ingestion. See `311/` for details.

### `rents/` — Rental Listings

Rental listing ingestion. See `rents/` for details.

## Conventions

- **One subfolder per data source.** Don't add files at the repo root.
- **One Docker image per Cloud Run Job.** Multi-stage pipelines (like
  `police/`) get nested subfolders, each with its own Dockerfile.
- **`.env` and any local credential files stay gitignored.** Use Secret
  Manager for anything sensitive at deploy time, and ADC for local dev.
- **Feature branches + PRs to `main`.** No direct pushes to `main`.

## Schema Design

