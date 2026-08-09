SELECT
    cast(police_incident_id as string) as incident_id,
    cast(neighborhood_id as string) as neighborhood_id,
    cast(police_district_id as string) as police_district_id,
    report_datetime as event_ts_utc,
    coalesce(
        nullif(trim(incident_category), ''),
        'Unknown'
    ) as category
FROM {{ source('livability', 'police_incidents') }}