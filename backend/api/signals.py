"""Signal feed and the "mark reviewed" action.

    GET  /api/signals?since=&kind=&seen=&run_id=&limit=&offset=
    POST /api/signals/<id>/seen
"""
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, jsonify
from sqlalchemy import func, select

from api import serialize
from api.database import get_db
from api.errors import NotFound
from api.params import bool_param, datetime_param, enum_list_param, int_param, str_param
from models import Antibody, EntityType, PipelineRun, Signal, SignalKind, Trial, antibody_trial

bp = Blueprint("signals", __name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _run_id_param() -> int | None:
    """run_id=<int>, or run_id=latest for the newest *finished* run (a run
    still in progress hasn't diffed yet, so it has no signals to show)."""
    raw = str_param("run_id")
    if raw is None:
        return None
    if raw.lower() == "latest":
        latest = get_db().scalar(
            select(func.max(PipelineRun.id)).where(PipelineRun.finished_at.is_not(None))
        )
        return latest if latest is not None else -1  # no finished runs yet: match nothing
    return int_param("run_id", min_value=1)


@bp.get("/signals")
def list_signals():
    since = datetime_param("since")
    kinds = enum_list_param("kind", SignalKind)
    seen = bool_param("seen")
    run_id = _run_id_param()
    limit = int_param("limit", DEFAULT_LIMIT, min_value=1, max_value=MAX_LIMIT)
    offset = int_param("offset", 0, min_value=0)

    filters = []
    if since is not None:
        filters.append(Signal.created_at >= since)
    if kinds:
        filters.append(Signal.kind.in_(kinds))
    if seen is not None:
        filters.append(Signal.seen.is_(seen))
    if run_id is not None:
        filters.append(Signal.run_id == run_id)

    db = get_db()
    total = db.scalar(select(func.count()).select_from(Signal).where(*filters))
    signals = db.scalars(
        select(Signal)
        .where(*filters)
        .order_by(Signal.created_at.desc(), Signal.id.desc())  # id breaks ties within a run
        .limit(limit)
        .offset(offset)
    ).all()

    entities = resolve_entities(signals)
    return jsonify(
        items=[serialize.signal(s, entities.get((s.entity_type, s.entity_id))) for s in signals],
        total=total,
        limit=limit,
        offset=offset,
    )


@bp.post("/signals/<int:signal_id>/seen")
def mark_seen(signal_id: int):
    db = get_db()
    signal = db.get(Signal, signal_id)
    if signal is None:
        raise NotFound(f"Signal {signal_id} not found")
    if not signal.seen:  # idempotent: a second call keeps the first seen_at
        signal.seen = True
        signal.seen_at = datetime.now(timezone.utc)
        db.commit()
    entity = resolve_entities([signal]).get((signal.entity_type, signal.entity_id))
    return jsonify(serialize.signal(signal, entity))


def resolve_entities(signals: list[Signal]) -> dict[tuple[EntityType, int], dict[str, Any]]:
    """Batch-load what each signal points at: a label to show, and the
    antibodies the dashboard can link to. At most three queries per page.

    entity_id is polymorphic (no FK), so a missing entity is simply absent.
    """
    ab_ids = {s.entity_id for s in signals if s.entity_type == EntityType.ANTIBODY}
    trial_ids = {s.entity_id for s in signals if s.entity_type == EntityType.TRIAL}
    db = get_db()
    out: dict[tuple[EntityType, int], dict[str, Any]] = {}

    if ab_ids:
        for ab_id, name in db.execute(
            select(Antibody.id, Antibody.display_name).where(Antibody.id.in_(ab_ids))
        ):
            out[(EntityType.ANTIBODY, ab_id)] = {
                "label": name,
                "antibodies": [{"id": ab_id, "display_name": name}],
            }

    if trial_ids:
        linked: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for trial_id, ab_id, name in db.execute(
            select(antibody_trial.c.trial_id, Antibody.id, Antibody.display_name)
            .join(Antibody, Antibody.id == antibody_trial.c.antibody_id)
            .where(antibody_trial.c.trial_id.in_(trial_ids))
            .order_by(Antibody.display_name)
        ):
            linked[trial_id].append({"id": ab_id, "display_name": name})
        for trial_id, nct_id in db.execute(
            select(Trial.id, Trial.nct_id).where(Trial.id.in_(trial_ids))
        ):
            out[(EntityType.TRIAL, trial_id)] = {"label": nct_id, "antibodies": linked[trial_id]}

    return out