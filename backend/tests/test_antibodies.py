"""GET /api/antibodies/<id>: chains, CDR3 bounds, liabilities, trials, query count."""
from datetime import date, datetime

from tests.fixtures import SEQUENCES


def get_detail(client, seed, name):
    r = client.get(f"/api/antibodies/{seed.antibodies[name]}")
    assert r.status_code == 200
    return r.get_json()


def test_bispecific_detail_has_all_four_chains(client, seed):
    body = get_detail(client, seed, "Duotamab")

    assert body["display_name"] == "Duotamab"
    assert body["targets"] == ["PD-1", "HER2"]
    assert body["stage_rank"] == 1
    assert body["source_url"].startswith("https://")
    assert [(c["chain_type"], c["chain_index"]) for c in body["chains"]] == [
        ("H", 1), ("H", 2), ("L", 1), ("L", 2),
    ]
    h1 = body["chains"][0]
    assert h1["sequence"] == SEQUENCES["duotamab_H1"]
    assert h1["length"] == len(h1["sequence"])
    assert h1["cdr3_sequence"] == "AKWGNSTYFDV"
    assert h1["cdr3_len"] == 11


def test_cdr3_bounds_are_1_based_and_match_the_sequence(client, seed):
    body = get_detail(client, seed, "Exemplimab")
    for ch in body["chains"]:
        start, end = ch["cdr3_start"], ch["cdr3_end"]
        assert ch["sequence"][start - 1:end] == ch["cdr3_sequence"]
        assert end - start + 1 == ch["cdr3_len"]


def test_chain_without_cdr3_match(client, seed):
    l2 = get_detail(client, seed, "Duotamab")["chains"][3]
    assert (l2["chain_type"], l2["chain_index"]) == ("L", 2)
    assert l2["cdr3_sequence"] is None
    assert l2["cdr3_len"] is None
    assert l2["cdr3_start"] is None and l2["cdr3_end"] is None
    assert all(li["in_cdr3"] is False for li in l2["liabilities"])


def test_liabilities_sorted_by_position_with_in_cdr3_flag(client, seed):
    body = get_detail(client, seed, "Exemplimab")
    h1 = body["chains"][0]
    positions = [li["position"] for li in h1["liabilities"]]
    assert positions == sorted(positions)  # fixture inserts them in reverse

    in_cdr3 = [li for li in h1["liabilities"] if li["in_cdr3"]]
    outside = [li for li in h1["liabilities"] if not li["in_cdr3"]]
    assert {(li["kind"], li["motif"], li["severity"]) for li in in_cdr3} >= {
        ("deamidation", "NG", "high"),
        ("oxidation", "M", "low"),
    }
    assert outside, "fixture should have framework liabilities too"

    # A motif counts as in CDR3 when any of its residues overlap the bounds.
    for li in in_cdr3:
        assert h1["cdr3_start"] <= li["position"] + len(li["motif"]) - 1
        assert li["position"] <= h1["cdr3_end"]
    for li in outside:
        last = li["position"] + len(li["motif"]) - 1
        assert last < h1["cdr3_start"] or li["position"] > h1["cdr3_end"]

    counts = h1["liability_counts"]
    assert counts["total"] == len(h1["liabilities"])
    assert counts["in_cdr3"] == len(in_cdr3)
    assert counts["high"] + counts["medium"] + counts["low"] == counts["total"]
    assert body["liability_counts"]["total"] == sum(
        c["liability_counts"]["total"] for c in body["chains"]
    )


def test_unpaired_cysteine_flagged(client, seed):
    h2 = get_detail(client, seed, "Duotamab")["chains"][1]
    assert (h2["chain_type"], h2["chain_index"]) == ("H", 2)
    assert any(
        li["kind"] == "unpaired_cysteine" and li["severity"] == "high"
        for li in h2["liabilities"]
    )


def test_trials_newest_update_first_and_undated_last(client, seed):
    body = get_detail(client, seed, "Duotamab")
    assert [t["nct_id"] for t in body["trials"]] == ["NCT09000001", "NCT09000002", "NCT09000003"]
    first = body["trials"][0]
    assert first["last_update_date"] == "2026-10-01"
    assert first["source_url"] == "https://clinicaltrials.gov/study/NCT09000001"
    assert body["trials"][2]["last_update_date"] is None


def test_trial_linked_to_two_antibodies_shows_on_both(client, seed):
    exe = get_detail(client, seed, "Exemplimab")
    duo = get_detail(client, seed, "Duotamab")
    assert "NCT09000001" in {t["nct_id"] for t in duo["trials"]}
    assert [t["nct_id"] for t in exe["trials"]] == ["NCT09000001", "NCT09000004"]


def test_enums_are_strings_and_timestamps_iso(client, seed):
    body = get_detail(client, seed, "Duotamab")
    li = body["chains"][0]["liabilities"][0]
    assert isinstance(li["kind"], str) and isinstance(li["severity"], str)
    assert body["chains"][0]["chain_type"] == "H"
    assert datetime.fromisoformat(body["created_at"]).utcoffset().total_seconds() == 0
    date.fromisoformat(body["trials"][0]["last_update_date"])  # raises if not ISO


def test_no_n_plus_one(client, seed, query_log):
    # antibody + chains + liabilities + trials, however many chains/trials there are
    with query_log() as log:
        client.get(f"/api/antibodies/{seed.antibodies['Duotamab']}")
    assert len(log) == 4, log.statements


def test_antibody_without_chains_or_trials(client, seed):
    body = get_detail(client, seed, "Nullimab")
    assert body["chains"] == [] and body["trials"] == []
    assert body["stage_rank"] is None
    assert body["liability_counts"]["total"] == 0


def test_unknown_id_is_404_json(client, seed):
    r = client.get("/api/antibodies/999999")
    assert r.status_code == 404
    assert r.get_json()["error"] == {
        "status": 404, "code": "not_found", "message": "Antibody 999999 not found",
    }


def test_non_integer_id_is_404_json(client, seed):
    # <int:antibody_id> doesn't match, so Flask's own 404 goes through our JSON handler
    r = client.get("/api/antibodies/pembrolizumab")
    assert r.status_code == 404
    assert r.get_json()["error"]["code"] == "not_found"