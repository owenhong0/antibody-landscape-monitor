"""Make the next pipeline run produce real-looking signals, on demand.

Rewrites the LATEST stored snapshot of a few entities so it disagrees with
what the source currently says. The next run fetches the true current state,
diffs it against these edited snapshots, and emits signals whose new_value
and source_url are real (click through and CT.gov shows the actual status).

Only snapshot rows are touched; the antibody and trial rows are refreshed
by the next run anyway.

    python -m pipeline.demo_seed            # seed, then:
    python -m pipeline.run                  # run produces the demo signals

Run it once per demo. It prints exactly what it changed.
"""
from __future__ import annotations

from sqlalchemy import func, select

from db import session_scope
from models import Antibody, AntibodySnapshot, Trial, TrialSnapshot, antibody_trial

DEMO_ANTIBODY = "pembrolizumab"
SEEDED_HASH = "demo-seed"  # never equals a real sha256, so the diff won't skip it

# Pretend each trial used to be at the earlier status in the pair.
STATUS_BEFORE = {
    "COMPLETED": "ACTIVE_NOT_RECRUITING",
    "ACTIVE_NOT_RECRUITING": "RECRUITING",
    "RECRUITING": "NOT_YET_RECRUITING",
    "TERMINATED": "RECRUITING",
    "WITHDRAWN": "NOT_YET_RECRUITING",
}
STAGE_BEFORE = {"approved": "filed", "filed": "phase_3", "phase_3": "phase_2", "phase_2": "phase_1"}


def latest(session, snapshot_cls, entity_col, entity_id):
    return session.scalars(
        select(snapshot_cls)
        .where(entity_col == entity_id)
        .order_by(snapshot_cls.run_id.desc())
        .limit(1)
    ).first()


def seed(trials: int = 2) -> None:
    with session_scope() as session:
        ab = session.scalars(select(Antibody).where(Antibody.inn_name == DEMO_ANTIBODY)).one()

        # 1. Trial status changes: the most recently updated trials of the demo antibody
        candidates = session.scalars(
            select(Trial)
            .join(antibody_trial, antibody_trial.c.trial_id == Trial.id)
            .where(antibody_trial.c.antibody_id == ab.id, Trial.status.in_(STATUS_BEFORE))
            .order_by(Trial.last_update_date.desc().nulls_last())
            .limit(trials)
        ).all()
        for trial in candidates:
            snap = latest(session, TrialSnapshot, TrialSnapshot.trial_id, trial.id)
            before = STATUS_BEFORE[snap.status]
            print(f"trial {trial.nct_id}: snapshot run {snap.run_id} status {snap.status} -> {before}")
            snap.status = before
            snap.content_hash = SEEDED_HASH

        # 2. Antibody stage change
        snap = latest(session, AntibodySnapshot, AntibodySnapshot.antibody_id, ab.id)
        if snap and snap.clinical_stage in STAGE_BEFORE:
            before = STAGE_BEFORE[snap.clinical_stage]
            print(f"antibody {ab.inn_name}: snapshot run {snap.run_id} stage {snap.clinical_stage} -> {before}")
            snap.clinical_stage = before
            snap.content_hash = SEEDED_HASH

        n_seeded = session.scalar(select(func.count()).select_from(TrialSnapshot).where(TrialSnapshot.content_hash == SEEDED_HASH))
        print(f"seeded {n_seeded} trial snapshot(s). Now run: python -m pipeline.run")


if __name__ == "__main__":
    seed()