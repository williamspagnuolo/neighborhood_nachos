# Transit orchestration risks and open questions

This list is intentionally limited to issues that materially affect correctness, safety, or basic operation.

## Must fix before scheduling

| Finding | Why it matters | Simple resolution |
|---|---|---|
| Live-looking 511 keys are stored in tracked job YAML. | Credentials may be compromised. | Rotate them, remove populated values, and use Secret Manager. |
| Joined output is `latest/joined/<date>/` without agency. | Muni and BART can overwrite or mix shards. | Use `latest/joined/<agency>/<date>/` and update the loader. |
| BigQuery uses one shared stage with `WRITE_TRUNCATE`. | Accidental overlapping loads can destroy each other's stage. | Use a unique stage name based on Cloud Run execution ID. |
| Parser dedupe, join, and merge keys differ. | Rows may collapse, multiply, duplicate, or match the wrong trip. | Approve one logical key and apply it consistently. |
| Job env YAML names are mostly not read by the scripts. | Documented/deployed jobs may start without required argparse values. | Add environment defaults or complete static job args and smoke-test the exact command. |
| Parser blob failures can still exit successfully. | Workflows can continue with incomplete data. | Fail the job if any blob fails in the first release. |
| Rewriting a date can leave stale shard files. | A wildcard load can combine output from separate attempts. | Overwrite a fixed complete shard set or clear only the exact derived agency/date prefix before rewriting. |
| Dataset/location examples conflict (`transit` vs `neighborhood_livability_data`, `US` vs `us-central1`). | Loads may fail or write to the wrong place. | Inspect and document the actual production resources. |
| No target-table DDL is included. | A new deployment cannot reproduce the expected schema. | Add the confirmed schema as a small SQL file or setup section. |
| UTC raw folder date is called service date. | Local-day/overnight trip interpretation can be wrong. | Name the workflow input `source_date` and document that it selects a UTC folder. |

## Worth checking, but not a reason to overbuild

- The join reads all parquet into pandas. Confirm one full agency/day fits configured memory; only redesign if measurements show it does not.
- The Workflows connector defaults to a 30-minute long-running-operation timeout. Measure parser duration and raise the timeout if needed.
- The parser's leader task waits for peer stage files. Align Cloud Run task timeout with that behavior and clean old scratch prefixes manually or with a basic bucket lifecycle rule.
- Join should log match rate and verify output row count equals TripUpdates input. Advanced anomaly thresholds can wait for several days of baseline data.
- Local `/tmp` reports disappear after Cloud Run exits. Structured log summaries are adequate initially; persistent manifests can be added later if diagnosis proves difficult.
- The stops upsert uses its own shared stage. Since it is separate and infrequent, simply avoid concurrent stops runs or give it a unique stage too.

## Blocking questions for the project owner

1. What are the production project, region, bucket, BigQuery dataset/table, and dataset location?
2. Should the workflow process UTC raw folder dates as the first version assumes?
3. What exact columns uniquely identify one joined transit row? Does `trip_start_time` belong in the key, and how should null `direction_id` be handled?
4. Should both agencies share the same final table?
5. What is the expected target schema, and should the table be partitioned by `trip_start_date`?
6. Are any failed protobufs acceptable, or should the initial policy remain strict?
7. Which historical UTC dates are missing, and do both raw feeds exist for each requested agency/date?
8. Who is authorized to pause the schedule, run a backfill, and rotate the exposed keys?

## Explicitly deferred complexity

Do not add these in the first version unless a concrete requirement appears:

- a custom distributed lock system;
- a pipeline run-ledger database;
- success manifests for every stage;
- automatic multi-date backfill orchestration;
- nested agency/date parallelism;
- generalized retries or automatic stale-lock recovery;
- a full monitoring dashboard and anomaly platform;
- automatic cleanup services for every failed artifact.

Sequential agencies and one-date-at-a-time backfills are the simplicity tradeoff. Operators must check that no same-date workflow is active before a manual run.

