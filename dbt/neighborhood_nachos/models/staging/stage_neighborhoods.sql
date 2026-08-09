SELECT
    cast(id as string) as boundary_id,
    name as boundary_name,
    geometry as geometry_wkt,
    safe.st_geogfromtext(geometry) as geometry,
    geometry is null as is_unlocated

FROM {{ source('livability', 'neighborhoods') }}
WHERE id is not null