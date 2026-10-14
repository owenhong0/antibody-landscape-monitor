"""GET /api/antibodies/<id>: one antibody with its chains, liabilities and trials.

Loaded with selectinload, so the query count is fixed (antibody, chains,
liabilities, trials = 4) no matter how many chains or trials there are.
"""
from datetime import date

from flask import Blueprint, jsonify
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api import serialize
from api.database import get_db
from api.errors import NotFound
from models import Antibody, Chain

bp = Blueprint("antibodies", __name__)


@bp.get("/antibodies/<int:antibody_id>")
def antibody_detail(antibody_id: int):
    ab = get_db().scalars(
        select(Antibody)
        .where(Antibody.id == antibody_id)
        .options(
            selectinload(Antibody.chains).selectinload(Chain.liabilities),
            selectinload(Antibody.trials),
        )
    ).one_or_none()
    if ab is None:
        raise NotFound(f"Antibody {antibody_id} not found")

    # Newest CT.gov update first; trials without a date last, then by NCT id.
    trials = sorted(
        ab.trials,
        key=lambda t: (
            t.last_update_date is None,
            -(t.last_update_date or date.min).toordinal(),
            t.nct_id,
        ),
    )
    chains = [serialize.chain(ch) for ch in ab.chains]  # H1, H2, L1, L2 (relationship order_by)

    return jsonify(
        {
            **serialize.antibody(ab),
            "liability_counts": {
                key: sum(ch["liability_counts"][key] for ch in chains)
                for key in ("high", "medium", "low", "in_cdr3", "total")
            },
            "chains": chains,
            "trials": [serialize.trial(t) for t in trials],
        }
    )