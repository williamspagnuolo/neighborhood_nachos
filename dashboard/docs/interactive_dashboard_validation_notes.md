# Interactive Dashboard Validation Notes

Date: 2026-06-28

## What was validated in implementation

- Locked time semantics implemented:
  - Pacific UI date range converted to UTC query bounds.
  - Inclusive UI end date converted to exclusive UTC upper bound.
- Locked boundary semantics implemented:
  - ID-based boundary filtering only.
  - Null boundary IDs excluded in all boundary aggregate queries.
- Locked geometry semantics implemented:
  - WKT conversion to GeoJSON done in BigQuery with `SAFE.ST_GEOGFROMTEXT` and `ST_ASGEOJSON`.
- Locked interaction behavior implemented:
  - Mode toggle clears selected boundary state.
- Required metrics implemented:
  - 311 total + service histogram.
  - Police total + incident category histogram.
  - Transit arrivals total + `AVG(arrival_delay_sec)`.

## Manual validation steps to run against live BigQuery

1. Launch app and pick a neighborhood.
2. Capture KPI values and histogram top categories for a narrow date window.
3. Run equivalent SQL directly in BigQuery and compare counts.
4. Repeat for:
   - One additional neighborhood
   - One police district

## Example parity SQL (311 total)

```sql
SELECT COUNT(*) AS incidents_311_total
FROM `PROJECT.DATASET.311_incidents`
WHERE neighborhood_id IS NOT NULL
  AND CAST(neighborhood_id AS STRING) = 'BOUNDARY_ID'
  AND requested_datetime >= TIMESTAMP('2026-01-01T08:00:00Z')
  AND requested_datetime < TIMESTAMP('2026-01-08T08:00:00Z');
```

## Example parity SQL (police histogram)

```sql
SELECT
  COALESCE(NULLIF(TRIM(incident_category), ''), 'Unknown') AS category,
  COUNT(*) AS category_count
FROM `PROJECT.DATASET.police_incidents`
WHERE police_district_id IS NOT NULL
  AND CAST(police_district_id AS STRING) = 'BOUNDARY_ID'
  AND report_datetime >= TIMESTAMP('2026-01-01T08:00:00Z')
  AND report_datetime < TIMESTAMP('2026-01-08T08:00:00Z')
GROUP BY category
ORDER BY category_count DESC, category ASC;
```

## Example parity SQL (transit totals)

```sql
SELECT
  COUNT(*) AS transit_arrivals_total,
  AVG(t.arrival_delay_sec) AS transit_avg_delay_sec
FROM `PROJECT.DATASET.trip_stops` t
JOIN `PROJECT.DATASET.stops` s
  ON t.stop_id = s.stop_id
WHERE s.neighborhood_id IS NOT NULL
  AND CAST(s.neighborhood_id AS STRING) = 'BOUNDARY_ID'
  AND t.arrival_time_predicted >= TIMESTAMP('2026-01-01T08:00:00Z')
  AND t.arrival_time_predicted < TIMESTAMP('2026-01-08T08:00:00Z');
```

## Unresolved risks

- Full metric parity for 3+ sample boundaries still requires execution with production credentials.
- Transit joins may overcount if duplicate `stop_id` values are shared across agencies.
