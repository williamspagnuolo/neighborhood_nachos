# Text-to-SQL Agent Plan

## Objective
Add a natural-language querying capability to the Interactive SF Dashboard.
Users type a plain-English question (for example, "which neighborhood had
the most 311 requests last week?"), Gemini writes a BigQuery SQL statement,
the UI shows the generated SQL plus a dry-run byte estimate, and — after
the user clicks Run — the results appear as a table.

## Success Criteria
- User can ask a question in Pacific Time terms and get answers from the
  gold dataset without writing SQL themselves.
- The generated SQL is always shown to the user before it runs.
- The agent never issues a mutating statement (only `SELECT` / `WITH`).
- Query cost is bounded by `maximum_bytes_billed` and a hard result row cap.
- Failure modes (invalid question, invalid SQL, over-budget query, empty
  result) all produce a clear, actionable UI message.
- The existing dashboard tab is not regressed.

## Locked Product Decisions
- **Dataset:** the agent queries `neighborhood_livability_gold` (a different
  dataset from the existing dashboard, which reads `neighborhood_livability_data`).
- **Schema source:** the agent introspects `INFORMATION_SCHEMA.COLUMNS` at
  startup and caches the result. No manually-maintained schema doc for the
  agent.
- **LLM provider:** Vertex AI Gemini via `google-genai` in Vertex mode,
  auth via ADC on the existing `dashboard-runner` service account.
- **Model default:** `gemini-2.5-flash` (fast, cheap, configurable).
- **UI placement:** the existing single-page layout becomes Tab 1
  ("Dashboard"). The agent lives on Tab 2 ("Ask a question").
- **Flow:** two-step — generate SQL, preview + estimate, user clicks Run.
- **Guardrails:**
  - Reject anything that isn't a single `SELECT` / `WITH` statement.
  - Force an outer `LIMIT` if the model didn't include one.
  - Dry-run every query first and reject if projected billed bytes exceed
    `DASH_LLM_MAX_BYTES_BILLED` (default 5 GB).
  - Set `maximum_bytes_billed` on the real query for defense in depth.
  - Runtime SA has only `roles/bigquery.dataViewer` — physically cannot
    mutate BQ even if the guardrails failed.

## Assumed IAM
Add to the existing `dashboard-runner` service account:
- `roles/aiplatform.user` (project-level) — allow Vertex AI Gemini calls.
- `roles/bigquery.dataViewer` on the `neighborhood_livability_gold` dataset
  (if it isn't already project-wide).

No new secrets. No API keys.

---

## Phase 0 — Scope Lock
### Tasks
- Confirm dataset name (`neighborhood_livability_gold`) and BQ location.
- Confirm two-step preview-then-run flow.
- Confirm agent runs in the same Cloud Run service, same runtime SA.

### Deliverables
- This document.

### Checkpoint
- No ambiguity on dataset, model, provider, or IAM.

---

## Phase 1 — Schema Introspection
### Tasks
- Query `INFORMATION_SCHEMA.COLUMNS` for the gold dataset once at
  agent construction; cache in-process.
- Skip staging tables (any table ending in `__stage` or `__staging`).
- Serialize schema as a compact bullet list per table for the prompt.

### Deliverables
- `text_to_sql.SchemaIntrospector` with `.load()` and `.render_for_prompt()`.

### Checkpoint
- Dry-run the introspection query manually: returns non-empty schema for
  the gold dataset.

---

## Phase 2 — Prompt + LLM Client
### Tasks
- Build a system prompt that includes:
  - Project, dataset, and location.
  - Serialized schema.
  - Time contract (UTC storage, Pacific display).
  - "Only SELECT / WITH; no DDL, DML, or scripts. Return JSON."
  - Two or three worked examples grounded in the actual schema.
- Wire `google-genai` Vertex client. Model + location from `AppConfig`.
- Parse model output as `{"sql": "...", "explanation": "..."}`.

### Deliverables
- `text_to_sql.TextToSqlAgent.generate(question) -> GeneratedQuery`.

### Checkpoint
- Local run: agent returns valid parseable SQL for "count 311 incidents
  by neighborhood in the last 7 days".

---

## Phase 3 — Validation + Dry Run
### Tasks
- Parse with `sqlglot` in BigQuery dialect.
- Reject if:
  - Multiple statements
  - Not a `SELECT` / `WITH` root
  - Contains any of `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`,
    `DROP`, `ALTER`, `TRUNCATE`, `CALL`, `EXECUTE`, `GRANT`, `REVOKE`
- If the top-level statement has no `LIMIT`, wrap it in
  `SELECT * FROM ( <sql> ) LIMIT :row_cap`.
- Run `client.query(sql, job_config=dry_run=True, use_query_cache=False)`
  and capture `total_bytes_processed`.
- Reject if `total_bytes_processed > max_bytes_billed`.

### Deliverables
- `text_to_sql.SqlValidator` and `text_to_sql.SqlExecutor.dry_run(sql)`.

### Checkpoint
- Manual test: pasting `DROP TABLE ...` or `SELECT *` on a huge table is
  rejected with a specific error.

---

## Phase 4 — UI Wiring
### Tasks
- Wrap the existing dashboard layout in a `dcc.Tabs` with two tabs.
- Tab 2 layout:
  - Textarea for the question
  - "Generate SQL" button
  - Preview panel showing generated SQL, model explanation, and byte
    estimate
  - "Run query" button (disabled until we have a validated SQL)
  - Results `dash_table.DataTable`
  - Error area
  - `dcc.Store` for the pending validated SQL
- Two callbacks:
  1. Generate SQL: question → SQL preview + estimate + explanation
  2. Run query: validated SQL from store → results table

### Deliverables
- Updates to `dashboard/app/app.py`.

### Checkpoint
- Manual walkthrough: type a question → SQL appears → click Run → table
  appears. Toggling to Tab 1 shows the original dashboard unchanged.

---

## Phase 5 — Documentation and Deploy
### Tasks
- Document all new env vars in `dashboard/README.md`.
- Document the additional IAM binding.
- Update the Cloud Run deploy command to include the new env vars.
- Note the agent's limitations in a "Known Limitations" section.

### Deliverables
- Updated `dashboard/README.md`.

### Final Go/No-Go Check
- Existing tab passes the original validation checklist.
- Agent tab: at least three real user questions produce correct results.
- Attempting a destructive query is rejected before dry-run.
- Attempting a >5 GB scan is rejected at dry-run.
