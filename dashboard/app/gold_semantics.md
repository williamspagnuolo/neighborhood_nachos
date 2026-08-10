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
- For normal date filtering and grouping, prefer the precomputed
  `event_date_pacific` column.
- For normal time-of-day filtering, prefer the precomputed
  `event_time_pacific` column.
- Use `event_ts_utc` when an exact timestamp or UTC comparison is required.
- Only derive Pacific-local values from a UTC timestamp when the existing
  Pacific date/time columns cannot answer the question.
- When conversion is necessary, explicitly use the
  `America/Los_Angeles` timezone.

## Preferred analytical models
- For dashboard and general analytical questions, prefer these curated dbt mart tables:
  - `gold_dashboard_events`
  - `gold_boundaries`
  - `gold_rental_listings`

- Do not use `stage_*` or `int_*` models unless the requested information cannot be answered from the gold marts.

### gold_dashboard_events

- **One row represents one source fact record used by the dashboard.**

- `event_type` identifies the domain:
  - `311`
  - `police`
  - `transit`

- `category` contains the normalized service/category value for 311 and police events.
- Transit-specific fields such as `arrival_delay_sec` are populated only when `event_type = 'transit'`.
- All timestamps are UTC in `event_ts_utc`.
- `event_date_pacific` and `event_time_pacific` provide Pacific-local calendar values for dashboard filtering.

### gold_rental_listings

- **One row represents one rental listing episode.** The intended grain is
  `rentcast_id` + `listed_date`. A single RentCast property/listing ID may have
  multiple historical listing episodes if it was removed and later relisted.
- `listed_date` and `removed_date` are UTC TIMESTAMP values.
- `removed_date IS NULL` means the listing episode is currently active.
- `price` is the asking rent for that listing episode.
- `bedroom_bucket` is the dashboard-ready grouping and is expected to contain:
  - `studio`
  - `1bd`
  - `2bd`
  - `3bd`
  - `4bd+`
  - `unknown`
- Rental rows already contain `neighborhood_id` and `police_district_id`; use
  those assigned boundary IDs for normal analytical queries rather than a
  spatial join.
- For a selected dashboard date range represented by an inclusive Pacific start
  date and inclusive Pacific end date, convert that range to UTC as
  `[start_utc, end_utc)` and include a rental episode when its active interval
  overlaps the selected interval:

    listed_date < end_utc
    AND (removed_date IS NULL OR removed_date > start_utc)

- Do **not** filter rentals with `listed_date BETWEEN start AND end`; that would
  omit listings that began before the selected period but remained available
  during it.
- For current-market metrics, use `removed_date IS NULL` (and exclude any
  future-dated listings with `listed_date <= CURRENT_TIMESTAMP()`).
- The dashboard's hour-of-day control applies to event data, not rentals. Rental
  comparisons should use the selected calendar-date range only.

### gold_boundaries

- **Contains both neighborhood and police-district boundaries.**

- `boundary_type` is either:
  - `neighborhoods`
  - `police_districts`

- Rows with NULL geometry represent the intentional unlocated/unknown boundary member and should be retained for analytics but not rendered on maps.

## Boundary Conventions
**`gold_dashboard_events` and `gold_rental_listings` both contain:**

- `neighborhood_id`
- `police_district_id`

Both columns are STRING identifiers.

`gold_boundaries.boundary_id` is also a STRING identifier.

Boundary IDs must **always** be treated as strings in SQL.

**Correct:**

neighborhood_id = '5'
police_district_id = '3'
boundary_id = '5'

**Incorrect:**

neighborhood_id = 5
police_district_id = 3
boundary_id = 5

Do not compare STRING boundary identifiers to INT64 literals.

### gold_boundaries

- `gold_boundaries` contains both neighborhood and police-district dimension records.
  - `boundary_type` identifies the boundary system.
  - Expected values are:
    - 'neighborhoods'
    - 'police_districts'
  - Use `boundary_name` for the human-readable boundary name.
  - Use `boundary_id` to join to the corresponding foreign key in `gold_dashboard_events` or `gold_rental_listings`.

**Neighborhood joins**

- To retrieve neighborhood names:

    FROM gold_dashboard_events AS e
    JOIN gold_boundaries AS b
      ON e.neighborhood_id = b.boundary_id
    AND b.boundary_type = 'neighborhoods'
    Police-district joins

- To retrieve police-district names:

    FROM gold_dashboard_events AS e
    JOIN gold_boundaries AS b
      ON e.police_district_id = b.boundary_id
    AND b.boundary_type = 'police_districts'

- Do not perform point-in-polygon or other spatial joins for normal analytical questions. Boundary membership has already been assigned upstream.
- Some boundary rows intentionally have NULL geometry to represent unknown/unlocated events. These rows are valid analytical dimension members.
- Do not discard them from counts merely because geometry is NULL.
- For map or geometry-specific queries, rows with NULL geometry should not be rendered.

## Standard analytical patterns
- **"Top N neighborhoods by X"** → For a question such as "Which neighborhoods have the most police incidents?"
  - Filter gold_dashboard_events to the relevant event_type.
  - Group by neighborhood_id.
  - Aggregate the requested metric.
  - Join to gold_boundaries using:
    boundary_type = 'neighborhoods'.
  - Order by the metric descending.
  - Apply the requested limit.
- Do not group by boundary_name before establishing the correct boundary-ID join.

- **"Over the last N days"** → filter on:
`event_date_pacific` >= DATE_SUB(CURRENT_DATE("America/Los_Angeles"), INTERVAL N DAY)`

## Data-quality caveats
- The most recent 2-3 days of any incident-source data (police, 311)
  typically under-report because agencies file their reports with a lag.
  When the question is about the "most recent day" or a "trend", either
  include this caveat in the answer or exclude the last 48 hours.

---
