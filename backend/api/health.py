"""GET /api/health: is the API up, can it reach Postgres, and what ran last."""
from flask import Blueprint, current_app, jsonify
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from api.database import get_db
from api.serialize import enum_value, iso
from models import PipelineRun

bp = Blueprint("health", __name__)


@bp.get("/health")
def health():
    try:
        db = get_db()
        db.execute(text("SELECT 1"))
        latest = db.scalars(
            select(PipelineRun).order_by(PipelineRun.id.desc()).limit(1)
        ).first()
    except SQLAlchemyError as exc:
        current_app.logger.warning("health check: database unreachable: %s", exc)
        return jsonify(status="error", database="unreachable"), 503

    latest_run = None
    if latest is not None:
        latest_run = {
            "id": latest.id,
            "status": enum_value(latest.status),
            "finished_at": iso(latest.finished_at),
        }
    return jsonify(status="ok", database="ok", latest_run=latest_run)