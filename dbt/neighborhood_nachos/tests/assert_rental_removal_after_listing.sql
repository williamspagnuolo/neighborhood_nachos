SELECT *
FROM {{ ref('gold_rental_listings') }}
WHERE removed_date is not NULL
    and removed_date < listed_date