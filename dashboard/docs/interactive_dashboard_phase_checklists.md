# Interactive Dashboard Phase Checklists

Use this file as the execution checklist for an implementation agent. Do not begin a phase until all prior phase checks are complete.

## Phase 0 - Scope and Definitions
- [ ] Confirm single-select map behavior and deselection behavior
- [ ] Confirm date filter UX (range picker and default full-dataset mode)
- [ ] Lock timezone policy:
  - [ ] Warehouse filtering uses UTC timestamps
  - [ ] UI display and controls use Pacific Time (`America/Los_Angeles`)
- [ ] Confirm timestamp columns:
  - [ ] 311 -> `requested_datetime`
  - [ ] Police -> `report_datetime`
  - [ ] Transit -> `arrival_time_predicted`
- [ ] Confirm transit average delay metric uses `arrival_delay_sec`
- [ ] Confirm how null categories should appear in histograms
- [ ] Lock null boundary-id policy: exclude null ids from boundary aggregates
- [ ] Lock boundary logic policy: "in boundary" means matching boundary id only
- [ ] Lock mode toggle behavior: reset selected boundary on toggle

## Phase 1 - BigQuery Query Layer
- [ ] Build one shared query interface for each metric block
- [ ] Add safe parameterization for boundary id and time range
- [ ] Implement one shared UTC date filter helper used by every query
- [ ] Implement 311 total + grouped `service_name`
- [ ] Implement police total + grouped `incident_category`
- [ ] Implement transit totals with `trip_stops` joined to `stops`
- [ ] Add tests or validation SQL for at least one known boundary id
- [ ] Verify that all queries handle null boundary ids safely
- [ ] Verify null boundary-id rows are excluded from aggregate stats

## Phase 2 - Boundaries and Geometry
- [ ] Load both `neighborhoods` and `police_districts`
- [ ] Parse WKT geometry strings and convert to GeoJSON for map rendering
- [ ] Ensure feature ids map exactly to warehouse ids
- [ ] Validate map renders full SF extent correctly
- [ ] Validate click event yields selected boundary id and name

## Phase 3 - Dash Layout and State
- [ ] Create top-level layout with controls, map, KPIs, and histograms
- [ ] Add mode toggle control
- [ ] Add date range control with full-range default
- [ ] Implement shared app state for mode, selected boundary, date range
- [ ] Add placeholder states before first selection

## Phase 4 - Callback Wiring
- [ ] Mode toggle updates map layer and resets selected boundary
- [ ] Map click updates active boundary state
- [ ] Date change re-runs metric queries
- [ ] Date controls display Pacific Time while query filters are UTC
- [ ] KPI cards refresh with each valid interaction
- [ ] Histograms refresh with each valid interaction
- [ ] Loading indicators appear during query execution
- [ ] Errors are caught and shown with user-friendly messages

## Phase 5 - Performance and Robustness
- [ ] Add cache key strategy for repeated interactions
- [ ] Measure query latency for frequent click patterns
- [ ] Reduce duplicate queries between cards/charts
- [ ] Add retry for transient BigQuery failures
- [ ] Log query durations and error counts

## Phase 6 - Validation and Release
- [ ] Compare dashboard results vs direct SQL for 3+ sample boundaries
- [ ] Verify behavior for no data in selected window
- [ ] Verify both boundary modes are fully functional
- [ ] Verify timezone behavior is documented and consistent
- [ ] Document setup (`.env`, credentials, project, dataset)
- [ ] Publish known limitations and follow-up backlog

## Completion Gate
- [ ] Every phase checklist complete
- [ ] No unresolved P1 data correctness concerns
- [ ] Stakeholder sign-off on interaction behavior
