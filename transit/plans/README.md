# Transit orchestration planning index

These documents describe a deliberately small orchestration layer around the existing `transit/app` Cloud Run jobs.

## Recommended reading order

1. [`HUMAN_ORCHESTRATION_PLAN.md`](HUMAN_ORCHESTRATION_PLAN.md) — novice-friendly setup and implementation plan.
2. [`RISKS_OPEN_QUESTIONS.md`](RISKS_OPEN_QUESTIONS.md) — issues that must be decided or fixed before scheduling.
3. [`PIPELINE_CONTRACT.md`](PIPELINE_CONTRACT.md) — the minimal stage, input, output, and idempotency contract.
4. [`CODING_AGENT_WORKFLOW.md`](CODING_AGENT_WORKFLOW.md) — how the coding agent should complete and hand off one step at a time.
5. [`AI_IMPLEMENTATION_PLAN.md`](AI_IMPLEMENTATION_PLAN.md) — focused, sequential work packages for a coding agent.
6. [`IMPLEMENTATION_LOG.md`](IMPLEMENTATION_LOG.md) — persistent status, change, test, and deviation record maintained by the agent.
7. [`TEST_AND_ACCEPTANCE_PLAN.md`](TEST_AND_ACCEPTANCE_PLAN.md) — essential tests only.
8. [`BACKFILL_RUNBOOK.md`](BACKFILL_RUNBOOK.md) — safe one-date-at-a-time backfill procedure.

## How to use these plans with a coding agent

Ask the agent to complete one numbered work package from `AI_IMPLEMENTATION_PLAN.md`. The agent should follow `CODING_AGENT_WORKFLOW.md`, update `IMPLEMENTATION_LOG.md`, and then stop for review. Its chat response must summarize the result and provide commands or procedures you can use to verify that package before requesting the next one.

## Minimal target design

```text
Cloud Scheduler (daily)
  -> Google Workflow
     -> for Muni, then BART:
        -> TripUpdates parser ---------+
                                        +-> join -> BigQuery upsert
        -> VehiclePositions parser ----+
```

The parsers run in parallel because they are independent. Muni and BART run sequentially to reduce concurrency concerns and keep the workflow easy to understand. A manual backfill invokes the same workflow with an explicit historical UTC source date.

The minute-level API polling service and the separate stops-dimension job remain outside this daily workflow.

## Intentional omissions

The first version does not require a custom lock service, pipeline run database, success-manifest system, multi-date workflow, automatic stale-resource cleanup, or complex retry framework. Safe operation comes from deterministic paths, an idempotent BigQuery merge, unique staging tables, one-date backfills, and an operator check that no conflicting workflow is active.

