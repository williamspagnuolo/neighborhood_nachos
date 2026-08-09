# Safe manual backfill runbook

Use this procedure after the workflow has been implemented and tested. It intentionally processes one UTC source date at a time.

## Rules

- Backfill only from existing raw snapshots. The realtime 511 API cannot recreate historical snapshots that were never collected.
- Never modify or delete `raw/` objects.
- Use the same workflow and code version as the daily pipeline.
- Confirm no workflow is already processing the same date before starting.
- Validate each date before continuing to the next.

## Step 1: choose one missing date

Record the UTC source date and agency. Remember that this date selects the ingestion folder, not necessarily a Pacific transit service day.

```text
source_date: YYYY-MM-DD
agencies: muni
reason:
operator:
```

Set these values in the terminal. `SOURCE_DATE` must be one explicit past UTC
raw-folder date. Do not substitute a date range or a shell loop.

```bash
export PROJECT_ID="neighboorhood-nachos"
export REGION="us-central1"
export BUCKET="511_transit_data"
export WORKFLOW_NAME="transit-daily"
export DATASET="neighborhood_livability_data"
export TARGET_TABLE="trip_stops"
export BQ_LOCATION="us-central1"
export SOURCE_DATE="2026-06-23"
```

Before continuing, check that the values select the intended development or
production environment. In particular, `SOURCE_DATE` is a UTC snapshot-folder
date, not a Pacific transit service date.

## Step 2: check raw input

Confirm that both Muni feed folders contain `.pb` files. The following two
**read-only** commands must each return one or more object
names; a no-match error is a reason to stop.

```text
gs://<bucket>/raw/TripUpdates/<agency>/<date>/
gs://<bucket>/raw/VehiclePositions/<agency>/<date>/
```

```bash
gcloud storage ls "gs://$BUCKET/raw/TripUpdates/muni/$SOURCE_DATE/*.pb"
gcloud storage ls "gs://$BUCKET/raw/VehiclePositions/muni/$SOURCE_DATE/*.pb"
```

Compare the counts and first/last timestamps with a nearby normal date. If a feed is absent, stop: the historical realtime snapshots cannot be recovered from the API. If the day is clearly partial, document that fact and obtain approval before loading it.

## Step 3: prevent overlap

First list every active execution of this workflow. This is a **read-only**
command. Inspect the `ARGUMENT` column: do not continue if any active argument
contains the same `source_date` (including an empty argument, which means the
workflow will use its default date and must be investigated).

```bash
gcloud workflows executions list "$WORKFLOW_NAME" \
  --project "$PROJECT_ID" \
  --location "$REGION" \
  --filter='state=ACTIVE' \
  --format='table(name,state,startTime,argument)'
```

Also inspect the four Cloud Run Job execution histories for a running job that
belongs to the same date. Do not start a backfill until any same-date job is
terminal.

```bash
gcloud run jobs executions list --job tripupdates-parse-day --project "$PROJECT_ID" --region "$REGION"
gcloud run jobs executions list --job vehiclepositions-parse-day --project "$PROJECT_ID" --region "$REGION"
gcloud run jobs executions list --job tripupdates-vp-join-day --project "$PROJECT_ID" --region "$REGION"
gcloud run jobs executions list --job joined-upsert-bigquery-day --project "$PROJECT_ID" --region "$REGION"
```

If the daily Scheduler could overlap, pause it temporarily. Do not start two backfills for the same date.

This manual check replaces a custom locking subsystem in the initial simple design.

## Step 4: invoke the workflow

This is a **Workflow execution mutation**. It starts the same `transit-daily`
workflow used by the daily path for exactly one UTC date and Muni only.
Run it only after the previous checks pass:

```bash
EXECUTION_NAME="$(gcloud workflows execute "$WORKFLOW_NAME" \
  --project "$PROJECT_ID" \
  --location "$REGION" \
  --data "{\"source_date\":\"$SOURCE_DATE\",\"agencies\":[\"muni\"]}" \
  --format='value(name)')"
printf '%s\n' "$EXECUTION_NAME"
```

The expected result is one non-empty execution resource name. Save it with the
backfill record. If it is empty or the command fails, do not try a second
execution until the failure is understood.

## Step 5: monitor stage order

For Muni, expect:

1. TripUpdates and VehiclePositions parsers run concurrently.
2. Join starts only after both parsers succeed.
3. BigQuery upsert starts only after join succeeds.

If a dependency starts after its prerequisite failed, stop further backfills and fix the workflow.

If Workflows times out while a Cloud Run Job may still be active, inspect that job before retrying. Do not immediately create an overlapping run.

Wait for this exact workflow execution to reach a terminal state. This command
does not start another execution:

```bash
gcloud workflows executions wait "$EXECUTION_NAME" \
  --project "$PROJECT_ID" \
  --location "$REGION" \
  --workflow "$WORKFLOW_NAME"
```

Expected result: the command returns only after the execution reaches a
terminal state. Inspect the exact execution and continue only when `state` is
`SUCCEEDED`; a nonzero command result or any other state is a reason to stop:

```bash
gcloud workflows executions describe "$EXECUTION_NAME" \
  --project "$PROJECT_ID" \
  --location "$REGION" \
  --workflow "$WORKFLOW_NAME"
```

## Step 6: validate the result

For the Muni/date result, verify:

- workflow and Cloud Run executions succeeded;
- parser input counts and output rows are nonzero;
- failed protobuf count is zero under the strict policy;
- join rows equal TripUpdates rows;
- joined output is under the agency-specific date path;
- BigQuery has expected rows for the agency/date;
- duplicate business-key count is zero;
- the unique staging table was removed after success, if configured.

Record the counts and result next to the backfill request.

Use this **read-only BigQuery query** after the workflow succeeds. It returns
the row count and canonical-key duplicate-group count for Muni for the UTC date
selected by `latest_snapshot_ts`. Muni should have nonzero rows for a normal
complete date, and `duplicate_key_group_count` must
be zero. Investigate a legitimate no-service or partial-feed date rather than
assuming its expected count.

```bash
bq --project_id="$PROJECT_ID" query \
  --use_legacy_sql=false \
  --location="$BQ_LOCATION" \
  --parameter="source_date:DATE:$SOURCE_DATE" \
   "SELECT
     agency_id,
     SUM(key_count) AS row_count,
     COUNTIF(key_count > 1) AS duplicate_key_group_count
   FROM (
     SELECT
       agency_id,
       trip_id,
       trip_start_date,
       trip_start_time,
       direction_id,
       stop_sequence,
       COUNT(*) AS key_count
     FROM \`$PROJECT_ID.$DATASET.$TARGET_TABLE\`
     WHERE DATE(latest_snapshot_ts) = @source_date
       AND agency_id = 'muni'
     GROUP BY
       agency_id,
       trip_id,
       trip_start_date,
       trip_start_time,
       direction_id,
       stop_sequence
   )
   GROUP BY agency_id
   ORDER BY agency_id"
```

The query reports duplicate *groups* rather than the total number of duplicate
rows. Its expected value is zero. The loader itself also rejects duplicate
canonical keys in its staging table before merge.

## Step 7: handle failure

- Leave raw data untouched.
- Use the workflow and Cloud Run execution logs to find the failed stage.
- Correct code/config and deploy an immutable new image if necessary.
- Confirm the earlier Cloud Run execution is terminal.
- Run the same date again through the workflow.
- Do not manually append parquet directly to BigQuery.

A failed unique staging table may be inspected and then removed by exact name. Never delete all staging tables or a broad GCS prefix.

## Step 8: continue or finish

Only after validation should you submit the next date. Continue one date at a
time; oldest to newest remains easiest to audit. The loader's timestamp guard
prevents an older cross-boundary observation from replacing a newer target row
if dates must be processed out of order. When finished, re-enable Scheduler if
it was paused and confirm the next daily run succeeds.

## Rerunning a previously successful date

Reruns should be safe because derived data is completely replaced for the exact agency/date and BigQuery uses an idempotent merge. Still:

1. document why the rebuild is needed;
2. check no active same-date workflow exists;
3. run the workflow once;
4. compare before/after row and duplicate counts.

## Never do these things

- Do not call the realtime API expecting historical snapshots.
- Do not edit raw protobufs to make parsing pass.
- Do not recursively delete a bucket or broad prefix.
- Do not load a joined wildcard that spans agencies.
- Do not reuse another execution's BigQuery stage.
- Do not launch a large parallel date range for convenience.
