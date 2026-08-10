SELECT
    rentcast_id,
    listed_date,
    removed_date,
    cast(neighborhood_id as string) as neighborhood_id,
    cast(police_district_id as string) as police_district_id,
    property_type,
    beds,
    baths,
    square_footage,
    price,
    formatted_address,
    SAFE_CAST(lat AS FLOAT64) AS lat,
    SAFE_CAST(long AS FLOAT64) AS long,
    case
        when SAFE_CAST(lat AS FLOAT64) between -90 and 90
            and SAFE_CAST(long AS FLOAT64) between -180 and 180
        then ST_GEOGPOINT(
            SAFE_CAST(long AS FLOAT64),
            SAFE_CAST(lat AS FLOAT64)
        )
        else NULL
    end AS geometry
FROM {{ source('livability', 'rental_listings') }}