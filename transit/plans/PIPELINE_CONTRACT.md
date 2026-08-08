# Minimal transit pipeline contract

This is the implementation contract for the first production-ready version. Keep it small unless actual failures show that more machinery is needed.

## 1. Processing unit

- **Source date**: a UTC `YYYY-MM-DD` folder under `raw/<feed>/<agency>/`. The ingestion code creates these folders in UTC.
- **Agency**: `muni` or `bart`.
- One workflow execution processes one source date.
- The workflow processes Muni and BART sequentially. Within an agency, its two parsers run concurrently.

## 2. Dependency order

For each agency:

```text
parse TripUpdates ---------+
                           +-> join -> BigQuery upsert
parse VehiclePositions ----+
```

If either parser fails, the join and upsert must not run. If the join fails, the upsert must not run. A Cloud Run Job's nonzero exit must fail the workflow.

## 3. Workflow input

```json
{
  "source_date": "2026-06-23",
  "agencies": ["muni", "bart"]
}
```

- `source_date` is optional for the scheduled run; it defaults to yesterday in UTC.
- Manual backfills always provide `source_date` explicitly.
- Reject invalid dates, today/future dates, and agencies outside the allowlist.
- Do not accept a date range in the first version. Run one date at a time.

## 4. Job configuration

Keep stable configuration in each Cloud Run Job: bucket, prefixes, BigQuery dataset/table/location, shard count, and resource settings. Pass only date and agency at execution time.

Update the Python scripts to accept environment defaults such as `TRANSIT_SOURCE_DATE` and `TRANSIT_AGENCY`, while retaining CLI flags for local use. This avoids replacing the complete `conda run ... python <script>` argument list through Cloud Run overrides.

No API key may be committed, placed in workflow input, or printed. Use Secret Manager for the ingestion service and stops job.

## 5. Storage paths

Raw inputs remain unchanged:

```text
raw/TripUpdates/{agency}/{source_date}/*.pb
raw/VehiclePositions/{agency}/{source_date}/*.pb
```

Derived paths must include agency and date:

```text
latest/TripUpdates/{agency}/{source_date}/part-*.parquet
latest/VehiclePositions/{agency}/{source_date}/part-*.parquet
latest/joined/{agency}/{source_date}/part-*.parquet
```

The current joined path lacks agency and must be fixed before automating both agencies.

Reruns must not mix old and new shards. Choose one simple policy and test it:

- Prefer a fixed shard count and always overwrite the full expected set; or
- before writing, remove only the exact derived `{stage}/{agency}/{date}/` prefix after confirming no other execution is processing it.

Never delete raw input. Avoid broad bucket wildcards.

## 6. BigQuery load

- Confirm one approved business key and use it consistently for deduplication, join, and merge.
- Assert that staging has no duplicate merge keys.
- Give each execution a unique staging table name, for example using the Cloud Run execution ID. This small change prevents accidental stage truncation if two loads overlap.
- `MERGE` must be idempotent: rerunning identical input updates existing logical rows rather than inserting duplicates.
- Delete only the exact staging table after a successful merge. A failed stage can be removed manually after investigation.

## 7. Minimum validation

Each job must fail clearly when:

- its required input prefix has no files;
- parsing produces zero rows;
- a protobuf parse failure occurs under the initial strict policy;
- required columns are missing;
- join keys or merge keys are duplicated unexpectedly;
- BigQuery target schema is missing/incompatible.

The join should verify that a left join produces the same row count as TripUpdates input. Jobs should print structured counts to Cloud Logging. A separate manifest/audit subsystem is not required initially.

## 8. Backfill safety

- Invoke the same workflow used by the schedule.
- Provide one explicit historical UTC source date.
- Before starting, confirm raw inputs exist and no workflow is already processing that date.
- Run dates sequentially.
- Verify BigQuery row and duplicate counts after each date.
- Pause the Scheduler only when it could overlap the date/run being backfilled.

## 9. Google Cloud pieces

- Cloud Scheduler invokes the workflow once daily after the prior UTC folder is complete.
- Workflows calls Cloud Run Jobs using `googleapis.run.v2.projects.locations.jobs.run`, which waits for job completion.
- Set the connector timeout longer than the measured maximum parser runtime if it exceeds the 30-minute default.
- The workflow service account can execute only the required jobs. Job service accounts receive only their necessary GCS/BigQuery access.

Official references: [Cloud Run v2 Workflows connector](https://cloud.google.com/workflows/docs/reference/googleapis/run/v2/projects.locations.jobs/run), [parallel steps](https://cloud.google.com/workflows/docs/execute-parallel-steps), and [scheduling workflows](https://cloud.google.com/workflows/docs/schedule-workflow).

