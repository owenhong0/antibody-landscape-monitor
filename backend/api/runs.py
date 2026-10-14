"""GET /api/runs?limit=: pipeline run history, newest first."""
from flask import Blueprint, jsonify
from sqlalchemy import func, select

from api import serialize
from api.database import get_db
from api.params import int_param
from models import PipelineRun

bp = Blueprint("runs", __name__)


@bp.get("/runs")
def list_runs():
    limit = int_param("limit", 20, min_value=1, max_value=200)
    db = get_db()
    runs = db.scalars(
        select(PipelineRun).order_by(PipelineRun.id.desc()).limit(limit)
    ).all()
    total = db.scalar(select(func.count()).select_from(PipelineRun))
    return jsonify(items=[serialize.run(r) for r in runs], total=total, limit=limit)