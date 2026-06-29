# Dashboard Risks, Gaps, and Open Questions

## Overall Assessment
The dashboard concept is strong and feasible. The requested metrics align well with the available schema. The highest risk is not feature complexity, but correctness/performance under interactive filtering, especially for transit joins and geometry rendering.

## Decisions Already Locked
- Time policy: filter in UTC; display/control time in Pacific Time (`America/Los_Angeles`).
- Boundary logic: records are "in boundary" by matching boundary id only.
- Geometry role: geometry is only for map drawing and click interaction.
- Transit delay metric: `AVG(arrival_delay_sec)`.
- Null boundary ids: exclude from aggregated boundary stats.
- Mode toggle behavior: reset selected boundary.

## Key Risks and Why They Matter

### 1) Geometry Parsing Implementation Risk
`neighborhoods.geometry` and `police_districts.geometry` are stored as `STRING`, not `GEOGRAPHY`.  
Risk: map rendering fails unless WKT strings are parsed and converted to GeoJSON consistently.

**Mitigation**
- Parse WKT with `ST_GEOGFROMTEXT` and convert using `ST_ASGEOJSON`.
- Standardize this in one utility/view used by both boundary layers.
- Add geometry validation test on app startup.

### 2) Transit Query Cost and Latency
Transit metrics require joining `trip_stops` and `stops` on every interaction.  
Risk: slow UI response and higher BigQuery cost.

**Mitigation**
- Cache query results keyed by filter state.
- Consider a pre-aggregated table/materialized view for `(boundary_id, date)` rollups.
- Push filters (time + boundary) as early as possible in SQL.

### 3) Timestamp/Timezone Ambiguity
Your metrics rely on three different timestamp fields across three systems.  
Risk: inconsistent date window filtering and hard-to-explain discrepancies.

**Mitigation**
- Use locked policy: SQL filters in UTC, UI display in Pacific Time.
- Document inclusive/exclusive range boundaries.
- Use one shared date filter helper across all SQL queries.

### 4) Boundary Membership Completeness
Some records can have null `neighborhood_id` or `police_district_id`.  
Risk: counts may look lower than expected after selecting boundaries.

**Mitigation**
- Use locked policy: exclude null-boundary rows from boundary-level aggregates.
- Show metadata note in UI for users: "Only records mapped to this boundary are counted."

### 5) Histogram Cardinality and Readability
`service_name` and `incident_category` can contain many categories.  
Risk: unreadable charts and expensive grouping.

**Mitigation**
- Show top N categories plus an "Other" bucket.
- Add deterministic category sorting.
- Consider optional search/filter on category in future iteration.

### 6) Selection and Toggle UX Edge Cases
User can click a boundary in one mode, then switch modes.  
Risk: stale or invalid selection state drives confusing outputs.

**Mitigation**
- Use locked policy: clear selection on every mode switch.
- Show clear prompt: "Select a neighborhood/police district to view metrics."

## Things Potentially Overlooked
- Authentication/credentials flow for BigQuery in local and deployed environments
- BigQuery query quotas and cost guardrails for interactive usage
- Empty-state behavior (no records in chosen date range)
- Accessibility and legibility of map colors for two boundary modes
- Automated validation checks to compare UI metrics to direct SQL
- Deployment target constraints (Dash server runtime, scaling, secrets management)

## Remaining Pre-Build Decisions
- Set acceptable latency target per interaction (e.g., under 2-3 seconds)
- Choose caching strategy (in-process cache for MVP vs Redis/external cache for production)
- Confirm whether transit delay should be shown as signed mean or paired with on-time/late percentages

## Open Questions for Product/Engineering Alignment
1. Should date filtering be by date only or full timestamp granularity in UI controls?
2. Should category histograms include null/unknown explicitly as a visible bar?
3. Is the expected deployment local-only, classroom demo, or cloud-hosted?
4. Do we need cross-filtering beyond map click (for example, clicking histogram bars)?

## Geometry Parsing Best Practice (for this project)
Given your WKT strings (for example `MULTIPOLYGON (...)`), use this pipeline:
1. Parse WKT to BigQuery geography with `SAFE.ST_GEOGFROMTEXT(geometry)`.
2. Convert geography to GeoJSON text with `ST_ASGEOJSON(...)`.
3. Load that GeoJSON into Dash map layers.

This is preferred over parsing WKT in Python because BigQuery functions are robust and keep transformation logic centralized.

## Reasonableness Check
This is a reasonable and achievable dashboard scope for an MVP if you:
- Keep selection single-boundary
- Add basic caching
- Validate geometry and timezone behavior early
- Delay advanced interactions (cross-filtering, comparative views) to a later phase
