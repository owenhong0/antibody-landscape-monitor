"""A small, hand-made dataset for the API tests.

Names, sequences and NCT ids are fictional. Liabilities are not typed in by
hand: they come from pipeline.analyze.scan_chain, the same function the real
pipeline uses, so in_cdr3 flags and CDR3 bounds are consistent.

What it covers:
- 6 antibodies across every landscape column, incl. stage_rank NULL (unknown)
- Duotamab: a PD-1 x HER2 bispecific with 4 chains (H1, L1, H2, L2), one
  light chain without a CDR3 match, liabilities in and out of CDR3
- NCT09000001 linked to two antibodies (Exemplimab + Duotamab)
- 4 pipeline runs (succeeded, failed, partial, running) and 6 signals of every
  kind, one pointing at an entity that no longer exists
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from models import (
    Antibody,
    Chain,
    ChainType,
    EntityType,
    Liability,
    PipelineRun,
    RunStatus,
    Signal,
    SignalKind,
    Trial,
)
from pipeline.analyze import scan_chain

TABLES = (
    "signal, liability, chain, antibody_trial, trial_snapshot, antibody_snapshot, "
    "trial, antibody, pipeline_run"
)

# Variable domains: framework + CDR3 + framework 4 (WGQG heavy, FGQG light).
HEAVY_FR = (
    "EVQLVESGGGLVQPGGSLRLSCAASGFTFSNSAMSWVRQAPGKGLEWVSAISGSGGSTYYADSVKG"
    "RFTISRDNSKNTLYLQMNSLRAEDTAVYYC"
)
LIGHT_FR = "DIQMTQSPSSLSASVGDRVTITCRASQGISSWLAWYQQKPGKAPKLLIYAASSLQSGVPSRFSGSGSGTDFTLTISSLQPEDFATYYC"

SEQUENCES = {
    # CDR3 "ARDNGYMSSFDY": NG (deamidation, high) and M (oxidation) inside CDR3
    "exemplimab_H1": HEAVY_FR + "ARDNGYMSSFDY" + "WGQGTLVTVSS",
    # CDR3 "QQYDSYPLT": DS (isomerization) inside CDR3
    "exemplimab_L1": LIGHT_FR + "QQYDSYPLT" + "FGQGTKVEIK",
    "duotamab_H1": HEAVY_FR + "AKWGNSTYFDV" + "WGQGTLVTVSS",
    "duotamab_L1": LIGHT_FR + "QQSYSTPWT" + "FGQGTKVEIK",
    # extra C in framework 1 -> odd cysteine count -> unpaired_cysteine
    "duotamab_H2": "EVQLVESGGCLVQPGGSLRLSCAASGFNISDTYIHWVRQAPGKGLEWVARIYPTNGYTRYADSVKG"
                   "RFTISADTSKNTAYLQMNSLRAEDTAVYYC" + "SRWGGDGFYAMDY" + "WGQGTLVTVSS",
    # no F-G-x-G motif: CDR3 regex misses, like the 3 real chains without a match
    "duotamab_L2": LIGHT_FR + "QQHYTTPPT" + "WGAGTKVEIK",
}


@dataclass
class Seed:
    """Ids the tests need. Use these instead of hard-coding numbers."""

    runs: dict[str, int]
    antibodies: dict[str, int]
    trials: dict[str, int]
    signals: dict[str, int]


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def make_chain(key: str, chain_type: ChainType, index: int) -> Chain:
    sequence = SEQUENCES[key]
    cdr3, found = scan_chain(sequence, chain_type)
    chain = Chain(
        chain_type=chain_type,
        chain_index=index,
        sequence=sequence,
        length=len(sequence),
        cdr3_sequence=cdr3.sequence if cdr3 else None,
        cdr3_len=len(cdr3.sequence) if cdr3 else None,
    )
    # Insert in reverse so the API's position ordering is actually exercised.
    chain.liabilities = [Liability(**li) for li in reversed(found)]
    return chain


def antibody(name: str, targets: list[str], stage: str, rank: int | None, fmt: str = "Whole mAb",
             status: str = "active") -> Antibody:
    return Antibody(
        inn_name=name.lower(),
        display_name=name,
        target=targets[0],
        targets=targets,
        format=fmt,
        clinical_stage=stage,
        stage_rank=rank,
        status=status,
        source_url=f"https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/therasabdab/search/?therapeutic={name}",
    )


def reset(session: Session) -> None:
    session.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))


def seed(session: Session) -> Seed:
    reset(session)

    # --- runs -------------------------------------------------------------
    run_ok = PipelineRun(
        status=RunStatus.SUCCEEDED,
        started_at=utc(2026, 10, 11, 9, 0),
        finished_at=utc(2026, 10, 11, 9, 1, 5, 500000),
        counts={"antibodies": 6, "chains": 6, "trials": 4, "signals": 3},
        raw_location="data/raw/1",
    )
    run_failed = PipelineRun(
        status=RunStatus.FAILED,
        started_at=utc(2026, 10, 12, 9, 0),
        finished_at=utc(2026, 10, 12, 9, 0, 2),
        counts={"antibodies": 6, "signals": 0},
        raw_location="data/raw/2",
        error="fetch_ctgov: 503 Service Unavailable",
    )
    run_partial = PipelineRun(
        status=RunStatus.PARTIAL,
        started_at=utc(2026, 10, 13, 9, 0),
        finished_at=utc(2026, 10, 13, 9, 1, 10, 250000),
        counts={"antibodies": 6, "trials": 4, "ctgov_failed": 1, "signals": 3},
        raw_location="data/raw/3",
    )
    run_running = PipelineRun(
        status=RunStatus.RUNNING, started_at=utc(2026, 10, 13, 12, 0), counts={},
        raw_location="data/raw/4",
    )
    session.add_all([run_ok, run_failed, run_partial, run_running])

    # --- antibodies -------------------------------------------------------
    exemplimab = antibody("Exemplimab", ["PD-1"], "approved", 5)
    exemplimab.chains = [
        make_chain("exemplimab_H1", ChainType.HEAVY, 1),
        make_chain("exemplimab_L1", ChainType.LIGHT, 1),
    ]
    duotamab = antibody("Duotamab", ["PD-1", "HER2"], "phase_1", 1, fmt="Bispecific")
    duotamab.chains = [
        make_chain("duotamab_H1", ChainType.HEAVY, 1),
        make_chain("duotamab_L1", ChainType.LIGHT, 1),
        make_chain("duotamab_H2", ChainType.HEAVY, 2),
        make_chain("duotamab_L2", ChainType.LIGHT, 2),
    ]
    fixturimab = antibody("Fixturimab", ["PD-1"], "phase_2", 2)
    prelimab = antibody("Prelimab", ["PD-L1"], "preclinical", 0)
    testuzumab = antibody("Testuzumab", ["HER2"], "phase_3", 3, status="discontinued")
    nullimab = antibody("Nullimab", ["CD20"], "unknown", None, status="unknown")
    session.add_all([exemplimab, duotamab, fixturimab, prelimab, testuzumab, nullimab])

    # --- trials -----------------------------------------------------------
    combo = Trial(nct_id="NCT09000001", title="Exemplimab plus Duotamab in solid tumors",
                  sponsor="Example Bio", status="RECRUITING", phase="PHASE2", enrollment=120,
                  last_update_date=date(2026, 10, 1))
    duo_p1 = Trial(nct_id="NCT09000002", title="First-in-human study of Duotamab",
                   sponsor="Example Bio", status="RECRUITING", phase="PHASE1", enrollment=40,
                   last_update_date=date(2026, 9, 15))
    duo_nodate = Trial(nct_id="NCT09000003", title="Duotamab expanded access",
                       sponsor="Example Bio", status="AVAILABLE", phase=None, enrollment=None,
                       last_update_date=None)
    exe_old = Trial(nct_id="NCT09000004", title="Exemplimab adjuvant study",
                    sponsor="Example Pharma", status="COMPLETED", phase="PHASE3", enrollment=950,
                    last_update_date=date(2025, 3, 2))
    exemplimab.trials = [exe_old, combo]
    duotamab.trials = [duo_nodate, combo, duo_p1]
    session.add_all([combo, duo_p1, duo_nodate, exe_old])
    session.flush()  # assigns ids, needed for the signals' entity_id

    # --- signals ----------------------------------------------------------
    def sig(run, kind, entity_type, entity_id, title, old, new, url, at, seen_at=None):
        return Signal(run_id=run.id, kind=kind, entity_type=entity_type, entity_id=entity_id,
                      title=title, old_value=old, new_value=new, source_url=url,
                      created_at=at, seen=seen_at is not None, seen_at=seen_at)

    ct = "https://clinicaltrials.gov/study/{}".format
    signals = {
        "status_changed": sig(run_partial, SignalKind.TRIAL_STATUS_CHANGED, EntityType.TRIAL, combo.id,
                              "NCT09000001 moved RECRUITING -> ACTIVE_NOT_RECRUITING",
                              "RECRUITING", "ACTIVE_NOT_RECRUITING", ct("NCT09000001"),
                              utc(2026, 10, 13, 9, 1, 10)),
        "new_trial": sig(run_partial, SignalKind.NEW_TRIAL, EntityType.TRIAL, duo_p1.id,
                         "New trial NCT09000002 for Duotamab", None, "RECRUITING", ct("NCT09000002"),
                         utc(2026, 10, 13, 9, 1, 5)),
        "stage_changed": sig(run_partial, SignalKind.ANTIBODY_STAGE_CHANGED, EntityType.ANTIBODY,
                             fixturimab.id, "Fixturimab moved phase_1 -> phase_2", "phase_1", "phase_2",
                             fixturimab.source_url, utc(2026, 10, 13, 9, 1, 0)),
        "phase_changed": sig(run_ok, SignalKind.TRIAL_PHASE_CHANGED, EntityType.TRIAL, exe_old.id,
                             "NCT09000004 moved PHASE2 -> PHASE3", "PHASE2", "PHASE3", ct("NCT09000004"),
                             utc(2026, 10, 11, 9, 1, 30)),
        "new_antibody": sig(run_ok, SignalKind.NEW_ANTIBODY, EntityType.ANTIBODY, duotamab.id,
                            "New PD-1 / HER2 antibody: Duotamab", None, "phase_1", duotamab.source_url,
                            utc(2026, 10, 11, 9, 1, 0), seen_at=utc(2026, 10, 11, 15, 0)),
        "orphan": sig(run_ok, SignalKind.NEW_TRIAL, EntityType.TRIAL, 999_999,
                      "New trial NCT09999999 for Exemplimab", None, "WITHDRAWN", ct("NCT09999999"),
                      utc(2026, 10, 11, 9, 0, 30)),
    }
    session.add_all(signals.values())
    session.commit()

    return Seed(
        runs={"succeeded": run_ok.id, "failed": run_failed.id, "partial": run_partial.id,
              "running": run_running.id},
        antibodies={ab.display_name: ab.id for ab in
                    (exemplimab, duotamab, fixturimab, prelimab, testuzumab, nullimab)},
        trials={t.nct_id: t.id for t in (combo, duo_p1, duo_nodate, exe_old)},
        signals={k: s.id for k, s in signals.items()},
    )