import argparse
import datetime as dt
import os
from collections.abc import Iterable


ALLOWED_AGENCIES = ("muni", "bart")


def env_value(name: str, default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def env_int(name: str, default: int) -> int:
    value = env_value(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer; got {value!r}.") from exc


def env_bool(name: str, default: bool = False) -> bool:
    value = env_value(name)
    if not value:
        return default
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{name} must be one of true/false, 1/0, yes/no, or on/off; got {value!r}."
    )


def default_source_date_utc(today: dt.date | None = None) -> str:
    utc_today = today or dt.datetime.now(dt.timezone.utc).date()
    return (utc_today - dt.timedelta(days=1)).isoformat()


def validate_agency(value: str) -> str:
    agency = value.strip().lower()
    if agency not in ALLOWED_AGENCIES:
        allowed = ", ".join(ALLOWED_AGENCIES)
        raise ValueError(f"agency must be one of: {allowed}; got {value!r}.")
    return agency


def validate_source_date(value: str, today: dt.date | None = None) -> str:
    source_date = value.strip()
    try:
        parsed = dt.date.fromisoformat(source_date)
    except ValueError as exc:
        raise ValueError(
            f"source date must be a real YYYY-MM-DD date; got {value!r}."
        ) from exc

    utc_today = today or dt.datetime.now(dt.timezone.utc).date()
    if parsed >= utc_today:
        raise ValueError(
            f"source date must be earlier than today in UTC ({utc_today}); got {value!r}."
        )
    return parsed.isoformat()


def validate_job_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    required: Iterable[tuple[str, str]],
) -> argparse.Namespace:
    try:
        args.agency = validate_agency(args.agency)
        args.service_date = validate_source_date(args.service_date)
        missing = [
            flag
            for attribute, flag in required
            if not getattr(args, attribute).strip()
        ]
        if missing:
            raise ValueError(f"missing required configuration: {', '.join(missing)}")
    except ValueError as exc:
        parser.error(str(exc))
    return args
