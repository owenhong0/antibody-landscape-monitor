"""GET /api/landscape: the competitive matrix.

    /api/landscape?target=PD-1   antibodies hitting PD-1, one column per stage
    /api/landscape               counts per target x stage (overview)

Filters on the `targets` array (GIN-indexed, `targets @> ARRAY['PD-1']`), not
the scalar `target` column, so a PD-1 x HER2 bispecific shows up under both.
"""
from collections import defaultdict
from typing import Any

from flask import Blueprint, jsonify
from sqlalchemy import func, select

from api import serialize
from api.database import get_db
from api.errors import BadRequest
from api.params import str_param
from models import Antibody
from pipeline.normalize import TARGET_BUCKETS

bp = Blueprint("landscape", __name__)

# Landscape columns, left to right. stage_rank NULL = unknown, always last.
STAGES: list[dict[str, Any]] = [
    {"stage_rank": 0, "stage": "preclinical", "label": "Preclinical"},
    {"stage_rank": 1, "stage": "phase_1", "label": "Phase 1"},
    {"stage_rank": 2, "stage": "phase_2", "label": "Phase 2"},
    {"stage_rank": 3, "stage": "phase_3", "label": "Phase 3"},
    {"stage_rank": 4, "stage": "filed", "label": "Filed"},
    {"stage_rank": 5, "stage": "approved", "label": "Approved"},
    {"stage_rank": None, "stage": "unknown", "label": "Unknown"},
]
STAGE_BY_RANK = {s["stage_rank"]: s for s in STAGES}
TRACKED_TARGETS = list(TARGET_BUCKETS)  # ["PD-1", "PD-L1", "HER2", "CD20", "IL-23"]


def resolve_target(raw: str) -> str:
    """Case-insensitive match against the tracked targets: 'pd-1' -> 'PD-1'."""
    for target in TRACKED_TARGETS:
        if target.lower() == raw.lower():
            return target
    raise BadRequest(f"Unknown target {raw!r}", {"allowed": TRACKED_TARGETS})


def _rank(rank: int | None) -> int | None:
    # A rank outside 0..5 would be a pipeline bug; show it as unknown rather than drop it.
    return rank if rank in STAGE_BY_RANK else None


@bp.get("/landscape")
def landscape():
    raw = str_param("target")
    if raw is None:
        return jsonify(overview())
    target = resolve_target(raw)

    antibodies = get_db().scalars(
        select(Antibody)
        .where(Antibody.targets.contains([target]))  # targets @> ARRAY['PD-1']
        .order_by(Antibody.display_name)
    ).all()

    cells: dict[int | None, list[Antibody]] = defaultdict(list)
    for ab in antibodies:
        cells[_rank(ab.stage_rank)].append(ab)

    return jsonify(
        target=target,
        total=len(antibodies),
        columns=[
            {
                **stage,
                "count": len(cells[stage["stage_rank"]]),
                "antibodies": [serialize.antibody_summary(ab) for ab in cells[stage["stage_rank"]]],
            }
            for stage in STAGES
        ],
    )


def overview() -> dict[str, Any]:
    db = get_db()
    # One row per (antibody, target): a bispecific becomes two rows.
    per_target = select(
        func.unnest(Antibody.targets).label("target"),
        Antibody.stage_rank.label("stage_rank"),
    ).subquery()
    rows = db.execute(
        select(per_target.c.target, per_target.c.stage_rank, func.count())
        .group_by(per_target.c.target, per_target.c.stage_rank)
    ).all()

    counts: dict[str, dict[str, int]] = {
        t: {s["stage"]: 0 for s in STAGES} for t in TRACKED_TARGETS
    }
    for target, rank, n in rows:
        if target in counts:
            counts[target][STAGE_BY_RANK[_rank(rank)]["stage"]] += n

    # Distinct antibodies: targets && ARRAY[...] (overlaps any tracked target).
    distinct = db.scalar(
        select(func.count()).select_from(Antibody).where(Antibody.targets.overlap(TRACKED_TARGETS))
    )
    return {
        "stages": STAGES,
        "targets": [
            {"target": t, "total": sum(counts[t].values()), "counts": counts[t]}
            for t in TRACKED_TARGETS
        ],
        "antibodies_total": distinct,
    }