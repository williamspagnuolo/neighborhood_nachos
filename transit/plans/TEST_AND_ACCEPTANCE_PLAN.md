# Essential test and acceptance plan

The goal is to cover the failures most likely to corrupt or skip data without creating a large test platform.

## 1. Local checks

- Python files compile and pass the repository formatter/linter if configured.
- Workflow YAML parses and validates in a development GCP project.
- No tracked file contains a live 511 key or service-account credential.
- Each Cloud Run Job's exact deployed command can run `--help` and receives its required static configuration.

## 2. Focused unit tests

### Inputs and paths

- An environment date/agency is used when the CLI flag is absent.
- An explicit CLI value wins for local testing.
- Invalid dates, today/future dates, and unsupported agencies fail.
- Muni and BART joined paths are different and include the requested UTC date.
- A cleanup helper, if added, rejects raw roots, bucket roots, invalid agencies, and invalid dates.

### Transform correctness

- Small known protobuf fixtures parse to expected rows.
- A corrupt protobuf makes the job fail after reporting it.
- Empty input and zero parsed rows fail.
- Approved business keys are unique after each parser.
- Join output row count equals TripUpdates input row count.
- Duplicate join/merge keys fail with a useful error.

### BigQuery load

- Each execution creates a different valid staging-table name.
- The merge SQL uses the approved key and column mapping.
- Missing target columns fail before merge.
- Cleanup drops only the exact execution stage.

## 3. Development integration tests

Use a development bucket prefix and BigQuery dataset.

| Test | Expected result |
|---|---|
| One complete Muni date | Parsers run together, then join and load succeed. |
| BART workflow input | Input validation rejects it before starting a Cloud Run Job. |
| Missing raw feed folder | Parser fails and join/load do not start. |
| Corrupt parser input | Workflow fails and dependent stages do not start. |
| Join schema/key error | Loader does not start. |
| Identical date rerun | Logical target rows are unchanged and duplicate-key count stays zero. |
| Rerun with a different row/shard count fixture | No stale derived shard is loaded. |

For successful tests, compare:

- raw object counts;
- parsed rows and failures;
- join input/output rows and vehicle match rate;
- BigQuery rows for agency/date;
- duplicate business-key count.

## 4. Backfill rehearsal

Follow `BACKFILL_RUNBOOK.md` for two dates:

1. verify raw inputs;
2. run one explicit date;
3. validate results;
4. run the same date again to prove idempotency;
5. run the next date only after the first is complete.

Also confirm the operator can see whether another workflow is active before starting.

## 5. Scheduler canary

Run the daily schedule in development for several days and confirm:

- it selects the previous UTC date, including a month boundary;
- it invokes the workflow once at the intended time;
- failures are visible in Workflow/Cloud Run history;
- the resulting BigQuery date/agency coverage is present.

## Production acceptance

- All “must fix” issues in `RISKS_OPEN_QUESTIONS.md` are resolved.
- Credentials are rotated and stored outside tracked files.
- Resource identifiers, location, target schema, and business key are approved.
- Happy-path, failure-ordering, stale-shard, and rerun tests pass for Muni.
- The manual backfill rehearsal passes.
- The team knows how to pause Scheduler and inspect workflow/job logs.

## Simple rollback

Pause Scheduler, let any active job reach a known terminal state, and redeploy the last known workflow/image revision if needed. Preserve raw data. Inspect BigQuery before resuming; do not use broad GCS deletion or table replacement as rollback.
