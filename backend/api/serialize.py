"""Model -> JSON helpers. Enums become their string values, dates
YYYY-MM-DD, datetimes ISO 8601 in UTC."""
import enum
from datetime import date, datetime, timezone
from pathlib import PurePath, PurePosixPath
from typing import Any

from flask.json.provider import DefaultJSONProvider

from models import Antibody, Chain, Liability, PipelineRun, Signal, Trial
from pipeline.analyze import find_cdr3



def iso(value: date | datetime | None) -> str | None:
    """Datetimes are converted to UTC whatever the Postgres session time
    zone is (a native Mac Postgres often defaults to local time)."""
    if value is None:
        return None
    if isinstance(value, datetime) and value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def enum_value(value: enum.Enum | str | None) -> str | None:
    return value.value if isinstance(value, enum.Enum) else value


class ApiJSONProvider(DefaultJSONProvider):
    """Safety net: Flask's default renders datetimes as RFC 822
    ("Wed, 14 Oct 2026 ..."). Anything that slips past the serializers
    still comes out as ISO, and enums as their values."""

    sort_keys = False  # keep fields in the order the serializers build them

    @staticmethod
    def default(o: Any) -> Any:
        if isinstance(o, (datetime, date)):
            return iso(o)
        if isinstance(o, enum.Enum):
            return o.value
        return DefaultJSONProvider.default(o)


# --- antibodies ---------------------------------------------------------------

def antibody_summary(ab: Antibody) -> dict[str, Any]:
    """The fields a landscape card needs."""
    return {
        "id": ab.id,
        "inn_name": ab.inn_name,
        "display_name": ab.display_name,
        "targets": list(ab.targets or []),
        "format": ab.format,
        "clinical_stage": ab.clinical_stage,
        "stage_rank": ab.stage_rank,
        "status": ab.status,
    }


def antibody(ab: Antibody) -> dict[str, Any]:
    """The detail page header: the summary plus provenance."""
    return {
        **antibody_summary(ab),
        "target": ab.target,
        "source_url": ab.source_url,
        "created_at": iso(ab.created_at),
        "updated_at": iso(ab.updated_at),
    }


def liability(li: Liability) -> dict[str, Any]:
    return {
        "id": li.id,
        "kind": enum_value(li.kind),
        "motif": li.motif,
        "position": li.position,  # 1-based
        "in_cdr3": li.in_cdr3,
        "severity": enum_value(li.severity),
    }


def chain(ch: Chain) -> dict[str, Any]:
    # CDR3 bounds come from the same regex the analyze step used, so the
    # dashboard's highlight agrees with each liability's in_cdr3 flag.
    cdr3 = find_cdr3(ch.sequence, ch.chain_type) if ch.cdr3_sequence else None
    liabilities = sorted(ch.liabilities, key=lambda li: (li.position, li.id))
    by_severity = {"high": 0, "medium": 0, "low": 0}
    for li in liabilities:
        by_severity[enum_value(li.severity)] += 1
    return {
        "id": ch.id,
        "chain_type": enum_value(ch.chain_type),  # "H" / "L"
        "chain_index": ch.chain_index,
        "length": ch.length,
        "sequence": ch.sequence,
        "cdr3_sequence": ch.cdr3_sequence,
        "cdr3_len": ch.cdr3_len,
        "cdr3_start": cdr3.start + 1 if cdr3 else None,  # 1-based, inclusive
        "cdr3_end": cdr3.end if cdr3 else None,          # 1-based, inclusive
        "liability_counts": {
            **by_severity,
            "in_cdr3": sum(li.in_cdr3 for li in liabilities),
            "total": len(liabilities),
        },
        "liabilities": [liability(li) for li in liabilities],
    }


# --- trials -------------------------------------------------------------------

def trial(t: Trial) -> dict[str, Any]:
    return {
        "id": t.id,
        "nct_id": t.nct_id,
        "title": t.title,
        "sponsor": t.sponsor,
        "status": t.status,
        "phase": t.phase,
        "enrollment": t.enrollment,
        "last_update_date": iso(t.last_update_date),
        "source_url": t.source_url,
    }

# --- signals ------------------------------------------------------------------

def signal(s: Signal, entity: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": s.id,
        "run_id": s.run_id,
        "kind": enum_value(s.kind),
        "title": s.title,
        "old_value": s.old_value,
        "new_value": s.new_value,
        "source_url": s.source_url,
        "entity_type": enum_value(s.entity_type),
        "entity_id": s.entity_id,
        # Resolved entity: a label + the antibodies to link to (None if it no longer exists).
        "entity": entity,
        "seen": s.seen,
        "seen_at": iso(s.seen_at),
        "created_at": iso(s.created_at),
    }

def public_path(raw: str | None) -> str | None:
    """Drop the machine-specific prefix from a pipeline path:
    '/Users/me/.../data/raw/5' -> 'data/raw/5'. Never expose local paths."""
    if not raw:
        return raw
    parts = PurePath(raw).parts
    if "data" in parts:
        return str(PurePosixPath(*parts[parts.index("data"):]))
    return PurePath(raw).name

# --- pipeline runs ------------------------------------------------------------

def run(r: PipelineRun) -> dict[str, Any]:
    duration = r.duration_seconds  # None while the run is still running
    return {
        "id": r.id,
        "status": enum_value(r.status),
        "started_at": iso(r.started_at),
        "finished_at": iso(r.finished_at),
        "duration_seconds": round(duration, 2) if duration is not None else None,
        "counts": r.counts or {},
        "raw_location": public_path(r.raw_location),
        "error": r.error,
    }