"""Small shared helpers for the approved logical transit-row identity."""

import pandas as pd


CANONICAL_ROW_KEY = [
    "agency_id",
    "trip_id",
    "trip_start_date",
    "trip_start_time",
    "direction_id",
    "stop_sequence",
]


def require_columns(df: pd.DataFrame, columns: list[str], context: str) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(f"{context} is missing required columns: {missing}")


def assert_unique_canonical_keys(df: pd.DataFrame, context: str) -> None:
    """Raise when logical rows repeat, treating null key values as equal."""
    require_columns(df, CANONICAL_ROW_KEY, context)
    duplicate_count = int(df.duplicated(subset=CANONICAL_ROW_KEY, keep=False).sum())
    if duplicate_count:
        raise ValueError(
            f"{context} contains {duplicate_count:,} row(s) with duplicate "
            f"canonical keys: {CANONICAL_ROW_KEY}"
        )
