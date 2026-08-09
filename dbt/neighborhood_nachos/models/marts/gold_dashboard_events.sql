{{ config(
        materialized='table',
        partition_by={
                "field": "event_date_pacific",
                "data_type": "date"
        },
        cluster_by=[
                "event_type",
                "neighborhood_id",
                "police_district_id"
        ]
    )
}}

with events_311 as (
    SELECT
        concat('311:', incident_id) as event_id,
        '311' as event_type,
        event_ts_utc,
        date(
            event_ts_utc,
            'America/Los_Angeles'
        ) as event_date_pacific,
        time(
            event_ts_utc,
            'America/Los_Angeles'
        ) as event_time_pacific,
        neighborhood_id,
        police_district_id,
        category,
        cast(null as float64) as arrival_delay_sec,
        cast(null as bool) as delayed_over_5_min
    FROM {{ ref('stage_311_incidents') }}
),
police as (
    SELECT
        concat('police:', incident_id) as event_id,
        'police' as event_type,
        event_ts_utc,
        date(
            event_ts_utc,
            'America/Los_Angeles'
        ) as event_date_pacific,
        time(
            event_ts_utc,
            'America/Los_Angeles'
        ) as event_time_pacific,
        neighborhood_id,
        police_district_id,
        category,
        cast(null as float64) as arrival_delay_sec,
        cast(null as bool) as delayed_over_5_min
    FROM {{ ref('stage_police_incidents') }}

),
transit as (

    SELECT
        concat('transit:', trip_stop_id) as event_id,
        'transit' as event_type,
        event_ts_utc,
        date(
            event_ts_utc,
            'America/Los_Angeles'
        ) as event_date_pacific,
        time(
            event_ts_utc,
            'America/Los_Angeles'
        ) as event_time_pacific,
        neighborhood_id,
        police_district_id,
        cast(null as string) as category,
        arrival_delay_sec,
        arrival_delay_sec > 300 as delayed_over_5_min

    FROM {{ ref('int_transit_arrivals') }}

)

select * from events_311
    union all
select * from police
    union all
select * from transit
