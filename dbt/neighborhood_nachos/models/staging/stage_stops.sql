SELECT
    stop_id,
    neighborhood_id,
    police_district_id
FROM {{ source('livability', 'stops') }}