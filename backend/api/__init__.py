"""Flask REST API over the pipeline's Postgres tables.

    cd backend && source .venv/bin/activate
    flask --app api run --port 5001 --debug

`flask --app api` looks in this package for create_app() and calls it.
"""
import os
from typing import Callable

from flask import Flask
from flask_cors import CORS
from sqlalchemy.orm import Session

from api.database import init_db
from api.errors import register_error_handlers
from api.serialize import ApiJSONProvider

# The Next.js dev server. Override with CORS_ORIGINS (comma-separated).
DEFAULT_CORS_ORIGINS = "http://localhost:3000"


def create_app(session_factory: Callable[[], Session] | None = None) -> Flask:
    """Build the app.

    session_factory: defaults to db.SessionLocal (DATABASE_URL from .env).
    Tests pass their own to use a test database or a broken one.
    """
    app = Flask(__name__)
    app.json = ApiJSONProvider(app)

    if session_factory is None:
        from db import SessionLocal  # imported lazily: needs DATABASE_URL

        session_factory = SessionLocal
    init_db(app, session_factory)
    register_error_handlers(app)

    # Browsers only let these origins read API responses. Not auth: curl and
    # servers ignore CORS entirely.
    origins = os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    CORS(app, resources={r"/api/*": {"origins": [o.strip() for o in origins.split(",") if o.strip()]}})

    # Imported here, not at the top of the file: blueprints import helpers
    # from the `api` package, and a top-level import would be circular.
    from api.antibodies import bp as antibodies_bp
    from api.health import bp as health_bp
    from api.landscape import bp as landscape_bp
    from api.runs import bp as runs_bp
    from api.signals import bp as signals_bp

    for bp in (health_bp, landscape_bp, antibodies_bp, signals_bp, runs_bp):
        app.register_blueprint(bp, url_prefix="/api")
    return app