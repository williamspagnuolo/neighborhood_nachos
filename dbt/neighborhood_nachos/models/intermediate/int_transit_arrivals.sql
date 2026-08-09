SELECT
    t.trip_stop_id,
    t.event_ts_utc,
    t.arrival_delay_sec,
    s.neighborhood_id,
    s.police_district_id
FROM {{ ref('stage_trip_stops') }} t
    left join {{ ref('stage_stops') }} s
        on t.stop_id = s.stop_id