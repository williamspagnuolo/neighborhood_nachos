# Gold-Layer Semantics Notes

This file is loaded verbatim into the text-to-SQL agent's system prompt at
container startup. Everything you add here is visible to Gemini on every
question. Use it for context that isn't naturally captured by BigQuery
table or column descriptions.

**When to edit this file** (not the code):
- New cross-table joining conventions or foreign-key rules.
- Business definitions the model wouldn't know from column names alone.
- Warnings about data-quality caveats or common pitfalls.
- Preferred query patterns for common analytical questions.

**How to edit**: normal PR. This file ships in the container image, so it
takes effect on the next Cloud Run revision (no other code change needed).
BigQuery table / column descriptions and the dataset description are pulled
automatically by the agent — put things there when possible, and put things
here only when they don't naturally fit on a single table or column.

---

## Time contract
- All TIMESTAMP columns in the gold dataset are stored in UTC.
- Users of the dashboard think in Pacific Time (`America/Los_Angeles`).
  Convert on the boundary: use `DATE(ts, "America/Los_Angeles")` for
  Pacific-date grouping and `TIMESTAMP(datetime_value, "America/Los_Angeles")`
  when a Pacific-local timestamp is required.

## Boundary conventions
- Every point-based fact table is expected to carry both
  `neighborhood_id` and `police_district_id` foreign keys, populated
  upstream via spatial joins against the `neighborhoods` and
  `police_districts` polygon tables.
- Prefer joining on those integer FKs. Do not attempt ad-hoc
  point-in-polygon joins from the agent — the boundary tables' `geometry`
  column is stored as WKT text and joining against it is expensive.

## Standard analytical patterns
- **"Top N neighborhoods by X"** → `GROUP BY neighborhood_id`, `ORDER BY x DESC`,
  `LIMIT N`, then join to `neighborhoods` for the human-readable `name`.
- **"Over the last N days"** → filter on `DATE(<timestamp_col>, "America/Los_Angeles")
  >= DATE_SUB(CURRENT_DATE("America/Los_Angeles"), INTERVAL N DAY)`.

## Data-quality caveats
- The most recent 2-3 days of any incident-source data (police, 311)
  typically under-report because agencies file their reports with a lag.
  When the question is about the "most recent day" or a "trend", either
  include this caveat in the answer or exclude the last 48 hours.

---

## (Waiting on the gold-layer tables to be created. Add per-table
## semantics here as the tables land, or — even better — attach them as
## native BigQuery table / column descriptions where they'll be picked
## up automatically.)
