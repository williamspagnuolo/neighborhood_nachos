WITH rentals as (
    SELECT *
    FROM {{ ref('stage_rental_listings') }}
),
deduplicated as (
    SELECT *
    FROM rentals
    WHERE price > 0
    QUALIFY row_number() over (
        partition by rentcast_id, listed_date
        order by removed_date desc
    ) = 1
)
SELECT
    rentcast_id,
    listed_date,
    removed_date,
    neighborhood_id,
    police_district_id,
    property_type,
    beds,
    case 
        when beds = 0 then 'studio'
        when beds = 1 then '1bd'
        when beds = 2 then '2bd'
        when beds = 3 then '3bd'
        when beds >= 4 then '4bd+'
        else 'unknown'
    end as bedroom_bucket,
    baths,
    square_footage,
    price,
    formatted_address,
    lat,
    long,
    geometry
FROM deduplicated