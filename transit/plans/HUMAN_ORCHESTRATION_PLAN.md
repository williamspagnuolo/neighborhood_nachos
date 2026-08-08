# Human-readable plan: simple daily transit orchestration

## Goal

Use Google Workflows to run the existing bucket-to-BigQuery jobs automatically in the correct order every day. Use the same workflow to process an explicit missed date manually.

The simplest dependable design is one workflow and the four existing processing jobs. It does not add a new orchestration service or database.

## Working with a coding agent

The coding-agent plan is divided into numbered work packages. Ask the agent to complete one package at a time. After each package, it should update `IMPLEMENTATION_LOG.md`, explain the result at a high level, list the checks it ran, and give you safe commands or procedures with expected results so you can verify the work. Review that handoff before requesting the next package. `CODING_AGENT_WORKFLOW.md` defines the full protocol.

## Current jobs and their order

For each agency and date:

1. `parse_tripupdates_day_to_parquet.py` reads raw TripUpdates protobufs and writes parquet.
2. `parse_vehiclepositions_day_to_parquet.py` reads raw VehiclePositions protobufs and writes parquet.
3. `join_tripupdates_vehiclepositions_day_to_parquet.py` joins those parquet outputs.
4. `upsert_joined_day_to_bigquery.py` stages and merges the joined rows into BigQuery.

Jobs 1 and 2 can run together. Job 3 waits for both, and job 4 waits for job 3. Process Muni first and BART second initially; this is slower than full parallelism but much easier to operate safely.

The minute-level `/poll` ingestion service remains scheduled separately. `upsert_stops_to_bigquery.py` is also separate and can run manually or on a simple weekly schedule.

## Step 1: settle three required decisions

Before coding, confirm:

1. The production project, region, bucket, BigQuery dataset/table, and BigQuery location. Repository examples currently disagree.
2. That the workflow date means the UTC raw-data folder date. The existing ingestion code creates folders in UTC even though the transit system is local.
3. The columns that uniquely identify a joined row. Current parser, join, and BigQuery merge keys do not match.

These decisions prevent writing to the wrong resource or creating duplicate/incorrect trip rows.

## Step 2: secure the API keys

Live-looking 511 keys appear in configuration files. Rotate them, remove populated values from tracked files/history as appropriate, and attach replacements through Secret Manager. Do not pass keys through workflow arguments or print them.

The daily transform jobs do not call the 511 realtime API, so the workflow itself needs no API key.

## Step 3: make each Cloud Run Job self-contained

The current environment YAML uses names such as `PARSE_BUCKET` and `JOIN_AGENCY`, but the Python scripts do not read many of them. Update each script/job definition so:

- stable values such as bucket, prefixes, dataset, table, location, and shard count live in the Cloud Run Job configuration;
- `TRANSIT_SOURCE_DATE` and `TRANSIT_AGENCY` can be overridden for each execution;
- explicit CLI flags still work locally;
- missing required configuration produces a clear nonzero failure.

Deploy four named jobs from the shared image: TripUpdates parser, VehiclePositions parser, join, and BigQuery upsert.

## Step 4: fix paths before running both agencies

Use these deterministic paths:

```text
latest/TripUpdates/<agency>/<date>/
latest/VehiclePositions/<agency>/<date>/
latest/joined/<agency>/<date>/
```

The current joined output omits agency, so BART and Muni could overwrite or mix data. Update the join writer and BigQuery reader together.

Ensure a rerun replaces the complete derived output for the exact stage/agency/date. Either always write the same full set of shards or clear only that exact derived prefix immediately before rewriting it. Never remove anything under `raw/`.

## Step 5: make the BigQuery merge safe to repeat

Align parser deduplication, join, and BigQuery `MERGE` on the approved row key. Before merging, fail if staging contains duplicate keys.

Change the staging table from the shared `<target>__stage` name to a unique name for each Cloud Run execution. Delete that exact table after a successful merge. This is a small safeguard against accidental overlap and does not require a separate lock system.

Run the same historical date twice in development. The second run must leave the same logical rows and zero duplicate business keys.

## Step 6: make failures visible to Workflows

The parsers currently catch individual protobuf errors and may still complete successfully. For the first release, make any protobuf parse failure fail the job after it prints the failed count and sample object names. Also fail on missing inputs, zero output rows, missing required columns, or duplicate keys.

Have each job print a compact JSON summary containing agency, date, input count, output rows/shards, and failed count. Cloud Logging plus Workflow/Cloud Run execution history is sufficient for the initial version; a custom run ledger and manifest system are unnecessary.

## Step 7: create the workflow YAML

Add a version-controlled file such as `transit/orchestration/workflow.yaml`.

The workflow should:

1. Accept optional `source_date` and an agency list.
2. Default the scheduled run to yesterday in UTC.
3. Validate a real historical `YYYY-MM-DD` date and allow only `muni`/`bart`.
4. Loop through agencies sequentially.
5. For each agency, launch the two parser jobs in parallel and wait for both.
6. Launch join only after both parsers succeed.
7. Launch BigQuery upsert only after join succeeds.
8. Return the Cloud Run execution results; let an unhandled job failure fail the workflow.

Use `googleapis.run.v2.projects.locations.jobs.run`. Set `connector_params.timeout` based on measured job duration. Do not add complex retry logic initially. The jobs are repeatable, and a failed workflow can be investigated and rerun explicitly.

## Step 8: configure Google Cloud

1. Enable Workflows, Cloud Run, Cloud Scheduler, Cloud Storage, BigQuery, Secret Manager, Artifact Registry/Cloud Build, and Logging APIs as needed.
2. Create a workflow service account that can execute only the four jobs, including override permission if required.
3. Give parser/join job identities the necessary bucket read/write access.
4. Give the loader identity joined-bucket read and target BigQuery permissions.
5. Build and deploy an immutable image version, then deploy the four job definitions.
6. Deploy the workflow in the same region as the jobs when practical.
7. Create one daily Scheduler trigger after the prior UTC day has closed, using UTC timezone.

The Scheduler account only needs permission to invoke the workflow.

## Step 9: test in development

Use a development prefix/dataset and test:

- one Muni date;
- one BART date;
- a missing input folder;
- one parser failure, confirming join/load do not run;
- the same successful date twice, confirming no BigQuery duplicates;
- a manual backfill while no scheduled workflow is active.

Check parser counts, join row count, vehicle match rate, BigQuery rows, and duplicate keys. Detailed cases are in `TEST_AND_ACCEPTANCE_PLAN.md`.

## Step 10: enable the daily schedule

After the tests pass, enable Scheduler. For the first several runs, check the Workflow execution and resulting BigQuery date coverage manually. Add one basic alert for workflow failure or, at minimum, a documented daily status check. More dashboards and anomaly detection can be added later if operations show they are useful.

## Step 11: backfill missed dates

Use `BACKFILL_RUNBOOK.md`. The short version is:

1. Confirm both raw feed folders exist for the agency/date.
2. Confirm no workflow is currently processing that date; pause Scheduler if necessary.
3. Invoke the workflow with one explicit UTC date.
4. Wait for completion and validate BigQuery rows/duplicates.
5. Continue to the next date only after the current date succeeds.

## Definition of done

- Scheduler invokes one workflow daily.
- The workflow enforces parser-parallel, join-next, load-last ordering.
- It processes both Muni and BART without sharing derived paths.
- A failed stage prevents dependent stages.
- Rerunning one date produces no duplicate logical BigQuery rows.
- A missed date can be run manually with the same workflow.
- API keys are no longer stored in tracked configuration.
- Setup, test, and backfill instructions have been verified by another person.
