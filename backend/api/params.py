"""Query-string parsing. Every helper raises BadRequest (400) with the
parameter name and, where it helps, the allowed values, so views never
handle bad input themselves."""
import enum
from datetime import date, datetime, time, timezone
from typing import TypeVar

from flask import request

from api.errors import BadRequest

E = TypeVar("E", bound=enum.Enum)

TRUE = {"true", "1", "yes"}
FALSE = {"false", "0", "no"}


def str_param(name: str) -> str | None:
    """The parameter's value, or None when absent. Present-but-empty
    (?target=) is a 400, not "no filter"."""
    if name not in request.args:
        return None
    value = request.args.get(name, "").strip()
    if value == "":
        raise BadRequest(f"Query parameter '{name}' is empty")
    return value


def int_param(name: str, default: int | None = None, *, min_value: int | None = None,
              max_value: int | None = None) -> int | None:
    raw = str_param(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise BadRequest(f"'{name}' must be an integer, got {raw!r}") from None
    too_low = min_value is not None and value < min_value
    too_high = max_value is not None and value > max_value
    if too_low or too_high:
        bounds = {k: v for k, v in (("min", min_value), ("max", max_value)) if v is not None}
        rule = " and ".join(f">= {min_value}" if k == "min" else f"<= {max_value}" for k in bounds)
        raise BadRequest(f"'{name}' must be {rule}, got {value}", bounds)
    return value


def bool_param(name: str) -> bool | None:
    raw = str_param(name)
    if raw is None:
        return None
    if raw.lower() in TRUE:
        return True
    if raw.lower() in FALSE:
        return False
    raise BadRequest(f"'{name}' must be true or false, got {raw!r}", {"allowed": ["true", "false"]})


def datetime_param(name: str) -> datetime | None:
    """ISO 8601 date or datetime. Dates mean midnight UTC; naive times are UTC."""
    raw = str_param(name)
    if raw is None:
        return None
    try:
        if len(raw) == 10:
            return datetime.combine(date.fromisoformat(raw), time.min, tzinfo=timezone.utc)
        # A '+' in a query string decodes to a space unless the client encoded it
        # (2026-10-13T09:00:00+00:00 arrives as "...09:00:00 00:00").
        value = datetime.fromisoformat(raw.replace(" ", "+"))
    except ValueError:
        raise BadRequest(
            f"'{name}' must be an ISO 8601 date or datetime, got {raw!r}",
            {"examples": ["2026-10-13", "2026-10-13T09:00:00Z"]},
        ) from None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def enum_list_param(name: str, enum_cls: type[E]) -> list[E] | None:
    """Comma-separated enum values, case-insensitive: kind=NEW_TRIAL,trial_status_changed."""
    raw = str_param(name)
    if raw is None:
        return None
    by_value = {m.value.lower(): m for m in enum_cls}
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    unknown = [p for p in parts if p.lower() not in by_value]
    if unknown or not parts:
        raise BadRequest(
            f"Unknown {name}: {', '.join(unknown) or raw}",
            {"allowed": [m.value for m in enum_cls]},
        )
    return [by_value[p.lower()] for p in parts]