# Transit orchestration implementation log

This file is maintained by the coding agent during implementation. It records actual work and deviations without changing the original plan history.

Allowed statuses: `NOT STARTED`, `IN PROGRESS`, `BLOCKED`, `COMPLETE`.

## Package status

| Package | Name | Status | Last updated | Notes |
|---|---|---|---|---|
| 1 | Confirm configuration and row identity | COMPLETE | 2026-08-07 | Deployed configuration, UTC date semantics, and the six-column canonical key with null-safe handling are confirmed. |
| 2 | Remove credential exposure | NOT STARTED | — | — |
| 3 | Standardize job inputs | NOT STARTED | — | — |
| 4 | Fix agency paths and reruns | NOT STARTED | — | — |
| 5 | Align keys and make failures real | NOT STARTED | — | — |
| 6 | Make BigQuery staging collision-safe | NOT STARTED | — | — |
| 7 | Create the workflow | NOT STARTED | — | — |
| 8 | Deployment and IAM documentation | NOT STARTED | — | — |
| 9 | Tests | NOT STARTED | — | — |
| 10 | Backfill support | NOT STARTED | — | — |

## Confirmed project decisions

Record decisions once confirmed; do not infer unknown production values from examples.

| Decision | Confirmed value | Date/source |
|---|---|---|
| GCP project | `neighboorhood-nachos` | 2026-08-07 read-only `gcloud run jobs list` and deployed job inspection |
| Cloud Run/Workflow region | `us-central1` | 2026-08-07 deployed Cloud Run Jobs; use the same region for the future workflow |
| GCS bucket and prefixes | Bucket `511_transit_data`; raw `raw/TripUpdates/{agency}/{source_date}/` and `raw/VehiclePositions/{agency}/{source_date}/`; parsed `latest/TripUpdates/{agency}/{source_date}/` and `latest/VehiclePositions/{agency}/{source_date}/`; contract joined path `latest/joined/{agency}/{source_date}/` | 2026-08-07 read-only bucket listing, deployed job args, and `PIPELINE_CONTRACT.md`; deployed joined output currently lacks agency and remains a Package 4 fix |
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
