SELECT
    police_incident_id as incident_id,
    neighborhood_id,
    police_district_id,
    report_datetime as event_ts_utc,
    coalesce(
        nullif(trim(incident_category), ''),
        'Unknown'
    ) as category
FROM {{ source('livability', 'police_incidents') }}