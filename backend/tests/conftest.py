"""Test setup: a separate Postgres database, migrated with the repo's Alembic
migrations, reseeded before every test.

The test database is your .env DATABASE_URL with `_test` appended to the
database name (antibody_landscape -> antibody_landscape_test), so it follows
whatever host/port your .env uses. Override with TEST_DATABASE_URL.
It is created if missing. Tests refuse to run against a name not ending
in `_test`, because every test TRUNCATEs all tables.
"""
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from dotenv import dotenv_values
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]


def _test_database_url() -> str:
    if os.getenv("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    base = os.getenv("DATABASE_URL") or dotenv_values(BACKEND.parent / ".env").get("DATABASE_URL")
    if not base:
        raise RuntimeError("Set DATABASE_URL in .env or TEST_DATABASE_URL")
    url = make_url(base)
    name = url.database if url.database.endswith("_test") else f"{url.database}_test"
    return url.set(database=name).render_as_string(hide_password=False)


TEST_DATABASE_URL = _test_database_url()
if not make_url(TEST_DATABASE_URL).database.endswith("_test"):
    raise RuntimeError(f"Refusing to run tests against {TEST_DATABASE_URL!r}: name must end in _test")

# Must happen before config.py / db.py are imported anywhere: they read it once.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL


def _create_database_if_missing(url_str: str) -> None:
    url = make_url(url_str)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database})
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine():
    """Once per test run: create the test DB if needed and migrate it."""
    from alembic import command
    from alembic.config import Config

    _create_database_if_missing(TEST_DATABASE_URL)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")  # env.py reads DATABASE_URL, set above

    from db import engine as app_engine  # the engine the API's sessions use

    yield app_engine
    app_engine.dispose()


@pytest.fixture()
def seed(engine):
    """Before each test that asks for it: truncate everything and reseed."""
    from db import SessionLocal
    from tests.fixtures import seed as seed_data

    with SessionLocal() as session:
        return seed_data(session)


@pytest.fixture()
def app(engine):
    from api import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


class QueryLog:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[object] = []

    def __len__(self) -> int:
        return len(self.statements)


@pytest.fixture()
def query_log(engine):
    """Records every SQL statement the API sends while the block runs:

        with query_log() as log:
            client.get(...)
        assert len(log) <= 4
    """

    @contextmanager
    def capture() -> Iterator[QueryLog]:
        log = QueryLog()

        def before(conn, cursor, statement, params, context, executemany):
            log.statements.append(statement)
            log.params.append(params)

        event.listen(engine, "before_cursor_execute", before)
        try:
            yield log
        finally:
            event.remove(engine, "before_cursor_execute", before)

    return capture