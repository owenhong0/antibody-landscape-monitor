"""GET /api/signals (filters, pagination, entity resolution) and POST .../seen."""
from datetime import datetime
from urllib.parse import quote

import pytest

NEWEST_FIRST = ["status_changed", "new_trial", "stage_changed", "phase_changed", "new_antibody", "orphan"]


def ids(seed, keys):
    return [seed.signals[k] for k in keys]


def feed(client, query=""):
    r = client.get(f"/api/signals{query}")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


# --- GET /api/signals ------------------------------------------------------------

def test_newest_first_with_all_fields(client, seed):
    body = feed(client)
    assert [s["id"] for s in body["items"]] == ids(seed, NEWEST_FIRST)
    assert body["total"] == 6 and body["limit"] == 50 and body["offset"] == 0

    assert body["items"][0] == {
        "id": seed.signals["status_changed"],
        "run_id": seed.runs["partial"],
        "kind": "TRIAL_STATUS_CHANGED",
        "title": "NCT09000001 moved RECRUITING -> ACTIVE_NOT_RECRUITING",
        "old_value": "RECRUITING",
        "new_value": "ACTIVE_NOT_RECRUITING",
        "source_url": "https://clinicaltrials.gov/study/NCT09000001",
        "entity_type": "trial",
        "entity_id": seed.trials["NCT09000001"],
        "entity": {
            "label": "NCT09000001",
            "antibodies": [  # a shared trial links to every antibody, sorted by name
                {"id": seed.antibodies["Duotamab"], "display_name": "Duotamab"},
                {"id": seed.antibodies["Exemplimab"], "display_name": "Exemplimab"},
            ],
        },
        "seen": False,
        "seen_at": None,
        "created_at": "2026-10-13T09:01:10+00:00",
    }


def test_antibody_entity_and_missing_entity(client, seed):
    items = {s["id"]: s for s in feed(client)["items"]}
    stage = items[seed.signals["stage_changed"]]
    assert stage["entity_type"] == "antibody"
    assert stage["entity"] == {
        "label": "Fixturimab",
        "antibodies": [{"id": seed.antibodies["Fixturimab"], "display_name": "Fixturimab"}],
    }
    assert items[seed.signals["orphan"]]["entity"] is None  # polymorphic id, no FK


def test_filter_by_kind(client, seed):
    body = feed(client, "?kind=NEW_TRIAL")
    assert [s["id"] for s in body["items"]] == ids(seed, ["new_trial", "orphan"])
    assert body["total"] == 2


def test_filter_by_several_kinds_case_insensitive(client, seed):
    body = feed(client, "?kind=new_antibody,ANTIBODY_STAGE_CHANGED")
    assert [s["id"] for s in body["items"]] == ids(seed, ["stage_changed", "new_antibody"])


def test_unknown_kind_is_400(client, seed):
    r = client.get("/api/signals?kind=TRIAL_DELETED")
    assert r.status_code == 400
    assert "NEW_TRIAL" in r.get_json()["error"]["details"]["allowed"]


@pytest.mark.parametrize("value,expected", [("false", 5), ("true", 1), ("0", 5), ("1", 1)])
def test_filter_by_seen(client, seed, value, expected):
    body = feed(client, f"?seen={value}")
    assert body["total"] == expected
    assert all(s["seen"] is (value in ("true", "1")) for s in body["items"])


def test_bad_seen_is_400(client, seed):
    assert client.get("/api/signals?seen=maybe").status_code == 400


@pytest.mark.parametrize("since,expected", [
    ("2026-10-13", 3),                   # a date means midnight UTC
    ("2026-10-13T09:01:05Z", 2),         # inclusive
    ("2026-10-13T05:01:05-04:00", 2),    # same instant, Boston time
    ("2026-10-13T09:01:05", 2),          # naive means UTC
    ("2030-01-01", 0),
])
def test_filter_by_since(client, seed, since, expected):
    assert feed(client, f"?since={quote(since)}")["total"] == expected


def test_unencoded_plus_offset_still_parses(client, seed):
    # a raw '+' decodes to a space when the client forgets to encode it
    assert feed(client, "?since=2026-10-13T09:01:05+00:00")["total"] == 2


def test_bad_since_is_400(client, seed):
    r = client.get("/api/signals?since=yesterday")
    assert r.status_code == 400
    assert r.get_json()["error"]["details"]["examples"]


def test_filter_by_run_id(client, seed):
    body = feed(client, f"?run_id={seed.runs['succeeded']}")
    assert [s["id"] for s in body["items"]] == ids(seed, ["phase_changed", "new_antibody", "orphan"])


def test_run_id_latest_is_newest_finished_run(client, seed):
    # the "running" run has the highest id but hasn't finished, so latest = "partial"
    body = feed(client, "?run_id=latest")
    assert {s["run_id"] for s in body["items"]} == {seed.runs["partial"]}
    assert body["total"] == 3


@pytest.mark.parametrize("query", ["?run_id=abc", "?run_id=0", "?run_id="])
def test_bad_run_id_is_400(client, seed, query):
    assert client.get(f"/api/signals{query}").status_code == 400


def test_filters_combine(client, seed):
    body = feed(client, f"?kind=NEW_TRIAL&seen=false&run_id={seed.runs['partial']}")
    assert [s["id"] for s in body["items"]] == ids(seed, ["new_trial"])


def test_pagination(client, seed):
    page1 = feed(client, "?limit=2")
    page2 = feed(client, "?limit=2&offset=2")
    past_end = feed(client, "?limit=2&offset=6")
    assert [s["id"] for s in page1["items"]] == ids(seed, NEWEST_FIRST[:2])
    assert [s["id"] for s in page2["items"]] == ids(seed, NEWEST_FIRST[2:4])
    assert past_end["items"] == [] and past_end["total"] == 6


@pytest.mark.parametrize("query", ["?limit=0", "?limit=201", "?limit=ten", "?offset=-1"])
def test_bad_pagination_is_400(client, seed, query):
    r = client.get(f"/api/signals{query}")
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "bad_request"


def test_feed_query_count_is_constant(client, seed, query_log):
    # count + page + antibody labels + trial links + trial labels
    with query_log() as log:
        client.get("/api/signals")
    assert len(log) <= 5, log.statements


# --- POST /api/signals/<id>/seen -------------------------------------------------

def test_mark_seen(client, seed):
    sid = seed.signals["status_changed"]
    r = client.post(f"/api/signals/{sid}/seen")
    assert r.status_code == 200
    body = r.get_json()
    assert body["id"] == sid and body["seen"] is True
    assert datetime.fromisoformat(body["seen_at"]).tzinfo is not None

    # committed: the next request (a new session) sees it
    unseen = feed(client, "?seen=false")
    assert sid not in [s["id"] for s in unseen["items"]]
    assert unseen["total"] == 4


def test_mark_seen_is_idempotent(client, seed):
    sid = seed.signals["new_trial"]
    first = client.post(f"/api/signals/{sid}/seen").get_json()
    second = client.post(f"/api/signals/{sid}/seen")
    assert second.status_code == 200
    assert second.get_json()["seen_at"] == first["seen_at"]


def test_mark_already_seen_keeps_original_seen_at(client, seed):
    r = client.post(f"/api/signals/{seed.signals['new_antibody']}/seen")
    assert r.get_json()["seen_at"] == "2026-10-11T15:00:00+00:00"


def test_mark_seen_unknown_id_is_404(client, seed):
    r = client.post("/api/signals/999999/seen")
    assert r.status_code == 404
    assert r.get_json()["error"]["message"] == "Signal 999999 not found"


def test_mark_seen_requires_post(client, seed):
    r = client.get(f"/api/signals/{seed.signals['new_trial']}/seen")
    assert r.status_code == 405
    assert r.get_json()["error"]["code"] == "method_not_allowed"
    assert "POST" in r.headers["Allow"]