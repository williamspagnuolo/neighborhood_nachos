# AI coding-agent implementation plan

## Objective

Implement one lightweight Google Workflow that runs the existing daily transit jobs in dependency order and accepts one explicit date for backfills.

Do not introduce a custom orchestrator, lock service, run database, manifest framework, date-range engine, or generalized retry library. Add complexity only when it directly fixes a demonstrated correctness or safety problem.

Do not deploy, alter production IAM, rotate secrets, enable Scheduler, delete GCS objects, or mutate production BigQuery without explicit authorization.

## How to execute this plan

Treat each numbered work package below as one user-requested step. Follow `CODING_AGENT_WORKFLOW.md` for every package and maintain `IMPLEMENTATION_LOG.md` as the durable record of changes, tests, decisions, and deviations.

Unless the user explicitly combines packages:

1. complete only the requested package;
2. run its focused verification;
3. update the implementation log;
4. provide the required chat handoff and user-runnable tests;
5. stop and wait before starting the next package.

Packages are intended to run in numerical order. Package 1 records decisions needed by later work. Package 9 consolidates tests added alongside earlier packages rather than postponing all testing until the end.

## Required behavior

For each agency, run:

```text
TripUpdates parser ---------+
                            +-> join -> BigQuery upsert
VehiclePositions parser ----+
```

Process agencies sequentially (`muni`, then `bart`). Use the same workflow for daily and manual one-date runs.

## Work package 1: confirm configuration and row identity

Before implementation, confirm with the user or read-only deployed-resource inspection:

- GCP project and Cloud Run region;
- GCS bucket and prefixes;
- BigQuery dataset, table, and location;
- that input dates represent UTC raw folders;
- the canonical business key for dedupe/join/merge.

The repository currently contains conflicting example datasets/locations and inconsistent keys. Do not silently choose production values.

## Work package 2: remove credential exposure

1. Replace populated tracked API-key values with safe placeholders.
2. Update instructions to attach keys from Secret Manager to the ingestion service and stops job.
3. Ensure secrets are not passed through workflow arguments or printed.
4. Report that actual key rotation/history cleanup requires human authorization/action.

Keep this scoped; a full security platform is not required.

## Work package 3: standardize job inputs

Refactor the four daily scripts so:

- `--service-date` remains available for compatibility/local use;
- its default can come from `TRANSIT_SOURCE_DATE` and otherwise yesterday UTC;
- `--agency` can default from `TRANSIT_AGENCY`;
- agency/date validation is shared or kept in a very small helper;
- stable bucket/prefix/BigQuery settings are provided in the Cloud Run Job definition;
- a missing required value fails before writes.

Update `jobs/*.env.yaml`, `deploy_job.sh`, and `jobs/README.md` so the deployed command actually satisfies argparse. Avoid building a large configuration abstraction.

## Work package 4: fix agency paths and reruns

Change joined output and loader input to:

```text
latest/joined/{agency}/{source_date}/part-*.parquet
```

Keep existing agency/date parser paths.

Prevent stale derived shards on rerun using the smallest suitable change:

- use a fixed known shard set and overwrite all of it; or
- add a helper that clears only the exact derived stage/agency/date prefix immediately before writing.

If implementing deletion, require a validated agency/date and assert the prefix is under an allowed derived root. Never delete `raw/`, a bucket root, or a wildcard broader than one stage/agency/date.

## Work package 5: align keys and make failures real

1. Apply the approved key consistently to parser dedupe, join, and BigQuery merge.
2. Fail when join/staging contains duplicate keys.
3. Make parser blob failures produce nonzero job exit under the initial strict policy.
4. Keep existing useful count JSON, adding agency/date where missing.
5. Fail on missing input, zero output, or required-column mismatch.

Do not build a separate data-quality service. Direct assertions inside the relevant jobs are enough.

## Work package 6: make BigQuery staging collision-safe

1. Build a staging table name from the target plus sanitized Cloud Run execution ID (and agency if useful).
2. Load only `latest/joined/{agency}/{source_date}/part-*.parquet`.
3. Check nonzero rows and unique merge keys.
4. Run the idempotent merge.
5. Drop only that exact staging table after success when configured.

Retaining a failed staging table for manual inspection is acceptable. No automated cleanup job is required initially.

## Work package 7: create the workflow

Add `transit/orchestration/workflow.yaml`.

### Inputs

```json
{
  "source_date": "2026-06-23",
  "agencies": ["muni", "bart"]
}
```

- Default date to yesterday UTC when omitted.
- Validate date and agency allowlist.
- Reject today/future dates.

### Execution

1. Loop through agencies sequentially.
2. For each agency, run the two parsers in named parallel branches.
3. After both succeed, run join.
4. After join succeeds, run BigQuery upsert.
5. Return compact execution results.

Use `googleapis.run.v2.projects.locations.jobs.run` and environment overrides for agency/date. Set `connector_params.timeout` if measured runs exceed the default. Let connector/job failures propagate naturally; do not add broad retries or elaborate exception state handling in the first version.

Validate workflow syntax in a development project.

## Work package 8: deployment and IAM documentation

Document or implement small idempotent helpers for:

- building one immutable shared job image;
- deploying/updating four Cloud Run Job definitions;
- deploying the workflow;
- creating one daily Scheduler job after the prior UTC folder closes.

Use least-privilege service accounts:

- workflow: execute the four jobs;
- parser/join: required GCS read/write;
- loader: joined GCS read plus target BigQuery load/query/table permissions;
- Scheduler: invoke the workflow.

Prefer placeholders/config variables over hardcoded example resources. Do not add Terraform unless the user asks for infrastructure as code; small documented `gcloud` commands are sufficient for this project.

## Work package 9: tests

Add focused tests for:

- environment/CLI date and agency behavior;
- path construction for both agencies;
- canonical key uniqueness and duplicate rejection;
- parser failure on a corrupt protobuf;
- join row-count preservation;
- unique staging name and merge SQL;
- rerunning identical data without duplicate target rows;
- workflow failure propagation in a development deployment.

Use existing libraries and simple fixtures. Avoid a broad mocking framework if direct unit tests suffice.

## Work package 10: backfill support

Document one command that invokes the same workflow with an explicit date. The operator procedure must:

1. verify both raw prefixes;
2. check no same-date workflow is active;
3. run one date;
4. wait and validate BigQuery;
5. continue sequentially.

Do not implement a multi-day workflow initially. A short shell loop can be added later only after one-date operation is proven, with the operator still reviewing each result.

## Acceptance checklist

- Four existing jobs, one workflow, and one daily Scheduler are sufficient for the daily path.
- Both parsers run concurrently; dependencies stop on failure.
- Agencies never share joined paths.
- Staging tables do not collide.
- Identical reruns do not create duplicate BigQuery rows.
- Backfill uses the same workflow and one explicit UTC date.
- No live secret remains in tracked config.
- Tests and the manual runbook pass in development.

## Final project handoff

After package 10 and the acceptance checks are complete, provide a final summary using the accumulated `IMPLEMENTATION_LOG.md`. Report changed files, tests/results, unresolved configuration/key decisions, and exact external steps still required. Include a simple rollback: pause Scheduler and redeploy the previous workflow/image revision. Never delete raw data as rollback.

