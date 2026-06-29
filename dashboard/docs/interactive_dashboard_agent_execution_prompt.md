# Agent Execution Prompt: Build the Interactive SF Dashboard

Copy and paste the prompt below into a coding agent session.

---

You are implementing an interactive Plotly Dash dashboard backed by BigQuery.

## Project Context
- Repo contains planning docs in:
  - `dashboard/docs/interactive_dashboard_agent_plan.md`
  - `dashboard/docs/interactive_dashboard_phase_checklists.md`
  - `dashboard/docs/interactive_dashboard_risks_and_open_questions.md`
- BigQuery schema is in:
  - `dashboard/docs/schema.csv`

## Objective
Build a dashboard map of San Francisco with two boundary modes:
1. Neighborhoods
2. Police districts

When user clicks a boundary polygon, show aggregated metrics for that selected boundary and selected time window.

## Locked Requirements (Do Not Change)
1. Time handling:
   - Source timestamps are UTC.
   - Query filtering is in UTC.
   - UI display and controls are in Pacific Time (`America/Los_Angeles`).
2. Boundary membership:
   - Record is "in boundary" only if boundary id matches (`neighborhood_id` or `police_district_id`).
   - Do not use point-in-polygon for aggregation in MVP.
3. Geometry usage:
   - Geometry is only for map rendering/clicking.
   - Geometry values are WKT strings and must be parsed for map use.
4. Null handling:
   - Exclude null boundary ids from aggregated boundary metrics.
5. Transit metric:
   - Average delay is `AVG(arrival_delay_sec)`.
6. Interaction behavior:
   - Reset selected boundary on mode toggle.

## Required Metrics
For selected boundary + time range:

- 311 (`311_incidents`, timestamp `requested_datetime`)
  - Total incident count
  - Histogram by `service_name`

- Police (`police_incidents`, timestamp `report_datetime`)
  - Total incident count
  - Histogram by `incident_category`

- Transit (`trip_stops` joined to `stops` on `stop_id`, timestamp `arrival_time_predicted`)
  - Total stop arrivals
  - Average `arrival_delay_sec`

## Geometry Parsing Best Practice
Use BigQuery functions to convert WKT -> geography -> GeoJSON:
- `SAFE.ST_GEOGFROMTEXT(geometry)`
- `ST_ASGEOJSON(...)`

Do not parse WKT in ad hoc Python if BigQuery conversion is available.

## Build Order (Sequential Phases)
Follow exactly this phase order. Complete and verify each phase before starting the next.

### Phase 0 - Contract and Setup
- Confirm app structure, target module layout, and runtime dependencies.
- Document environment variables required for BigQuery access.
- Add a brief technical design note in code comments or a local README section.

Exit criteria:
- Clear file/module plan exists.
- All locked requirements are represented in implementation notes.

### Phase 1 - Query/Data Layer
- Create a reusable BigQuery client module.
- Build parameterized query functions for each metric block.
- Implement one shared UTC time filter helper.
- Enforce null-boundary exclusion in boundary aggregates.

Exit criteria:
- Each query function can run independently.
- Returned schema is directly usable by UI callbacks.

### Phase 2 - Boundaries and Map Data
- Build loader for both boundary tables.
- Parse/convert WKT geometry to GeoJSON for map rendering.
- Ensure feature ids map exactly to table `id` and include names.

Exit criteria:
- Both neighborhood and district polygons render.
- Click event provides stable selected boundary id/name.

### Phase 3 - Dash Layout and State
- Implement layout:
  - mode toggle
  - date range control
  - map
  - KPI cards
  - 311 histogram
  - police histogram
- Define app state:
  - mode
  - selected boundary
  - date range

Exit criteria:
- App launches with placeholder/empty state cleanly.
- Controls update state without errors.

### Phase 4 - Interactive Callbacks
- Wire mode toggle callback:
  - swap map layer
  - reset selected boundary
- Wire map click callback to set selection.
- Wire date filter callback:
  - UI in Pacific, query params converted to UTC.
- Wire KPI/chart callbacks using data layer.

Exit criteria:
- Interaction loop works end-to-end:
  - toggle -> click boundary -> metrics load
  - date change -> metrics refresh consistently

### Phase 5 - Reliability and Performance
- Add lightweight caching by `(mode, boundary_id, start_utc, end_utc)`.
- Add graceful error states and loading indicators.
- Add basic logging for query duration and failures.

Exit criteria:
- Common interactions remain responsive.
- Failures are user-readable and logged.

### Phase 6 - Validation and Documentation
- Validate dashboard outputs against direct SQL for at least 3 sample boundaries.
- Validate timezone behavior and null-id exclusion behavior.
- Update README with run instructions and env setup.

Exit criteria:
- Validation checklist complete.
- Known limitations documented.

## Implementation Guidance
- Keep functions small and testable.
- Use parameterized SQL, not string interpolation for raw values.
- Avoid duplicate queries across callbacks where possible.
- For histograms, include a stable ordering and graceful handling of unknown categories.

## Testing Checklist (Minimum)
- Toggle mode resets selection.
- No-boundary-selected state renders correctly.
- Date range applies to all three data domains.
- UTC filter + Pacific display consistency verified.
- Null boundary ids are excluded from boundary aggregates.
- Transit join returns plausible counts/delay for sample boundary.

## Deliverables
1. Working Dash app modules
2. Query/data access modules
3. Updated README with setup instructions
4. Short validation notes (what was checked and sample results)

## Final Output Format
When finished, report:
1. Files created/changed
2. What was implemented by phase
3. Validation results and any unresolved risks

---

End of prompt.
