"""Behavior for the public deploy: read-only mode and no local paths in responses."""
import pytest

from api import create_app
from api.serialize import public_path


def make_client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_mark_seen_is_403_in_read_only_mode(engine, seed, monkeypatch):
    monkeypatch.setenv("ALLOW_WRITES", "false")
    client = make_client()
    sid = seed.signals["new_trial"]

    r = client.post(f"/api/signals/{sid}/seen")
    assert r.status_code == 403
    assert r.get_json()["error"]["code"] == "forbidden"

    # nothing was written
    unseen = client.get("/api/signals?seen=false").get_json()
    assert sid in [s["id"] for s in unseen["items"]]


def test_read_only_403_does_not_reveal_unknown_ids(engine, seed, monkeypatch):
    monkeypatch.setenv("ALLOW_WRITES", "false")
    assert make_client().post("/api/signals/999999/seen").status_code == 403


def test_reads_work_in_read_only_mode(engine, seed, monkeypatch):
    monkeypatch.setenv("ALLOW_WRITES", "false")
    client = make_client()
    for path in ("/api/health", "/api/landscape?target=PD-1", "/api/signals", "/api/runs"):
        assert client.get(path).status_code == 200, path


@pytest.mark.parametrize("raw,expected", [
    ("/Users/lilliemach/PycharmProjects/antibody-landscape-monitor/data/raw/5", "data/raw/5"),
    ("/srv/app/data/raw/12", "data/raw/12"),
    ("data/raw/1", "data/raw/1"),
    ("/tmp/somewhere/run-7", "run-7"),
    (None, None),
])
def test_public_path(raw, expected):
    assert public_path(raw) == expected


def test_runs_never_expose_absolute_paths(client, seed):
    for run in client.get("/api/runs").get_json()["items"]:
        assert not run["raw_location"].startswith("/")