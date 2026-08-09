{{ config(
    materialized='table'
)
}}

SELECT
    'neighborhoods' as boundary_type,
    boundary_id,
    boundary_name,
    geometry,
    is_unlocated
FROM {{ ref('stage_neighborhoods') }}

union all

SELECT
    'police_districts' as boundary_type,
    boundary_id,
    boundary_name,
    geometry,
    is_unlocated
FROM {{ ref('stage_police_districts') }}