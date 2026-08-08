# Safe manual backfill runbook

Use this procedure after the workflow has been implemented and tested. It intentionally processes one UTC source date at a time.

## Rules

- Backfill only from existing raw snapshots. The realtime 511 API cannot recreate historical snapshots that were never collected.
- Never modify or delete `raw/` objects.
- Use the same workflow and code version as the daily pipeline.
- Confirm no workflow is already processing the same date before starting.
- Validate each date before continuing to the next.

## Step 1: choose one missing date

Record the UTC source date and agencies. Remember that this date selects the ingestion folder, not necessarily a Pacific transit service day.

```text
source_date: YYYY-MM-DD
agencies: muni, bart
reason:
operator:
```

## Step 2: check raw input

Confirm that both folders contain `.pb` files for each requested agency:

```text
gs://<bucket>/raw/TripUpdates/<agency>/<date>/
gs://<bucket>/raw/VehiclePositions/<agency>/<date>/
```

Compare the counts and first/last timestamps with a nearby normal date. If a feed is absent, stop: the historical realtime snapshots cannot be recovered from the API. If the day is clearly partial, document that fact and obtain approval before loading it.

## Step 3: prevent overlap

Check Google Workflows and Cloud Run execution history for an active run of the same date. If the daily Scheduler could overlap, pause it temporarily. Do not start two backfills for the same date.

This manual check replaces a custom locking subsystem in the initial simple design.

## Step 4: invoke the workflow

Submit one request:

```json
{
  "source_date": "2026-06-23",
  "agencies": ["muni", "bart"]
}
```

Before confirming execution, verify the selected project, workflow, date, and agencies. Save the workflow execution name.

## Step 5: monitor stage order

For Muni, then BART, expect:

1. TripUpdates and VehiclePositions parsers run concurrently.
2. Join starts only after both parsers succeed.
3. BigQuery upsert starts only after join succeeds.

If a dependency starts after its prerequisite failed, stop further backfills and fix the workflow.

If Workflows times out while a Cloud Run Job may still be active, inspect that job before retrying. Do not immediately create an overlapping run.

## Step 6: validate the result

For each agency/date, verify:

- workflow and Cloud Run executions succeeded;
- parser input counts and output rows are nonzero;
- failed protobuf count is zero under the strict policy;
- join rows equal TripUpdates rows;
- joined output is under the agency-specific date path;
- BigQuery has expected rows for the agency/date;
- duplicate business-key count is zero;
- the unique staging table was removed after success, if configured.

Record the counts and result next to the backfill request.

## Step 7: handle failure

- Leave raw data untouched.
- Use the workflow and Cloud Run execution logs to find the failed stage.
- Correct code/config and deploy an immutable new image if necessary.
- Confirm the earlier Cloud Run execution is terminal.
- Run the same date again through the workflow.
- Do not manually append parquet directly to BigQuery.

A failed unique staging table may be inspected and then removed by exact name. Never delete all staging tables or a broad GCS prefix.

## Step 8: continue or finish

Only after validation should you submit the next date. Process dates sequentially, oldest to newest. When finished, re-enable Scheduler if it was paused and confirm the next daily run succeeds.

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

