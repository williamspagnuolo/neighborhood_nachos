# Interactive San Francisco Dashboard Agent Plan

## Objective
Build a Plotly Dash application backed by BigQuery that lets users:
- Toggle map boundary mode between `neighborhoods` and `police_districts`
- Select a date/time window (default = full data range)
- Click one boundary and view aggregated 311, police, and transit metrics for that selected boundary

## Success Criteria
- Boundary toggle updates both map layer and aggregation key (`neighborhood_id` vs `police_district_id`)
- Time filter applies consistently to each dataset with correct timestamp columns
- Clicking a polygon updates KPI cards and histograms without page reload
- Queries return in acceptable time for interactive usage (target: under 2-3 seconds for most interactions)
- App is deployable with environment-based configuration

## Locked Product Decisions (Do Not Reopen During MVP Build)
- All warehouse timestamps are treated as UTC for filtering logic.
- User-facing time controls and labels are displayed in Pacific Time (`America/Los_Angeles`).
- Boundary inclusion is ID-based only:
  - Neighborhood mode filters by matching `neighborhood_id`
  - Police mode filters by matching `police_district_id`
- Boundary geometries are for map display and click interaction only (not spatial point-in-polygon computation for MVP).
- All records with null boundary ids are excluded from aggregated boundary stats.
- Transit delay metric is `AVG(arrival_delay_sec)` over filtered rows.
- Selection resets on boundary mode toggle.

## Assumed BigQuery Tables
- `311_incidents` (time: `requested_datetime`)
- `police_incidents` (time: `report_datetime`)
- `trip_stops` (time: `arrival_time_predicted`)
- `stops` (join key for transit: `stop_id`; boundary ids are in `stops`)
- `neighborhoods`, `police_districts` (boundary geometry + names)

---

## Phase 0 - Scope Lock and Contract
### Tasks
- Define exact dashboard interaction contract:
  - Single-select boundary via map click
  - Reset selection on mode toggle
  - Date filter component type (range picker, presets, or both)
- Define timezone contract:
  - SQL filtering logic on UTC timestamps
  - UI controls/labels rendered in Pacific Time (`America/Los_Angeles`)
- Confirm metric definitions:
  - 311 total count and service histogram
  - Police total count and category histogram
  - Transit total stop arrivals and average `arrival_delay_sec`
- Confirm null handling policy:
  - Exclude rows with null boundary ids from boundary-level aggregates

### Deliverables
- 1-page product contract in markdown
- List of accepted assumptions and explicit non-goals

### Checkpoint (must pass before Phase 1)
- No ambiguous KPI definitions remain
- No unresolved ambiguity about timestamp semantics or timezone policy

---

## Phase 1 - Data Access Layer and Query Design
### Tasks
- Create a BigQuery access module with parameterized query functions.
- Implement boundary-aware query patterns:
  - If mode = neighborhoods, filter by `neighborhood_id`
  - If mode = police districts, filter by `police_district_id`
  - Exclude null boundary ids in all boundary-level metric queries
- Implement one shared UTC time filter helper used by all three data domains.
- Transit query design:
  - Join `trip_stops.stop_id = stops.stop_id`
  - Filter by selected boundary id from `stops`
  - Aggregate `COUNT(*)` arrivals and `AVG(arrival_delay_sec)` delay
- Build histogram queries:
  - 311 grouped by `service_name`
  - Police grouped by `incident_category`
- Add explicit handling for null categories (bucket into `Unknown`)

### Deliverables
- Query module with unit-testable SQL builders
- Query templates documented with expected inputs/outputs

### Checkpoint
- Sanity query samples run successfully in BigQuery
- Returned schema exactly matches UI needs (no extra post-processing assumptions)

---

## Phase 2 - Geometry/Map Data Preparation
### Tasks
- Build boundary loader for `neighborhoods` and `police_districts`.
- Convert stored WKT geometry strings into map-renderable GeoJSON features.
- Attach stable feature ids (`id`) and labels (`name`) for click mapping.
- Validate geometry integrity and coordinate reference assumptions.

### Deliverables
- Reusable function: `load_boundaries(mode) -> GeoJSON + lookup table`
- Mapping from map click payload to boundary id/name

### Checkpoint
- Both boundary layers render correctly on SF map
- Clicking each polygon yields the expected boundary id

---

## Phase 3 - Dash App Skeleton and State Model
### Tasks
- Create Dash layout sections:
  - Header/title
  - Controls (mode toggle + date range selector)
  - Map component
  - KPI cards
  - Two histograms (311 + police)
- Define shared state model:
  - Selected mode
  - Selected boundary id/name
  - Date range
- Implement callback wiring plan before writing callback code.

### Deliverables
- Running Dash app skeleton with placeholder data
- Callback dependency diagram documented in code comments or markdown

### Checkpoint
- App starts cleanly
- UI controls update local state without query execution errors

---

## Phase 4 - Interactive Callback Implementation
### Tasks
- Implement map click callback to set active boundary.
- Implement mode toggle callback:
  - Swap boundary layer
  - Always reset selection state on toggle
- Implement date filter callback with default full range behavior.
- Ensure date control shows Pacific Time while backend query parameters are UTC.
- Implement metric callbacks with shared query helpers.
- Return empty-state UI if no boundary is selected.

### Deliverables
- Fully interactive dashboard with live BigQuery-backed metrics
- Consistent loading and error states on each chart/card

### Checkpoint
- Manual test matrix passes:
  - Toggle mode -> click region -> metrics update
  - Change date range -> all KPI/histograms update
  - No selection -> graceful placeholder content

---

## Phase 5 - Performance and Reliability Hardening
### Tasks
- Add query result caching by `(mode, boundary_id, start_ts, end_ts)`.
- Reduce query volume by consolidating metrics where practical.
- Add retry/backoff for transient BigQuery failures.
- Add structured logging for callback timing and query failures.

### Deliverables
- Performance tuning notes with before/after timings
- Basic operational telemetry hooks

### Checkpoint
- Interaction latency consistently acceptable
- Failure scenarios show actionable user-facing messages

---

## Phase 6 - Validation, Documentation, and Deployment
### Tasks
- Validate metrics against direct SQL checks for sample boundaries.
- Add test scripts for query correctness and callback behavior.
- Document run instructions, environment variables, and deployment approach.
- Prepare release checklist and known limitations.

### Deliverables
- README for dashboard app
- Test checklist with validation evidence
- Deployment notes (local + hosted target)

### Final Go/No-Go Check
- Metric parity checks pass for sample slices
- All key interactions validated end-to-end
- Known risks documented with mitigations
