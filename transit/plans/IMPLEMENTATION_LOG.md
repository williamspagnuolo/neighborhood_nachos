# Transit orchestration implementation log

This file is maintained by the coding agent during implementation. It records actual work and deviations without changing the original plan history.

Allowed statuses: `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, `COMPLETE`.

## Package status

| Package | Name | Status | Last updated | Notes |
|---|---|---|---|---|
| 1 | Confirm configuration and row identity | COMPLETE | 2026-08-07 | Deployed configuration, UTC date semantics, and the six-column canonical key with null-safe handling are confirmed. |
| 2 | Remove credential exposure | COMPLETE | 2026-08-07 | Tracked plaintext values removed, logging sanitized, and Secret Manager attachment documented; external rotation/deployed cleanup remains. |
| 3 | Standardize job inputs | COMPLETE | 2026-08-07 | Four daily jobs now use validated environment defaults, retain CLI overrides, and deploy with self-contained stable configuration. |
| 4 | Fix agency paths and reruns | COMPLETE | 2026-08-08 | Joined paths include agency; deterministic derived rewrites clear only an exact validated stage/agency/date prefix. |
| 5 | Align keys and make failures real | COMPLETE | 2026-08-08 | The approved six-column key is shared across parsers, join, and merge; duplicate, zero-output, missing-column, and parser blob failures now fail the relevant job. |
| 6 | Make BigQuery staging collision-safe | COMPLETE | 2026-08-08 | The loader uses a sanitized execution/agency-specific staging name, keeps exact agency/date input and validation, and drops only its own stage after successful merge when configured. |
| 7 | Create the workflow | COMPLETE | 2026-08-08 | Added one validated local Workflow source that defaults to yesterday UTC, processes Muni then BART, runs parsers in parallel, and uses Cloud Run Job environment overrides. Deployment validation remains an external action because the Workflows API is disabled. |
| 8 | Deployment and IAM documentation | COMPLETE | 2026-08-08 | Added idempotent job/workflow deployment helpers and least-privilege IAM/Scheduler documentation. No cloud mutation performed; Scheduler cron remains an owner decision pending raw-folder-completeness evidence. |
| 9 | Tests | COMPLETE | 2026-08-08 | Local suite expanded to 29 tests, including real parser entrypoint failure on corrupt protobufs and workflow dependency-source checks. Development failure propagation remains an explicitly recorded external validation. |
| 10 | Backfill support | COMPLETE | 2026-08-08 | One-date runbook now has exact raw-input, overlap, execution, wait, and BigQuery-validation commands; no multi-date automation or cloud operation was performed. |

## Confirmed project decisions

Record decisions once confirmed; do not infer unknown production values from examples.

| Decision | Confirmed value | Date/source |
|---|---|---|
| GCP project | `neighboorhood-nachos` | 2026-08-07 read-only `gcloud run jobs list` and deployed job inspection |
| Cloud Run/Workflow region | `us-central1` | 2026-08-07 deployed Cloud Run Jobs; use the same region for the future workflow |
| GCS bucket and prefixes | Bucket `511_transit_data`; raw `raw/TripUpdates/{agency}/{source_date}/` and `raw/VehiclePositions/{agency}/{source_date}/`; parsed `latest/TripUpdates/{agency}/{source_date}/` and `latest/VehiclePositions/{agency}/{source_date}/`; joined `latest/joined/{agency}/{source_date}/` | 2026-08-07 read-only bucket/job inspection and `PIPELINE_CONTRACT.md`; repository joined writer/loader updated 2026-08-08 in Package 4, deployment still pending |
| BigQuery dataset/table/location | `neighboorhood-nachos.neighborhood_livability_data.trip_stops`, `us-central1` | 2026-08-07 read-only deployed loader args and BigQuery metadata |
| Date semantics | `source_date` selects a UTC raw-data folder date, not a Pacific service day | 2026-08-07 `transit/app/upload_transit_to_bucket.py` and deployed raw object layout |
| Canonical business key | `(agency_id, trip_id, trip_start_date, trip_start_time, direction_id, stop_sequence)`; comparisons of nullable `trip_start_time` and `direction_id` must be null-safe | 2026-08-07 owner approval in chat after review of repository and deployed-data evidence |
| Parser failure policy | TBD | — |

## Step entries

Append entries using this template. Keep previous entries intact.

### Package N — Name — YYYY-MM-DD

**Status:** `IN PROGRESS`, `BLOCKED`, or `COMPLETE`

**Requested scope**

- What the user asked the agent to complete.

**Changes made**

- `relative/path`: high-level change and reason.

**Verification performed by agent**

- `command`: result.

**User verification still needed**

- Safe command/action and expected result, or `None`.

**Deviations and unforeseen issues**

- Difference from the plan, reason, and effect on later packages, or `None`.

**Decisions and assumptions**

- Confirmed decision/temporary assumption, or `None`.

**External actions**

- Deployment, secret rotation, IAM, Scheduler, or data operation completed/still required. Never include secret values.

**Follow-up**

- Remaining work within this package or effect on a later package.

### Package 1 — Confirm configuration and row identity — 2026-08-07

**Status:** `BLOCKED`

**Requested scope**

- Confirm production project/region, GCS bucket and prefixes, BigQuery target/location, UTC date semantics, and one canonical business key without beginning later packages.

**Changes made**

- `transit/plans/IMPLEMENTATION_LOG.md`: recorded the configuration confirmed from deployed resources, the UTC source-date contract, key-analysis evidence, and the one remaining owner decision.
- No application, job configuration, deployment, IAM, secret, GCS object, or BigQuery data was changed.

**Verification performed by agent**

- `git status --short`: identified and preserved the pre-existing modified/untracked files, including `transit/app/environment.yaml`, service artifacts, proposal artifacts, and the untracked planning directory.
- Read all nine Markdown plans in their requested order and inspected all Python jobs, Dockerfiles, YAML configuration, deployment/run scripts, and `transit/app/jobs/README.md` under `transit/app`.
- `gcloud run jobs list --project=neighboorhood-nachos --region=us-central1`: confirmed all four daily jobs exist in `us-central1`.
- Read-only `gcloud run jobs describe` for the four daily jobs: confirmed project, region, bucket/prefix arguments, and deployed loader target `neighborhood_livability_data.trip_stops` in `us-central1`; environment values for jobs other than the non-secret parser configuration were intentionally excluded from output.
- `gcloud storage buckets describe gs://511_transit_data --project=neighboorhood-nachos`: confirmed bucket `511_transit_data` in multi-region `US`.
- Read-only top-level `gcloud storage ls` checks: confirmed both `muni` and `bart` folders under both raw feed roots and both parsed feed roots; confirmed deployed joined folders currently use `latest/joined/{date}/` without agency.
- `bq --project_id=neighboorhood-nachos show --format=prettyjson neighborhood_livability_data.trip_stops`: confirmed the table, schema, `us-central1` location, partitioning on `latest_snapshot_ts`, and clustering on the current five join columns.
- Partition-pruned read-only key comparison for `DATE(latest_snapshot_ts) = '2026-06-28'` (14,050,800 bytes processed): 292,725 Muni rows; zero duplicate extra rows under the current five-column key, the TripUpdates six-column key, or a five-column key excluding direction; zero null `direction_id`; all 292,725 `trip_start_time` values null. This sample cannot determine whether `trip_start_time` is semantically required and contains no BART rows.
- Source comparison: TripUpdates dedupe uses `(agency_id, trip_id, trip_start_date, trip_start_time, direction_id, stop_sequence)`; VehiclePositions dedupe uses `(agency_id, trip_id, direction_id, stop_sequence)`; join and BigQuery merge use `(agency_id, trip_id, direction_id, stop_sequence, trip_start_date)` with null-safe equality only in BigQuery. The original transformation notebook also names the six-column TripUpdates key.

**User verification still needed**

- Approve the canonical key and null policy. Recommended: `(agency_id, trip_id, trip_start_date, trip_start_time, direction_id, stop_sequence)`, with null-safe equality for nullable `trip_start_time` and `direction_id`. This preserves distinct repeated trip instances when GTFS-RT supplies `start_time` and matches the original TripUpdates grain. If a different key is intended, specify the exact columns and null semantics.
- Optional read-only configuration check: `gcloud run jobs describe joined-upsert-bigquery-day --project=neighboorhood-nachos --region=us-central1 --format='value(spec.template.spec.template.spec.containers[0].args)'`. Expected: arguments name bucket `511_transit_data`, dataset `neighborhood_livability_data`, table `trip_stops`, and location `us-central1`.
- Optional read-only metadata check: `bq --project_id=neighboorhood-nachos show --format=prettyjson neighborhood_livability_data.trip_stops`. Expected: `location` is `us-central1`; this requires Google Cloud credentials and does not mutate data.

**Deviations and unforeseen issues**

- Package 1 is `BLOCKED`, not `COMPLETE`, because deployed data and repository history do not safely resolve the semantic row-identity choice. No production value or key was silently selected.
- The repository root documents default region `us-east1`, while deployed transit jobs use `us-central1`; the deployed transit value was recorded because this package explicitly calls for deployed-resource confirmation.
- The active local gcloud default project is unrelated (`lakehouse-503322`), so every inspection command used explicit `--project=neighboorhood-nachos` and region/location flags.

**Decisions and assumptions**

- Confirmed that future workflow `source_date` means the UTC raw-folder date.
- Confirmed that the future workflow should use `us-central1`, matching the transit Cloud Run Jobs and BigQuery dataset; the bucket itself is in multi-region `US`.
- Confirmed the contract prefix roots above. The agency segment missing from deployed joined output is recorded but intentionally left for Package 4.
- No canonical key assumption was made. The six-column recommendation remains subject to owner approval.

**External actions**

- Read-only Google Cloud inspection only. No deployment, IAM update, secret rotation, Scheduler action, GCS write/delete, or BigQuery mutation was performed.

**Follow-up**

- Owner approval of the exact canonical business key and null handling is required to complete Package 1.
- Parser failure policy remains intentionally undecided because it belongs to Package 5, not this package.

### Package 1 — Confirm configuration and row identity — 2026-08-07 (decision resolved)

**Status:** `COMPLETE`

**Requested scope**

- Record the owner's approval of the recommended canonical key and complete Package 1 without starting a later package.

**Changes made**

- `transit/plans/IMPLEMENTATION_LOG.md`: recorded the approved canonical key and null policy, changed Package 1 from `BLOCKED` to `COMPLETE`, and retained the earlier blocked entry as history.
- No pipeline code was changed. Applying this approved key consistently is explicitly scoped to Package 5.

**Verification performed by agent**

- Rechecked `git status --short` before editing and preserved all pre-existing changes.
- Confirmed the approved key exactly matches the six-column TripUpdates deduplication grain already documented in the Package 1 evidence.
- `git diff --check`: passed after the documentation update.
- Secret-pattern scan of `transit/plans/IMPLEMENTATION_LOG.md`: no UUID-like secret value found.

**User verification still needed**

- `rg -n "Canonical business key|Confirm configuration and row identity" transit/plans/IMPLEMENTATION_LOG.md`: expected to show Package 1 as `COMPLETE` and the six approved columns with null-safe handling.

**Deviations and unforeseen issues**

- None. Code changes were intentionally not pulled forward from Package 5.

**Decisions and assumptions**

- Approved canonical key: `(agency_id, trip_id, trip_start_date, trip_start_time, direction_id, stop_sequence)`.
- Approved null policy: missing `trip_start_time` values compare equal to other missing `trip_start_time` values, and missing `direction_id` values compare equal to other missing `direction_id` values. Populated values match only the same populated value.

**External actions**

- None. No deployment, IAM update, secret rotation, Scheduler action, GCS operation, or BigQuery mutation was performed.

**Follow-up**

- Package 5 must apply this key consistently to TripUpdates deduplication, VehiclePositions identity extraction/deduplication, the feed join, duplicate validation, and BigQuery merge behavior.

### Package 2 — Remove credential exposure — 2026-08-07

**Status:** `COMPLETE`

**Requested scope**

- Remove populated API-key values from tracked configuration, document Secret Manager attachment for ingestion and stops, prevent secrets from being passed or printed, and report external rotation/history work without changing deployed resources.

**Changes made**

- `transit/app/jobs/upsert_joined_to_bigquery.env.yaml`: removed the unrelated, unused stops API key from the joined loader configuration.
- `transit/app/jobs/upsert_stops_to_bigquery.env.yaml`: replaced the populated value with a comment requiring Secret Manager injection.
- `transit/app/jobs/ingest.env.yaml`: clarified that its empty API-key entries are safe placeholders and must remain unpopulated.
- `transit/app/jobs/README.md`: documented the active service-based ingestion design, current secret names, numeric-version Secret Manager attachment commands, least-privilege stops-secret access, the paused legacy job risk, rotation/history responsibilities, and the prohibition on command-line/workflow secrets. Removed the legacy ingest deployment example and the stops `--api-key` argument example.
- `transit/app/upload_transit_to_bucket.py`: converted `requests` failures to sanitized errors containing feed, agency, and optional HTTP status but never the prepared URL/API key; updated missing-key guidance to use Secret Manager in Cloud Run.
- `transit/app/upsert_stops_to_bigquery.py`: applied the same sanitized request-error handling and changed deployed-use guidance away from command-line key values.
- `transit/plans/IMPLEMENTATION_LOG.md`: recorded Package 2 status, validation, deviations, and outstanding external actions.

**Verification performed by agent**

- Read-only deployed inspection: `transit-minute-ingest` is the enabled Scheduler target and references four existing Secret Manager secrets; each has enabled version 1. The most recent ten inspected service requests returned HTTP 200. One earlier transient GCS HTTP 503 was followed by successful scheduled calls.
- Read-only dependency inspection: the old `transit-minute-job` Scheduler is paused; no repository runtime depends on the job name; the Workflows API is disabled; parsers consume GCS objects rather than the old job resource.
- Read-only deployed exposure inspection with literal values suppressed: paused `transit-minute-job` still has four literal keys; `stops-upsert-bigquery` has a literal key; `joined-upsert-bigquery-day` has the same literal key even though its code does not read it.
- `python -m py_compile transit/app/upload_transit_to_bucket.py transit/app/upsert_stops_to_bigquery.py`: passed.
- Focused mocked tests in Conda environment `env_transit`: HTTP 401 exceptions for both 511 clients were converted to the expected sanitized messages and a sentinel key embedded in the underlying `requests` exception was absent; successful protobuf and UTF-8-BOM JSON responses remained unchanged.
- Initial combined test with the default Python interpreter could not import `google.cloud.bigquery`; rerunning in the repository's available `env_transit` environment passed. This was an environment dependency issue, not a code failure.
- Ruby/Psych parse of the three edited env YAML files: passed.
- Tracked transit scan for populated `API_KEY`/`API_KEYS` assignments: no matches.
- Tracked transit scan for UUID-shaped values: no matches.
- `rg` check for stops `--api-key` deployment examples: no matches.
- `git diff --check`: passed.
- Reviewed `git status --short` and the scoped diff; only the seven Package 2 files listed above changed.

**User verification still needed**

- `git grep -l -E '^[A-Z0-9_]*(API_KEY|API_KEYS)[A-Z0-9_]*:[[:space:]]*"[^"[:space:]][^"]*"|^[A-Z0-9_]*(API_KEY|API_KEYS)[A-Z0-9_]*=[^[:space:]#]+' -- transit`: expected output is empty.
- `conda run --no-capture-output -n env_transit python -m py_compile transit/app/upload_transit_to_bucket.py transit/app/upsert_stops_to_bigquery.py`: expected exit code 0 and no output.
- Review `transit/app/jobs/README.md` before performing any command labeled as an IAM or Cloud Run mutation. Those commands require credentials and explicit authorization; they can change production access/configuration.

**Deviations and unforeseen issues**

- Added minimal exception sanitization to both 511 HTTP clients after finding that `requests.raise_for_status()` can include the prepared URL and its `api_key` query parameter in Cloud Run logs. This is directly within Package 2's requirement not to print secrets.
- The active service already uses Secret Manager, so no service change is needed for repository cleanup. Documentation reflects the deployed service design instead of the older job-based example.

**Decisions and assumptions**

- Secret values remain absent from workflow inputs and deployment examples. The stops script retains its CLI flag for local backward compatibility, but documentation explicitly prohibits using it for deployed jobs.
- Secret environment mappings use an approved numeric version in documented commands rather than `latest`, following Cloud Run guidance for secrets exposed as environment variables.
- Ignored local files `transit/app/.env` and `transit/app/job.env.yaml` were preserved as user-owned local state; they still contain plaintext values and should be secured or removed by the owner when no longer needed.

**External actions**

- Required owner confirmation: verify that every key formerly committed has been revoked/rotated at 511. Existing Secret Manager metadata does not prove payloads are replacements; no payload was read.
- With explicit authorization, attach `stops-location-api-key` to `stops-upsert-bigquery` and remove its deployed literal value using the documented procedure.
- With explicit authorization, remove the unused deployed literal from `joined-upsert-bigquery-day`.
- Before the paused `transit-minute-job` is ever resumed or manually run, attach Secret Manager mappings and remove its four deployed literals, or separately authorize retiring the legacy job.
- Decide with repository collaborators whether Git history cleanup is required. History rewriting is disruptive and does not replace key rotation.
- No deployment, IAM update, secret read/write, Scheduler change, GCS operation, or BigQuery mutation was performed in this package.

**Follow-up**

- Package 2 repository work is complete. Outstanding cloud/key actions are explicitly external and do not affect the currently scheduled `transit-minute-ingest` service.

### Package 3 — Standardize job inputs — 2026-08-07

**Status:** `COMPLETE`

**Requested scope**

- Standardize environment and CLI inputs for the four daily jobs, share agency/date validation, make deployed commands self-contained, update job definitions/deployment documentation, and add focused validation without beginning later packages.

**Changes made**

- `transit/app/transit_job_config.py`: added a small shared helper for environment strings/integers/booleans, yesterday-UTC defaults, `muni`/`bart` validation, strict historical `YYYY-MM-DD` validation, and required-value checks.
- `transit/app/parse_tripupdates_day_to_parquet.py`: added environment defaults for bucket, agency, source date, prefixes, output settings, shard count, and daily-output flags; retained explicit CLI overrides; validates before creating clients or writing.
- `transit/app/parse_vehiclepositions_day_to_parquet.py`: added the equivalent shared runtime/stable defaults and pre-cloud validation.
- `transit/app/join_tripupdates_vehiclepositions_day_to_parquet.py`: added the equivalent environment defaults and validation while intentionally retaining the current joined path for Package 4.
- `transit/app/upsert_joined_day_to_bigquery.py`: added `--agency`, environment defaults for runtime inputs and confirmed BigQuery/GCS settings, and validation before Storage/BigQuery clients are created; agency-specific loading remains Package 4.
- `transit/app/jobs/parse_tripupdates.env.yaml`: replaced unused `PARSE_*` names with the variables actually read by the script and supplied all stable daily settings.
- `transit/app/jobs/parse_vehiclepositions.env.yaml`: replaced unused `VP_*` names with the variables actually read by the script and supplied all stable daily settings.
- `transit/app/jobs/join_tripupdates_vehiclepositions.env.yaml`: supplied bucket, prefixes, output directory/shards, and the two standard runtime inputs.
- `transit/app/jobs/upsert_joined_to_bigquery.env.yaml`: supplied the confirmed bucket, project, dataset, table, location, joined prefix, and the two standard runtime inputs.
- `transit/app/jobs/deploy_job.sh`: clarified that daily jobs require no static CLI args and added an early missing-env-file guard.
- `transit/app/jobs/README.md`: documented the runtime/stable configuration split, exact no-argument command, local `--help` smoke test, and deployment examples for all four jobs without static args.
- `transit/app/tests/test_job_inputs.py`: added focused standard-library unit tests for all four job env files, precedence, validation, and stable production values.
- `transit/plans/IMPLEMENTATION_LOG.md`: recorded Package 3 status, changes, tests, scope boundaries, and external deployment status.

**Verification performed by agent**

- `conda run --no-capture-output -n env_transit python -m unittest discover -s transit/app/tests -p 'test_*.py' -v`: 9 tests passed. Coverage includes all four no-argument job configurations, explicit CLI precedence, confirmed stable project/dataset/table/location values, agency allowlisting, malformed/today/future date rejection, missing bucket/table failures, and invalid boolean rejection.
- Initial test attempt imported PyYAML, which is not installed in `env_transit`; the test was simplified to parse these flat env files with the standard library, avoiding a new dependency. The rerun passed.
- `conda run --no-capture-output -n env_transit python -m py_compile` for the helper and four daily scripts: passed.
- Exact `conda run --no-capture-output -n env_transit python transit/app/<daily-script> --help` smoke test for all four scripts: passed without cloud access.
- `bash -n transit/app/jobs/deploy_job.sh transit/app/jobs/run_job.sh`: passed.
- Safe deploy-helper failure check with a nonexistent env file: failed before `gcloud` as expected and printed `Environment file not found`.
- Ruby/Psych parse of the four daily job env YAML files: passed.
- Scan of the four daily env files for obsolete `PARSE_*`, `VP_*`, `JOIN_AGENCY`, or `BQ_LOCATION` keys: no matches.
- Tracked transit scan for populated API-key assignments: no matches.
- `git diff --check`: passed.
- Final status/diff review distinguished the still-uncommitted Package 2 changes from Package 3 and found no unrelated modifications introduced during this package.

**User verification still needed**

- `conda run --no-capture-output -n env_transit python -m unittest discover -s transit/app/tests -p 'test_*.py' -v`: expected `Ran 9 tests` and `OK`; local-only and no cloud access.
- For each daily script, run `conda run --no-capture-output -n env_transit python transit/app/<script>.py --help`: expected usage text and exit code 0; local-only and no cloud access.
- `bash -n transit/app/jobs/deploy_job.sh`: expected exit code 0 and no output.
- Review the four daily env YAML files and deployment examples before any deployment. Deployment commands mutate Cloud Run and can incur cost; none were run by the agent.

**Deviations and unforeseen issues**

- Avoided adding PyYAML solely for tests after the first test run exposed that it is absent from `env_transit`; the production code has no YAML dependency and the test's small flat-file reader is sufficient for these env definitions.
- No path, dedupe/join key, parser failure-policy, staging, or workflow changes were pulled forward from Packages 4–7.

**Decisions and assumptions**

- `TRANSIT_SOURCE_DATE` empty/unset means yesterday UTC. An explicit `--service-date` wins.
- `TRANSIT_AGENCY` defaults to `muni`. An explicit `--agency` wins. Only `muni` and `bart` are accepted.
- Source dates must be real historical UTC dates; today and future dates are rejected before cloud access, matching the pipeline contract.
- Stable production values are bucket `511_transit_data`, project `neighboorhood-nachos`, dataset `neighborhood_livability_data`, table `trip_stops`, and BigQuery location `us-central1`, as confirmed in Package 1.
- The loader accepts and validates agency now, but does not add it to its source path until Package 4.

**External actions**

- No image build, Cloud Run deployment/update, job execution, IAM change, Scheduler change, GCS operation, or BigQuery operation was performed.
- The deployed daily jobs still use their existing image/arguments until an authorized later deployment. Review service accounts, resource limits, task count/parallelism, and the immutable image before deploying these definitions.

**Follow-up**

- Package 4 must add agency to joined output/loader paths and implement the approved narrow rerun policy. Package 3 is otherwise complete.

### Package 4 — Fix agency paths and reruns — 2026-08-08

**Status:** `COMPLETE`

**Requested scope**

- Change joined writer/loader paths to `latest/joined/{agency}/{source_date}/part-*.parquet`, prevent stale derived shards on rerun with the smallest safe mechanism, validate deletion scope, and avoid all raw or broad-prefix deletion.

**Changes made**

- `transit/app/transit_gcs_paths.py`: added one small path/safety helper that builds validated derived stage/agency/date prefixes and deletes only objects returned for that exact prefix, using generation-match preconditions when available.
- `transit/app/parse_tripupdates_day_to_parquet.py`: deterministic source-date output now clears only `latest/TripUpdates/{agency}/{source_date}/` immediately before uploading replacement shards. Timestamp-keyed/local output behavior is unchanged.
- `transit/app/parse_vehiclepositions_day_to_parquet.py`: applies the same exact-prefix cleanup to deterministic VehiclePositions output; in parallel mode only the leader reaches final cleanup/upload.
- `transit/app/join_tripupdates_vehiclepositions_day_to_parquet.py`: writes and reports `latest/joined/{agency}/{source_date}/`, clears only that exact joined prefix before replacement, and passes agency into the writer.
- `transit/app/upsert_joined_day_to_bigquery.py`: reads only `latest/joined/{agency}/{source_date}/*.parquet` using the same path builder.
- `transit/app/jobs/README.md`: documented all three derived path contracts, the exact-prefix cleanup policy, rejected roots/patterns, the prohibition on overlapping same-stage/agency/date executions, and the intentional absence of a distributed lock.
- `transit/app/tests/test_gcs_paths.py`: added focused path, safety, deletion, parser-cleanup, and stale-shard replacement tests.
- `transit/plans/IMPLEMENTATION_LOG.md`: updated the confirmed prefix decision and recorded Package 4 results and external deployment/migration considerations.

**Verification performed by agent**

- `conda run --no-capture-output -n env_transit python -m unittest discover -s transit/app/tests -p 'test_*.py' -v`: 17 tests passed. Package 4 coverage includes distinct Muni/BART joined paths, development-prefix support, rejection of empty/raw/bucket/wildcard/URI/traversal roots, invalid agency/date rejection, exact-prefix listing, generation-matched deletion, refusal of an out-of-prefix object, parser cleanup calls, agency-specific uploads, and removal of stale shards when a rerun writes fewer shards.
- `conda run --no-capture-output -n env_transit python -m py_compile` for the path helper and four affected daily scripts: passed.
- Exact `--help` smoke tests for all four daily scripts: passed without cloud access.
- `bash -n transit/app/jobs/deploy_job.sh transit/app/jobs/run_job.sh`: passed.
- Static scan for the former date-only joined writer/loader constructions: no matches.
- Tracked transit scan for populated API-key assignments: no matches.
- `git diff --check`: passed.
- Read-only deployed IAM inspection: bucket `511_transit_data` grants `roles/storage.objectAdmin` to `transit-runner@neighboorhood-nachos.iam.gserviceaccount.com`; the currently deployed join identity `352753661138-compute@developer.gserviceaccount.com` has project `roles/editor`. Both currently include object-delete ability. No IAM change was made; least-privilege cleanup remains Package 8 deployment/IAM work.
- Final status/diff review preserved the uncommitted Package 2–3 changes and found no unrelated changes introduced by Package 4.

**User verification still needed**

- `conda run --no-capture-output -n env_transit python -m unittest discover -s transit/app/tests -p 'test_*.py' -v`: expected `Ran 17 tests` and `OK`; tests use mocks/temp files and do not access or delete GCS data.
- `PYTHONPATH=transit/app conda run --no-capture-output -n env_transit python -c 'from transit_gcs_paths import derived_date_prefix; print(derived_date_prefix("latest/joined", "muni", "2026-06-23")); print(derived_date_prefix("latest/joined", "bart", "2026-06-23"))'`: expected `latest/joined/muni/2026-06-23/` and `latest/joined/bart/2026-06-23/`.
- Before an authorized development deployment, confirm no execution is processing the chosen agency/date. After one controlled run, use a read-only `gcloud storage ls gs://<dev-bucket>/latest/joined/<agency>/<date>/` and expect only the current run's `part-*.parquet` shards.

**Deviations and unforeseen issues**

- Chose exact-prefix cleanup instead of relying solely on a fixed shard count. This is still the plan's small approved option and safely handles a rerun that produces fewer shards or changes shard count.
- No old date-only `latest/joined/{date}/` objects were migrated or deleted. The repository now ignores that legacy layout; historical dates must be reprocessed or deliberately migrated later under an authorized procedure.
- No row-key, failure-policy, staging-table, workflow, or locking changes were pulled forward from later packages.

**Decisions and assumptions**

- Allowed derived roots equal or end with `latest/TripUpdates`, `latest/VehiclePositions`, or `latest/joined`, enabling a development namespace such as `development/latest/joined` while rejecting any path containing a `raw` segment.
- Cleanup occurs only after a nonempty dataframe is ready for deterministic daily upload. It lists and deletes only the exact validated stage/agency/date prefix, then uploads the replacement shards.
- Safe concurrency remains operational: do not overlap the same stage/agency/date. Sequential agencies and the backfill operator check remain the intentionally simple alternative to distributed locking.

**External actions**

- No image build, Cloud Run deployment/update, job execution, IAM change, Scheduler change, GCS write/delete, or BigQuery operation was performed.
- Deploy the joined writer and loader changes together after development review; deploying only the loader before agency-specific joined objects exist will make it fail with missing input.
- Existing date-only joined objects remain untouched and recoverable. Decide later whether to reprocess dates through the workflow or perform a separately authorized exact migration; never broadly delete the legacy root.

**Follow-up**

- Package 5 must align the approved canonical key and make parser/data-quality failures nonzero. Package 4 is otherwise complete.

### Package 5 — Align keys and make failures real — 2026-08-08

**Status:** `COMPLETE`

**Requested scope**

- Apply the approved canonical key consistently, fail on duplicate join/staging keys and parser blob failures, preserve useful count JSON with agency/date, and fail on missing input, zero output, or required-column mismatch.

**Changes made**

- `transit/app/transit_row_identity.py`: added the one shared approved key, required-column validation, and null-safe duplicate-key assertion used by the daily jobs.
- `transit/app/parse_tripupdates_day_to_parquet.py`: uses the shared key for local/global latest-row deduplication, emits agency/source date in stats, fails before publishing derived output when any blob failed, validates final key uniqueness, and fails on zero output.
- `transit/app/parse_vehiclepositions_day_to_parquet.py`: extracts `trip_start_time` from the VehiclePositions TripDescriptor, includes it in stage parquet and deduplication, emits agency/source date in stats, and applies the same strict failure/zero-output/unique-key checks.
- `transit/app/join_tripupdates_vehiclepositions_day_to_parquet.py`: joins on all six key columns, rejects missing columns or duplicate input/output keys, uses a one-to-one merge, and asserts that joined row count equals TripUpdates row count. It no longer silently drops duplicate VehiclePositions rows at join time.
- `transit/app/upsert_joined_day_to_bigquery.py`: merges on all six key columns and queries its staging table before merge to reject duplicate canonical-key groups; stats now identify agency/source date.
- `transit/app/jobs/README.md`: documented the approved key and strict failure behavior.
- `transit/app/tests/test_row_identity.py`: added focused local tests for the key, null duplicate handling, start-time distinction, join cardinality/duplicate rejection, strict parser failure helpers, and staging/merge SQL key coverage.
- `transit/plans/IMPLEMENTATION_LOG.md`: recorded package status, results, compatibility note, and external actions.

**Verification performed by agent**

- `conda run --no-capture-output -n env_transit python -m unittest discover -s transit/app/tests -p 'test_*.py' -v`: 24 tests passed. This includes the 17 prior input/path tests plus seven canonical-key and failure-policy tests; it uses mocks/temp files only and does not access Google Cloud.
- `conda run --no-capture-output -n env_transit python -m py_compile transit/app/transit_row_identity.py transit/app/parse_tripupdates_day_to_parquet.py transit/app/parse_vehiclepositions_day_to_parquet.py transit/app/join_tripupdates_vehiclepositions_day_to_parquet.py transit/app/upsert_joined_day_to_bigquery.py`: passed.
- `git diff --check`: passed.
- Static key scan confirmed `trip_start_time` is extracted by both parsers, the shared key is used by parser dedupe/join/merge, and no legacy five-column `JOIN_KEYS` declaration remains.
- Reviewed the resulting diff and `git status --short`; pre-existing uncommitted Package 2–4 changes were preserved. A tracked transit scan found no populated secret-style assignment introduced by this package.

**User verification still needed**

- `conda run --no-capture-output -n env_transit python -m unittest discover -s transit/app/tests -p 'test_*.py' -v`: expected `Ran 24 tests` and `OK`; local-only and no cloud access.
- `conda run --no-capture-output -n env_transit python -m py_compile transit/app/transit_row_identity.py transit/app/parse_tripupdates_day_to_parquet.py transit/app/parse_vehiclepositions_day_to_parquet.py transit/app/join_tripupdates_vehiclepositions_day_to_parquet.py transit/app/upsert_joined_day_to_bigquery.py`: expected exit code 0 and no output.

**Deviations and unforeseen issues**

- Added a small shared row-identity helper rather than repeating six-column lists and duplicate logic in four files. This is directly scoped to consistent key enforcement and is not a data-quality service or manifest system.
- Existing parsed VehiclePositions objects were produced before `trip_start_time` was added. After an authorized deployment, they must be regenerated by the updated VehiclePositions parser before the updated join can read them; the new required-column check intentionally fails rather than silently using the old four-column grain.

**Decisions and assumptions**

- The approved strict initial policy is now implemented: any raw protobuf blob failure produces a nonzero parser exit, so a future Workflow will not proceed with partial parsed output.
- Null `trip_start_time` and `direction_id` values remain valid; pandas duplicate checks and BigQuery merge predicates treat matching nulls as the same logical key.

**External actions**

- No image build, Cloud Run deployment/update, Cloud Run job execution, IAM change, Scheduler change, GCS write/delete, or BigQuery mutation was performed.
- Before an authorized development deployment, deploy both parsers, the join, and the loader from the same image. Reprocess a chosen historical agency/date through the updated parsers before running the updated join/loader; do not point the new join at old parsed VehiclePositions parquet.

**Follow-up**

- Package 6 will make the BigQuery staging table collision-safe. Its staging-name work is intentionally not started here.

### Package 6 — Make BigQuery staging collision-safe — 2026-08-08

**Status:** `COMPLETE`

**Requested scope**

- Build collision-safe staging names, retain exact joined input and validation, run the idempotent merge, and delete only the exact successful stage when configured.

**Changes made**

- `transit/app/upsert_joined_day_to_bigquery.py`: replaced the fixed `<target>__stage` table with `<target>__stage_<agency>_<sanitized-cloud-run-execution-id>`. It reads `CLOUD_RUN_EXECUTION`; a UUID fallback is used only for local/non-Cloud-Run execution. The generated stage name is included in JSON stats. Existing exact `latest/joined/{agency}/{source_date}/part-*.parquet` loading, nonzero/duplicate validation, null-safe idempotent merge, and optional exact-stage deletion remain in place.
- `transit/app/tests/test_row_identity.py`: added local checks that names are BigQuery-safe, differ by agency and execution, use the Cloud Run execution ID when available, and have a safe local fallback.
- `transit/app/jobs/README.md`: documented execution-specific staging and the configured success-only cleanup behavior.
- `transit/plans/IMPLEMENTATION_LOG.md`: recorded Package 6 results and deployment boundary.

**Verification performed by agent**

- `conda run --no-capture-output -n env_transit python -m unittest discover -s transit/app/tests -p 'test_*.py' -v`: 26 tests passed. Tests are local-only and use mocks/temp files; no GCS or BigQuery access occurred.
- `conda run --no-capture-output -n env_transit python -m py_compile transit/app/upsert_joined_day_to_bigquery.py transit/app/tests/test_row_identity.py`: passed.
- `git diff --check`: passed.

**User verification still needed**

- `conda run --no-capture-output -n env_transit python -m unittest discover -s transit/app/tests -p 'test_*.py' -v`: expected `Ran 26 tests` and `OK`; local-only and no cloud access.
- `PYTHONPATH=transit/app CLOUD_RUN_EXECUTION=loader-run-123 conda run --no-capture-output -n env_transit python -c 'from upsert_joined_day_to_bigquery import build_staging_table_name; print(build_staging_table_name("trip_stops", "muni"))'`: expected `trip_stops__stage_muni_loader_run_123`; local-only and no cloud access.

**Deviations and unforeseen issues**

- The prior optional static `--bq-staging-table` setting was removed from this loader because allowing a shared caller-supplied name would undermine the required collision-safety guarantee. The deployed loader does not use that option. This is a deliberate scoped compatibility change, recorded here rather than silently retaining an unsafe escape hatch.

**Decisions and assumptions**

- Cloud Run provides `CLOUD_RUN_EXECUTION` for job executions. Agency is included in the stage name as additional separation. The UUID fallback is for local/manual invocation only and is not a workflow/run ledger.
- Failed stages are retained for inspection. `TRANSIT_DROP_STAGING_AFTER_MERGE=true` deletes only the generated stage table and only after merge success; it cannot target a shared stage name.

**External actions**

- No image build, Cloud Run deployment/update, job execution, IAM change, Scheduler change, GCS write/delete, or BigQuery query/load/merge/delete was performed.
- An authorized development run after deployment should confirm the emitted `staging_table` JSON value has the expected execution/agency suffix and that only that exact table is removed when success cleanup is enabled.

**Follow-up**

- Package 7 will add the Google Workflow definition. No workflow file or Google Cloud resource was created in this package.

### Package 7 — Create the workflow — 2026-08-08

**Status:** `COMPLETE`

**Requested scope**

- Add one Workflow YAML with UTC date/agency input validation, sequential agencies, parallel parser branches, dependent join/upsert, Cloud Run v2 job calls using environment overrides, and compact execution results.

**Changes made**

- `transit/orchestration/workflow.yaml`: added the version-controlled workflow source. It accepts optional `source_date` and `agencies`, defaults to yesterday UTC and `["muni", "bart"]`, rejects malformed/nonexistent, today, future, and unsupported values, then processes agencies in input order. For each agency it invokes the TripUpdates and VehiclePositions jobs in named parallel branches; after both complete, it invokes join and BigQuery upsert. A small `run_job` subworkflow calls `googleapis.run.v2.projects.locations.jobs.run` with only `TRANSIT_SOURCE_DATE` and `TRANSIT_AGENCY` container-environment overrides. It has no retry/catch framework, so connector or nonzero job failures propagate and prevent downstream work.
- `transit/plans/IMPLEMENTATION_LOG.md`: recorded Package 7 scope, validation, and the external deployment limitation.

**Verification performed by agent**

- `ruby -e 'require "yaml"; ... YAML.load_file("transit/orchestration/workflow.yaml") ...'`: passed; YAML parses and contains `main`, `process_agency`, and `run_job` blocks.
- Static source review confirmed the four deployed job names, both required environment override names, `googleapis.run.v2.projects.locations.jobs.run`, parallel parser branches, no retry/catch clauses, and no secret values.
- `conda run --no-capture-output -n env_transit python -m unittest discover -s transit/app/tests -p 'test_*.py' -v`: 26 existing local tests passed. These tests use mocks/temp files and do not call Google Cloud.
- `git diff --check`: passed.
- Read-only `gcloud workflows list` during prior investigation reported `workflows.googleapis.com` is disabled for `neighboorhood-nachos`; no API enablement, deployment, or execution was attempted.

**User verification still needed**

- `ruby -e 'require "yaml"; YAML.load_file("transit/orchestration/workflow.yaml"); puts "workflow YAML parse OK"'`: expected `workflow YAML parse OK`; local-only.
- After the owner authorizes a development deployment and the Workflows API is enabled there, deploy this exact source to a development workflow and run it with one historical date. Expected order: Muni parsers together, then Muni join/upsert, then BART parsers together, then BART join/upsert. A parser/join failure must prevent dependent stages.

**Deviations and unforeseen issues**

- Development-project workflow syntax/connector validation could not be performed because the authorized project has the Workflows API disabled. `gcloud workflows deploy` has no local `--validate-only` mode, and enabling the API or deploying would mutate cloud resources outside this package's authorization. Local YAML/structural validation was performed instead.
- No connector timeout was set because no measured parser duration is recorded yet. The connector's current documented default is 1,800 seconds; measure a development execution before deciding whether Package 8 deployment settings should set a longer timeout.

**Decisions and assumptions**

- The workflow uses the confirmed `us-central1` region and retrieves its project ID from the workflow runtime environment. It names only the four confirmed daily Cloud Run Jobs.
- The workflow service account will require Cloud Run job execution-with-overrides permission; assigning/documenting IAM is intentionally deferred to Package 8.
- Input `agencies` order is preserved, so the default is Muni then BART. No concurrent agency/date processing, date ranges, locks, ledgers, secrets, or broad retry logic was added.

**External actions**

- No API enablement, image build, workflow deployment/execution, Cloud Run change/execution, IAM change, Scheduler change, GCS operation, or BigQuery operation was performed.
- Before any development deployment, the owner must authorize enabling the Workflows API in the chosen development project and provide/select a workflow service account with the required narrow Cloud Run permissions. Production deployment remains out of scope.

**Follow-up**

- Package 8 will document small deployment and IAM helpers. Package 9 will include workflow failure-propagation validation in a development deployment after those external prerequisites are available.

### Package 8 — Deployment and IAM documentation — 2026-08-08

**Status:** `COMPLETE`

**Requested scope**

- Document or implement small idempotent helpers for one immutable shared image, four job definitions, workflow deployment, one daily Scheduler trigger, and least-privilege service accounts without deploying or changing IAM.

**Changes made**

- `transit/app/jobs/deploy_job.sh`: added optional `--service-account <email>` support for Cloud Run Job create/update. The option is omitted when not supplied, preserving existing behavior; when supplied it applies the reviewed identity to the job definition.
- `transit/orchestration/deploy_workflow.sh`: added a small executable helper that validates required local arguments/source, then uses `gcloud workflows deploy` to create or update one named workflow with a specified service account and error-only call logging. It never enables APIs or creates Scheduler jobs itself.
- `transit/orchestration/README.md`: documented resource values, read-only preflight/post-deployment checks, narrow role mapping, explicit mutation templates for service accounts/IAM, one immutable image plus the four job definitions, workflow deployment, and a create-or-update Scheduler procedure. Every mutation command is labelled and contains placeholders where owner choices are required.
- `transit/plans/IMPLEMENTATION_LOG.md`: recorded Package 8 results and the one remaining Scheduler-time decision.

**Verification performed by agent**

- `bash -n transit/app/jobs/deploy_job.sh transit/orchestration/deploy_workflow.sh`: passed.
- `bash transit/app/jobs/deploy_job.sh --help` and `bash transit/orchestration/deploy_workflow.sh --help`: passed; usage documents the optional job service account and required workflow identity/source.
- `ruby -e 'require "yaml"; YAML.load_file("transit/orchestration/workflow.yaml") ...'`: passed.
- `conda run --no-capture-output -n env_transit python -m unittest discover -s transit/app/tests -p 'test_*.py' -v`: 26 tests passed; local-only mocks/temp files, no Google Cloud access.
- `git diff --check`: passed.
- Secret-pattern scan of `transit/app` and `transit/orchestration`: no populated secret-style assignment introduced.
- Read current official role documentation before writing the guide: `roles/run.jobsExecutorWithOverrides` includes `run.jobs.runWithOverrides`; `roles/workflows.invoker` invokes the workflow; Storage Object Viewer/Object Admin and BigQuery Job User/Data Editor provide the documented resource-level capabilities.

**User verification still needed**

- `bash -n transit/app/jobs/deploy_job.sh transit/orchestration/deploy_workflow.sh`: expected exit code 0 and no output; local-only.
- `bash transit/orchestration/deploy_workflow.sh --help`: expected usage text and exit code 0; local-only.
- Review `transit/orchestration/README.md` with the authorized cloud owner before any mutation command. Confirm the service-account names, approved immutable image repository/tag, development project, and Scheduler cron.

**Deviations and unforeseen issues**

- The plans require Scheduler to run only after the prior UTC folder closes but provide no measured/raw-folder-completeness time. The guide therefore uses required `SCHEDULE_CRON_UTC=<approved-daily-cron>` rather than silently choosing a potentially unsafe production schedule. This does not block local documentation or later Package 9 tests, but blocks authorized Scheduler creation until the owner decides.

**Decisions and assumptions**

- Recommended one transform account is shared by both parsers and join because all need list/read/write/delete access only to GCS objects; the loader is separated because it requires BigQuery mutations but only GCS read access.
- The workflow account receives `roles/run.jobsExecutorWithOverrides` only on the four named jobs. The Scheduler account receives `roles/workflows.invoker` only on the one workflow. The guide explicitly avoids project-wide `Editor` grants.
- An immutable image reference is required but no repository/tag was invented because no approved Artifact Registry repository was confirmed.

**External actions**

- No API enablement, image build, Cloud Run deployment/update/execution, workflow deployment/execution, IAM change, Scheduler change, GCS operation, or BigQuery operation was performed.
- Before development deployment, an authorized owner must select the development project, immutable image repository/tag, four service-account names, and an approved Scheduler cron after confirming raw-folder completeness. Enabling Workflows and all deployment/IAM/Scheduler commands remain explicit external mutations in `transit/orchestration/README.md`.

**Follow-up**

- Package 9 will consolidate focused tests, including corrupt protobuf failure and workflow failure propagation in an authorized development deployment. Package 10 will document the final one-date backfill command after a workflow name/development deployment is selected.

### Package 9 — Tests — 2026-08-08

**Status:** `COMPLETE`

**Requested scope**

- Add focused coverage for inputs/paths, canonical keys, corrupt protobuf failure, join preservation, staging names/merge behavior, idempotent rerun logic, and workflow failure propagation.

**Changes made**

- `transit/app/tests/test_pipeline_failures.py`: added direct local entrypoint tests for a corrupt TripUpdates blob and a corrupt VehiclePositions blob. Each test runs the actual parser `main()` with mocked Storage listing/download only, asserts its strict nonzero `RuntimeError`, and asserts no upload bucket call was reached. Added source-level workflow checks for the parallel parser declaration, join-before-upsert ordering, Cloud Run connector usage, and absence of catch/retry clauses that could swallow a failed job.
- `transit/plans/IMPLEMENTATION_LOG.md`: recorded the consolidated suite and development-validation limitation.

**Verification performed by agent**

- `conda run --no-capture-output -n env_transit python -m unittest discover -s transit/app/tests -p 'test_*.py' -v`: 29 tests passed. Coverage now includes environment/CLI inputs, agency/date paths and safe cleanup, six-column canonical-key uniqueness and null behavior, parser corrupt-blob failures before upload, join row-count preservation/duplicate rejection, staging uniqueness/duplicate SQL/merge-key SQL, and workflow dependency-source assertions.
- `conda run --no-capture-output -n env_transit python -m py_compile transit/app/tests/test_pipeline_failures.py`: passed.
- `git diff --check`: passed.

**User verification still needed**

- `/Users/patri/miniconda3/envs/env_transit/bin/python -m unittest discover -s transit/app/tests -p 'test_*.py' -v`: expected `Ran 29 tests` and `OK`; local-only, with mocked Storage and no Google Cloud access.
- Authorized development integration after Package 8 prerequisites: deploy the workflow/jobs to a development project, run a date containing a deliberately corrupt raw protobuf, and verify Workflow fails while join/upsert have no execution. Then run a successful date twice and verify no duplicate business keys. This is a Cloud Run/Workflow/GCS/BigQuery mutation and was not authorized or run.

**Deviations and unforeseen issues**

- The plan's workflow failure-propagation test cannot be run locally because it requires a deployed workflow, Cloud Run jobs, a development bucket/dataset, and a controlled corrupt input. The Workflows API remains disabled in the available project and no development project/resource authorization was provided. Local source checks verify the structural prerequisite (uncaught connector failures and dependency ordering), but do not replace the required deployment test.

**Decisions and assumptions**

- Existing tests from Packages 3–7 already covered environment/CLI behavior, path construction, canonical-key duplicate rejection, staging names, merge SQL, and join preservation, so Package 9 adds the missing direct corrupt-blob entrypoint coverage rather than duplicating those cases.
- `MERGE` idempotence remains tested structurally through its six-column null-safe `WHEN MATCHED`/`WHEN NOT MATCHED` design; an actual identical-data rerun remains part of the authorized development integration test.

**External actions**

- No API enablement, image build, deployment/execution, IAM/Scheduler change, GCS operation, or BigQuery operation was performed.
- The development workflow failure and identical-rerun tests remain required before production Scheduler enablement.

**Follow-up**

- Package 10 will document the final one-date workflow backfill invocation and operator procedure without implementing multi-date automation.

### Package 10 — Backfill support — 2026-08-08

**Status:** `COMPLETE`

**Requested scope**

- Document one explicit-date invocation of the existing workflow, including raw-input checks, same-date overlap prevention, wait, BigQuery validation, and sequential continuation. Do not implement multi-date automation.

**Changes made**

- `transit/plans/BACKFILL_RUNBOOK.md`: expanded the existing one-date-only runbook with confirmed project/resource variables; four exact read-only raw protobuf-prefix checks; a read-only active Workflow execution check that displays the input argument; read-only Cloud Run Job execution-history checks; one labelled `gcloud workflows execute` command that submits `source_date` and both agencies to `transit-daily`; an exact wait/describe procedure for the resulting execution; and a parameterized read-only BigQuery validation query for per-agency row counts and canonical-key duplicate groups.
- The runbook explicitly prohibits date ranges and shell-loop automation. Operators must validate one date before manually selecting the next, oldest to newest.

**Verification performed by agent**

- Read local `gcloud workflows executions list`, `wait`, `describe`, and `execute` help. The documented command forms and relevant options (`--project`, `--location`, `--workflow`, `--data`, and `--format`) match the installed CLI.
- Confirmed the workflow input contract in `transit/orchestration/workflow.yaml`: it accepts `source_date` and `agencies`, processes Muni then BART, and rejects today/future dates.
- Confirmed the BigQuery target includes `agency_id`, the six-column canonical identity, and `latest_snapshot_ts`; `latest_snapshot_ts` is the appropriate UTC-date predicate for the one-date post-load validation query.
- No cloud resource or data operation was run. `gcloud --help` emitted only a local sandbox warning that its user log directory is not writable; it did not contact or mutate the project.

**User verification still needed**

- After the authorized development deployment from Packages 7–8, follow the complete runbook for one known-complete historical UTC date. Expected: all four raw-prefix commands list `.pb` objects; no active same-date execution; one workflow execution succeeds; both agencies appear in the BigQuery query with nonzero expected rows and zero duplicate-key groups.
- Perform the remaining development integration tests from Package 9 (controlled corrupt protobuf failure and identical-data rerun) before production Scheduler enablement.

**Decisions and assumptions**

- The runbook uses the confirmed workflow name `transit-daily`, project `neighboorhood-nachos`, region `us-central1`, bucket `511_transit_data`, and target `neighborhood_livability_data.trip_stops`. An operator must still verify these are the intended environment before executing its mutation command.
- Date-level target validation uses `DATE(latest_snapshot_ts)` because the target has no persisted `source_date` column and snapshot timestamps are derived from the selected UTC raw folders. This is an operational validation predicate, not an additional row-identity field.
- The BigQuery query counts duplicate canonical-key groups across `agency_id`, `trip_id`, `trip_start_date`, `trip_start_time`, `direction_id`, and `stop_sequence`; zero is expected.

**Deviations and external actions**

- No multi-date workflow, loop, lock service, manifest, data deletion, deployment, API enablement, IAM change, Scheduler action, GCS operation, or BigQuery operation was implemented or performed.
- Actual workflow execution and the BigQuery validation query are intentional cloud operations (the query can incur normal query cost). They remain clearly labelled in the runbook and require the owner’s authorization in the appropriate environment.
- The existing unresolved prerequisite remains: deploy the four jobs/workflow to an approved development environment, choose service accounts and immutable image, and complete development integration checks before any production Scheduler enablement.

**Follow-up**

- All numbered implementation packages are complete. The remaining work is authorized development deployment/integration validation and, only after review, the external production rollout described in `transit/orchestration/README.md`.
