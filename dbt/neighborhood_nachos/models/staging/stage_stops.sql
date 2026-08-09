SELECT
    stop_id,
    cast(neighborhood_id as string) as neighborhood_id,
    cast(police_district_id as string) as police_district_id
FROM {{ source('livability', 'stops') }}