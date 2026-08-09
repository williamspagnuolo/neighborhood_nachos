SELECT
    cast(`311_incident_id` as string) as incident_id,
    neighborhood_id,
    police_district_id,
    requested_datetime as event_ts_utc,
    coalesce(
        nullif(trim(service_name), ''),
        'Unknown'
    ) as category
FROM {{ source('livability', '311_incidents') }}