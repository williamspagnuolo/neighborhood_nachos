SELECT
    trip_stop_id,
    stop_id,
    arrival_time_predicted as event_ts_utc,
    arrival_delay_sec
FROM {{ source('livability', 'trip_stops') }}
WHERE arrival_time_predicted is not null
