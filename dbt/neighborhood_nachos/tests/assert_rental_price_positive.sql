SELECT *
FROM {{ ref('gold_rental_listings') }}
WHERE price <= 0