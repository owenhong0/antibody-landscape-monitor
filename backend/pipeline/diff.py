"""Diff step: compare this run's snapshots with earlier ones and emit signals.

How "previous" is chosen
    For each entity, the previous state is its most recent snapshot from any
    EARLIER run, not "the run with id - 1". Runs can skip CT.gov
    (--skip-ctgov) or fail partway, so the previous run may have no trial
    snapshots at all; comparing against it would flag every trial as new.

What fires a signal
    ANTIBODY_STAGE_CHANGED  clinical_stage differs from the previous snapshot
    NEW_ANTIBODY            no earlier snapshot for this antibody
    TRIAL_STATUS_CHANGED    overall status differs (RECRUITING -> COMPLETED)
    TRIAL_PHASE_CHANGED     phase differs (PHASE2 -> PHASE2|PHASE3)
    NEW_TRIAL               no earlier snapshot for this trial (newly tracked)

Rules
    - The first run is a baseline: with no earlier snapshots of a type there
      is nothing to compare against, so it emits no "new" signals for it.
    - Identical content hashes are skipped without comparing fields.
    - Only entities seen in this run are compared. Something missing from a
      run (CT.gov failed, source dropped it) never produces a signal.
    - Every signal carries a source_url (also enforced by a DB constraint).
    - Re-running the diff for the same run replaces that run's signals.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import delete, insert, select

from db import session_scope
from models import (
    Antibody,
    AntibodySnapshot,
    EntityType,
    Signal,
    SignalKind,
    Trial,
    TrialSnapshot,
    antibody_trial,
)

log = logging.getLogger(__name__)

TITLE_MAX = 255


def _title(text: str) -> str:
    return text if len(text) <= TITLE_MAX else text[: TITLE_MAX - 1] + "…"


def _previous_snapshots(session, snapshot_cls, entity_col, run_id: int) -> dict[int, Any]:
    """entity_id -> its latest snapshot from a run before `run_id`.
    Postgres DISTINCT ON keeps the first row per entity in the given order."""
    stmt = (
        select(snapshot_cls)
        .where(snapshot_cls.run_id < run_id)
        .distinct(entity_col)
        .order_by(entity_col, snapshot_cls.run_id.desc())
    )
    return {getattr(s, entity_col.key): s for s in session.scalars(stmt)}


# --------------------------------------------------------------------------
# Antibodies
# --------------------------------------------------------------------------

def diff_antibodies(session, run_id: int) -> list[dict[str, Any]]:
    current = session.execute(
        select(AntibodySnapshot, Antibody)
        .join(Antibody, Antibody.id == AntibodySnapshot.antibody_id)
        .where(AntibodySnapshot.run_id == run_id)
    ).all()
    if not current:
        return []

    previous = _previous_snapshots(session, AntibodySnapshot, AntibodySnapshot.antibody_id, run_id)
    baseline = not previous  # first run with antibody snapshots

    signals = []
    for snap, ab in current:
        prev = previous.get(ab.id)
        if prev is None:
            if not baseline:
                targets = ", ".join(ab.targets or [ab.target])
                signals.append(
                    {
                        "kind": SignalKind.NEW_ANTIBODY,
                        "entity_type": EntityType.ANTIBODY,
                        "entity_id": ab.id,
                        "title": _title(f"New {targets} antibody: {ab.display_name}"),
                        "old_value": None,
                        "new_value": snap.clinical_stage,
                        "source_url": ab.source_url,
                    }
                )
            continue

        if prev.content_hash == snap.content_hash:
            continue
        if prev.clinical_stage != snap.clinical_stage:
            signals.append(
                {
                    "kind": SignalKind.ANTIBODY_STAGE_CHANGED,
                    "entity_type": EntityType.ANTIBODY,
                    "entity_id": ab.id,
                    "title": _title(
                        f"{ab.display_name}: {prev.clinical_stage} → {snap.clinical_stage}"
                    ),
                    "old_value": prev.clinical_stage,
                    "new_value": snap.clinical_stage,
                    "source_url": ab.source_url,
                }
            )
    return signals


# --------------------------------------------------------------------------
# Trials
# --------------------------------------------------------------------------

def _antibody_names_by_trial(session, trial_ids: list[int]) -> dict[int, list[str]]:
    names: dict[int, list[str]] = defaultdict(list)
    if not trial_ids:
        return names
    rows = session.execute(
        select(antibody_trial.c.trial_id, Antibody.display_name)
        .join(Antibody, Antibody.id == antibody_trial.c.antibody_id)
        .where(antibody_trial.c.trial_id.in_(trial_ids))
        .order_by(Antibody.display_name)
    )
    for trial_id, name in rows:
        names[trial_id].append(name)
    return names


def diff_trials(session, run_id: int) -> list[dict[str, Any]]:
    current = session.execute(
        select(TrialSnapshot, Trial)
        .join(Trial, Trial.id == TrialSnapshot.trial_id)
        .where(TrialSnapshot.run_id == run_id)
    ).all()
    if not current:  # e.g. --skip-ctgov: nothing to compare
        return []

    previous = _previous_snapshots(session, TrialSnapshot, TrialSnapshot.trial_id, run_id)
    baseline = not previous
    antibody_names = _antibody_names_by_trial(session, [t.id for _, t in current])

    signals = []
    for snap, trial in current:
        drugs = ", ".join(antibody_names.get(trial.id, [])[:3]) or "tracked antibody"
        base = {
            "entity_type": EntityType.TRIAL,
            "entity_id": trial.id,
            "source_url": trial.source_url,
        }
        prev = previous.get(trial.id)

        if prev is None:
            if not baseline:
                signals.append(
                    {
                        **base,
                        "kind": SignalKind.NEW_TRIAL,
                        "title": _title(f"Newly tracked trial for {drugs}: {trial.nct_id}"),
                        "old_value": None,
                        "new_value": snap.status,
                    }
                )
            continue

        if prev.content_hash == snap.content_hash:
            continue
        if prev.status != snap.status:
            signals.append(
                {
                    **base,
                    "kind": SignalKind.TRIAL_STATUS_CHANGED,
                    "title": _title(f"{trial.nct_id} ({drugs}): {prev.status} → {snap.status}"),
                    "old_value": prev.status,
                    "new_value": snap.status,
                }
            )
        if prev.phase != snap.phase:
            signals.append(
                {
                    **base,
                    "kind": SignalKind.TRIAL_PHASE_CHANGED,
                    "title": _title(f"{trial.nct_id} ({drugs}): {prev.phase} → {snap.phase}"),
                    "old_value": prev.phase,
                    "new_value": snap.phase,
                }
            )
    return signals


# --------------------------------------------------------------------------
# Step
# --------------------------------------------------------------------------

def diff_signals(event: dict[str, Any]) -> dict[str, Any]:
    """Compare this run's snapshots with each entity's previous snapshot.

    event in:  {"run_id"}
    event out: + {"signals", "signals_by_kind"}
    """
    run_id = int(event["run_id"])

    with session_scope() as session:
        signals = diff_antibodies(session, run_id) + diff_trials(session, run_id)

        # Re-diffing a run replaces its signals instead of duplicating them.
        session.execute(delete(Signal).where(Signal.run_id == run_id))
        if signals:
            session.execute(insert(Signal), [{"run_id": run_id, **s} for s in signals])

    by_kind = Counter(s["kind"].value for s in signals)
    log.info("diff: %d signals %s", len(signals), dict(by_kind))
    return {**event, "signals": len(signals), "signals_by_kind": dict(by_kind)}