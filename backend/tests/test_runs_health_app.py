"""/api/runs, /api/health, and app-wide behavior: JSON errors and sessions."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api import create_app


def make_app(session_factory=None):
    app = create_app(session_factory=session_factory)
    app.config["TESTING"] = True
    return app


# --- /api/runs -------------------------------------------------------------------

def test_runs_newest_first_with_duration_and_error(client, seed):
    r = client.get("/api/runs")
    assert r.status_code == 200
    body = r.get_json()
    assert body["total"] == 4
    assert [run["id"] for run in body["items"]] == [
        seed.runs["running"], seed.runs["partial"], seed.runs["failed"], seed.runs["succeeded"],
    ]
    running, partial, failed, ok = body["items"]

    assert running["status"] == "running"
    assert running["finished_at"] is None and running["duration_seconds"] is None

    assert partial["status"] == "partial"
    assert partial["duration_seconds"] == 70.25
    assert partial["counts"]["ctgov_failed"] == 1
    assert partial["started_at"] == "2026-10-13T09:00:00+00:00"

    assert failed["status"] == "failed"
    assert failed["error"] == "fetch_ctgov: 503 Service Unavailable"
    assert failed["duration_seconds"] == 2.0

    assert ok["status"] == "succeeded" and ok["error"] is None
    assert ok["counts"] == {"antibodies": 6, "chains": 6, "trials": 4, "signals": 3}
    assert set(ok) == {"id", "status", "started_at", "finished_at", "duration_seconds",
                       "counts", "raw_location", "error"}


def test_runs_limit(client, seed):
    body = client.get("/api/runs?limit=1").get_json()
    assert [r["id"] for r in body["items"]] == [seed.runs["running"]]
    assert body["total"] == 4 and body["limit"] == 1
    assert client.get("/api/runs?limit=0").status_code == 400


# --- /api/health -----------------------------------------------------------------

def test_health_ok_reports_latest_run(client, seed):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json() == {
        "status": "ok",
        "database": "ok",
        "latest_run": {"id": seed.runs["running"], "status": "running", "finished_at": None},
    }


def test_health_reports_unreachable_database(engine):
    # Same driver and credentials as the real engine, but nothing listens on port 1,
    # so the connection is refused immediately.
    broken = create_engine(engine.url.set(host="127.0.0.1", port=1),
                           connect_args={"connect_timeout": 1})
    r = make_app(sessionmaker(bind=broken)).test_client().get("/api/health")
    assert r.status_code == 503
    assert r.get_json() == {"status": "error", "database": "unreachable"}
    broken.dispose()


# --- errors ----------------------------------------------------------------------

def test_unknown_route_is_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.is_json and r.get_json()["error"]["code"] == "not_found"


def test_wrong_method_is_json_405_with_allow_header(client):
    r = client.post("/api/runs")
    assert r.status_code == 405
    assert r.get_json()["error"]["code"] == "method_not_allowed"
    assert "GET" in r.headers["Allow"]


def test_unexpected_error_is_json_500_without_details(engine):
    app = make_app()

    @app.get("/api/boom")
    def boom():
        raise RuntimeError("kaboom: password=hunter2")

    r = app.test_client().get("/api/boom")
    assert r.status_code == 500
    assert r.get_json()["error"] == {
        "status": 500, "code": "internal_error", "message": "Internal server error",
    }
    assert "kaboom" not in r.get_data(as_text=True)


# --- sessions --------------------------------------------------------------------

def test_one_session_per_request_closed_on_teardown(engine, seed):
    from db import SessionLocal

    opened = []

    def factory():
        s = SessionLocal()
        opened.append(s)
        return s

    client = make_app(factory).test_client()
    client.get("/api/signals")  # several queries, still one session
    client.get("/api/runs")
    assert len(opened) == 2
    assert all(not s.in_transaction() for s in opened)
    assert engine.pool.checkedout() == 0  # every connection back in the pool