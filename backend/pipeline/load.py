"""Load steps: normalized JSON -> Postgres.

Rules every load follows (see docs/data-transformations.md):

- Idempotent. Re-running a load for the same run_id changes nothing and never
  fails on a unique constraint: rows are upserted (INSERT ... ON CONFLICT).
- Never delete. An antibody or trial that drops out of a source keeps its row,
  history and signals. It just stops getting new snapshots.
- Current values go on the entity row (antibody, trial) for fast reads; a
  snapshot row per run is appended for diffing and audit.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any, Iterator, Sequence

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert

from db import session_scope
from models import Antibody, AntibodySnapshot, Chain, Trial, TrialSnapshot, antibody_trial

log = logging.getLogger(__name__)

BATCH_SIZE = 500  # rows per INSERT; keeps well under Postgres' 65k parameter limit


def _batches(rows: Sequence[dict[str, Any]], size: int = BATCH_SIZE) -> Iterator[Sequence[dict[str, Any]]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _as_date(value: str | None) -> date | None:
    """CT.gov dates are 'YYYY-MM-DD', occasionally just 'YYYY-MM'."""
    if not value:
        return None
    parts = value.split("-")
    if len(parts) == 2:
        parts.append("01")
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return None


# --------------------------------------------------------------------------
# Antibodies + chains
# --------------------------------------------------------------------------

ANTIBODY_FIELDS = (
    "inn_name", "display_name", "target", "targets", "format",
    "clinical_stage", "stage_rank", "status", "source_url",
)


def load_antibodies(event: dict[str, Any]) -> dict[str, Any]:
    """Upsert antibodies and their chains; append one snapshot per antibody.

    event in:  {"run_id", "antibodies_path"}
    event out: + {"loaded_antibodies", "loaded_chains"}
    """
    run_id = int(event["run_id"])
    records = json.loads(Path(event["antibodies_path"]).read_text())

    with session_scope() as session:
        # 1. Antibodies: insert new, update current values on existing.
        ab_rows = [{k: r[k] for k in ANTIBODY_FIELDS} for r in records]
        ids: dict[str, int] = {}
        for batch in _batches(ab_rows):
            stmt = insert(Antibody).values(list(batch))
            stmt = stmt.on_conflict_do_update(
                index_elements=[Antibody.inn_name],
                set_={k: stmt.excluded[k] for k in ANTIBODY_FIELDS if k != "inn_name"},
            ).returning(Antibody.id, Antibody.inn_name)
            ids.update({name: ab_id for ab_id, name in session.execute(stmt)})

        # 2. Chains: upsert by (antibody, H/L, index). Updating in place keeps
        #    chain ids stable, so liabilities attached to them survive re-runs.
        chain_rows = [
            {
                "antibody_id": ids[r["inn_name"]],
                "chain_type": c["chain_type"],
                "chain_index": c["chain_index"],
                "sequence": c["sequence"],
                "length": c["length"],
            }
            for r in records
            for c in r["chains"]
        ]
        for batch in _batches(chain_rows):
            stmt = insert(Chain).values(list(batch))
            stmt = stmt.on_conflict_do_update(
                index_elements=[Chain.antibody_id, Chain.chain_type, Chain.chain_index],
                set_={"sequence": stmt.excluded.sequence, "length": stmt.excluded.length},
            )
            session.execute(stmt)

        # 3. Snapshots: one per antibody per run. DO NOTHING makes a retried
        #    load for the same run a no-op instead of a unique-constraint error.
        snap_rows = [
            {
                "antibody_id": ids[r["inn_name"]],
                "run_id": run_id,
                "clinical_stage": r["clinical_stage"],
                "content_hash": r["content_hash"],
            }
            for r in records
        ]
        for batch in _batches(snap_rows):
            session.execute(
                insert(AntibodySnapshot).values(list(batch)).on_conflict_do_nothing(
                    index_elements=[AntibodySnapshot.antibody_id, AntibodySnapshot.run_id]
                )
            )

    log.info("load: %d antibodies, %d chains", len(ids), len(chain_rows))
    return {**event, "loaded_antibodies": len(ids), "loaded_chains": len(chain_rows)}


# --------------------------------------------------------------------------
# Trials + antibody links
# --------------------------------------------------------------------------

TRIAL_FIELDS = ("nct_id", "title", "sponsor", "status", "phase", "enrollment", "last_update_date")


def load_trials(event: dict[str, Any]) -> dict[str, Any]:
    """Upsert trials, link them to antibodies, append one snapshot per trial.

    Runs after load_antibodies: links need antibody ids.

    event in:  {"run_id", "trials_path"}
    event out: + {"loaded_trials", "loaded_links"}
    """
    run_id = int(event["run_id"])
    records = json.loads(Path(event["trials_path"]).read_text())
    for r in records:
        r["last_update_date"] = _as_date(r.get("last_update_date"))
        if r.get("sponsor"):  # trial.sponsor is VARCHAR(255)
            r["sponsor"] = r["sponsor"][:255]

    with session_scope() as session:
        antibody_ids = dict(session.execute(select(Antibody.inn_name, Antibody.id)).all())

        # 1. Trials
        ids: dict[str, int] = {}
        trial_rows = [{k: r.get(k) for k in TRIAL_FIELDS} for r in records]
        for batch in _batches(trial_rows):
            stmt = insert(Trial).values(list(batch))
            stmt = stmt.on_conflict_do_update(
                index_elements=[Trial.nct_id],
                set_={k: stmt.excluded[k] for k in TRIAL_FIELDS if k != "nct_id"},
            ).returning(Trial.id, Trial.nct_id)
            ids.update({nct: t_id for t_id, nct in session.execute(stmt)})

        # 2. Antibody <-> trial links. Existing links are kept even if a trial
        #    falls out of this run's top-50 window (never delete).
        link_rows = [
            {"antibody_id": antibody_ids[inn], "trial_id": ids[r["nct_id"]]}
            for r in records
            for inn in r["inn_names"]
            if inn in antibody_ids
        ]
        for batch in _batches(link_rows):
            session.execute(insert(antibody_trial).values(list(batch)).on_conflict_do_nothing())

        # 3. Snapshots
        snap_rows = [
            {
                "trial_id": ids[r["nct_id"]],
                "run_id": run_id,
                "status": r.get("status"),
                "phase": r.get("phase"),
                "enrollment": r.get("enrollment"),
                "last_update_date": r["last_update_date"],
                "content_hash": r["content_hash"],
            }
            for r in records
        ]
        for batch in _batches(snap_rows):
            session.execute(
                insert(TrialSnapshot).values(list(batch)).on_conflict_do_nothing(
                    index_elements=[TrialSnapshot.trial_id, TrialSnapshot.run_id]
                )
            )

    log.info("load: %d trials, %d antibody-trial links", len(ids), len(link_rows))
    return {**event, "loaded_trials": len(ids), "loaded_links": len(link_rows)}