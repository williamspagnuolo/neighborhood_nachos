from typing import Any

from transit_job_config import validate_agency, validate_source_date


DERIVED_ROOT_SUFFIXES = (
    "latest/TripUpdates",
    "latest/VehiclePositions",
    "latest/joined",
)
UNSAFE_PATH_CHARACTERS = frozenset("*?[]{}")


def derived_date_prefix(root: str, agency: str, source_date: str) -> str:
    normalized_root = root.strip().strip("/")
    if not normalized_root:
        raise ValueError("derived root must not be empty")
    if "://" in normalized_root or any(
        character in normalized_root for character in UNSAFE_PATH_CHARACTERS
    ):
        raise ValueError(f"unsafe derived root: {root!r}")
    root_parts = normalized_root.split("/")
    if any(part in {"", ".", ".."} for part in root_parts):
        raise ValueError(f"unsafe derived root: {root!r}")
    if any(part.lower() == "raw" for part in root_parts):
        raise ValueError(f"raw paths are not valid derived roots: {root!r}")
    if not any(
        normalized_root == suffix or normalized_root.endswith(f"/{suffix}")
        for suffix in DERIVED_ROOT_SUFFIXES
    ):
        allowed = ", ".join(DERIVED_ROOT_SUFFIXES)
        raise ValueError(
            f"derived root must equal or end with an allowed root ({allowed}); "
            f"got {root!r}"
        )

    validated_agency = validate_agency(agency)
    validated_date = validate_source_date(source_date)
    return f"{normalized_root}/{validated_agency}/{validated_date}/"


def clear_derived_date_prefix(
    storage_client: Any,
    bucket_name: str,
    root: str,
    agency: str,
    source_date: str,
) -> tuple[str, int]:
    """Delete only one validated derived stage/agency/date prefix."""
    prefix = derived_date_prefix(root, agency, source_date)
    blobs = list(storage_client.list_blobs(bucket_name, prefix=prefix))
    deleted = 0
    for blob in blobs:
        if not blob.name.startswith(prefix):
            raise RuntimeError(
                f"refusing to delete object outside exact prefix {prefix!r}: "
                f"{blob.name!r}"
            )
        generation = getattr(blob, "generation", None)
        if generation is None:
            blob.delete()
        else:
            blob.delete(if_generation_match=generation)
        deleted += 1
    return prefix, deleted
