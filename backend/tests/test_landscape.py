from api.landscape import STAGES


def names(column):
    return [ab["display_name"] for ab in column["antibodies"]]


def column(body, stage):
    return next(c for c in body["columns"] if c["stage"] == stage)


def test_target_groups_by_stage_rank(client, seed):
    r = client.get("/api/landscape?target=PD-1")
    assert r.status_code == 200
    body = r.get_json()

    assert body["target"] == "PD-1"
    assert body["total"] == 3
    # Every column is always present, left to right, unknown last.
    assert [c["stage"] for c in body["columns"]] == [s["stage"] for s in STAGES]
    assert [c["stage_rank"] for c in body["columns"]] == [0, 1, 2, 3, 4, 5, None]
    assert names(column(body, "approved")) == ["Exemplimab"]
    assert names(column(body, "phase_2")) == ["Fixturimab"]
    assert names(column(body, "phase_1")) == ["Duotamab"]
    assert column(body, "preclinical")["count"] == 0
    assert sum(c["count"] for c in body["columns"]) == body["total"]


def test_bispecific_appears_under_both_targets(client, seed):
    her2 = client.get("/api/landscape?target=HER2").get_json()
    assert her2["total"] == 2
    assert names(column(her2, "phase_1")) == ["Duotamab"]
    duo = column(her2, "phase_1")["antibodies"][0]
    assert duo["targets"] == ["PD-1", "HER2"]
    assert duo["format"] == "Bispecific"
    assert set(duo) == {"id", "inn_name", "display_name", "targets", "format",
                        "clinical_stage", "stage_rank", "status"}


def test_null_stage_rank_lands_in_unknown_column(client, seed):
    body = client.get("/api/landscape?target=CD20").get_json()
    unknown = column(body, "unknown")
    assert unknown["stage_rank"] is None
    assert names(unknown) == ["Nullimab"]


def test_target_is_case_insensitive(client, seed):
    body = client.get("/api/landscape?target=pd-l1").get_json()
    assert body["target"] == "PD-L1"
    assert names(column(body, "preclinical")) == ["Prelimab"]


def test_tracked_target_without_antibodies_is_empty_not_an_error(client, seed):
    body = client.get("/api/landscape?target=IL-23").get_json()
    assert body["total"] == 0
    assert all(c["count"] == 0 and c["antibodies"] == [] for c in body["columns"])


def test_unknown_target_is_400_with_allowed_values(client, seed):
    r = client.get("/api/landscape?target=EGFR")
    assert r.status_code == 400
    err = r.get_json()["error"]
    assert err["code"] == "bad_request"
    assert "EGFR" in err["message"]
    assert err["details"]["allowed"] == ["PD-1", "PD-L1", "HER2", "CD20", "IL-23"]


def test_empty_target_is_400(client, seed):
    assert client.get("/api/landscape?target=").status_code == 400


def test_overview_counts_per_target_and_stage(client, seed):
    r = client.get("/api/landscape")
    assert r.status_code == 200
    body = r.get_json()

    assert [s["stage"] for s in body["stages"]] == [s["stage"] for s in STAGES]
    by_target = {t["target"]: t for t in body["targets"]}
    assert list(by_target) == ["PD-1", "PD-L1", "HER2", "CD20", "IL-23"]
    assert by_target["PD-1"]["counts"] == {
        "preclinical": 0, "phase_1": 1, "phase_2": 1, "phase_3": 0,
        "filed": 0, "approved": 1, "unknown": 0,
    }
    assert by_target["HER2"]["counts"]["phase_1"] == 1  # the bispecific
    assert by_target["HER2"]["counts"]["phase_3"] == 1
    assert by_target["CD20"]["counts"]["unknown"] == 1
    assert by_target["IL-23"]["total"] == 0
    # The bispecific counts under PD-1 and HER2, but once in the distinct total.
    assert sum(t["total"] for t in body["targets"]) == 7
    assert body["antibodies_total"] == 6


def test_filters_on_targets_array_with_gin_index(client, seed, query_log, engine):
    with query_log() as log:
        client.get("/api/landscape?target=HER2")
    i = next(i for i, sql in enumerate(log.statements) if "FROM antibody" in sql)
    sql, params = log.statements[i], log.params[i]
    assert "antibody.targets @>" in sql
    assert "antibody.target =" not in sql

    # Re-run the exact statement under EXPLAIN: with seq scans off (the
    # fixture table is tiny), Postgres answers it from the GIN index.
    with engine.connect() as conn:
        conn.exec_driver_sql("SET enable_seqscan = off")
        plan = "\n".join(row[0] for row in conn.exec_driver_sql(f"EXPLAIN {sql}", params))
    assert "ix_antibody_targets" in plan