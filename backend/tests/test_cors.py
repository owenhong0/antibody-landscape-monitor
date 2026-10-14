"""CORS: the Next.js dev server can read API responses; other origins can't."""
from api import create_app

DASHBOARD = "http://localhost:3000"


def test_allows_next_dev_server(client, seed):
    r = client.get("/api/health", headers={"Origin": DASHBOARD})
    assert r.headers["Access-Control-Allow-Origin"] == DASHBOARD


def test_preflight_for_mark_seen(client, seed):
    # What the browser sends before a POST with Content-Type: application/json.
    r = client.options(
        f"/api/signals/{seed.signals['new_trial']}/seen",
        headers={
            "Origin": DASHBOARD,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert r.status_code == 200
    assert r.headers["Access-Control-Allow-Origin"] == DASHBOARD
    assert "POST" in r.headers["Access-Control-Allow-Methods"]


def test_other_origins_get_no_cors_headers(client, seed):
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_origins_configurable_from_env(engine, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000, https://alm.example.com")
    client = create_app().test_client()
    r = client.get("/api/health", headers={"Origin": "https://alm.example.com"})
    assert r.headers["Access-Control-Allow-Origin"] == "https://alm.example.com"