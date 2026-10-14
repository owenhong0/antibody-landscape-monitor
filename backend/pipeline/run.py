"""Local pipeline runner: the same steps Step Functions runs as Lambdas.

    cd backend && source .venv/bin/activate
    python -m pipeline.run                     # full run
    python -m pipeline.run --ctgov-limit 5     # quick test: CT.gov for 5 antibodies only
    python -m pipeline.run --skip-ctgov        # antibodies + chain analysis only, no CT.gov calls

Creates a pipeline_run row, passes one event dict through every step in order,
and records the outcome (succeeded / partial / failed) with counts.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable

from config import RAW_DATA_DIR
from db import session_scope
from models import PipelineRun, RunStatus
from pipeline.analyze import analyze_chains
from pipeline.diff import diff_signals
from pipeline.fetch import fetch_ctgov, fetch_therasabdab
from pipeline.load import load_antibodies, load_trials
from pipeline.normalize import normalize_antibodies, normalize_trials

log = logging.getLogger("pipeline")

Step = Callable[[dict[str, Any]], dict[str, Any]]

ANTIBODY_STEPS: list[Step] = [fetch_therasabdab, normalize_antibodies, load_antibodies, analyze_chains]
TRIAL_STEPS: list[Step] = [fetch_ctgov, normalize_trials, load_trials]
FINAL_STEPS: list[Step] = [diff_signals]  # runs last, after whatever was loaded


def start_run() -> int:
    with session_scope() as session:
        run = PipelineRun(status=RunStatus.RUNNING, counts={})
        session.add(run)
        session.flush()
        run.raw_location = str(RAW_DATA_DIR / str(run.id))
        return run.id


def finish_run(run_id: int, status: RunStatus, counts: dict[str, Any], error: str | None = None) -> None:
    with session_scope() as session:
        run = session.get(PipelineRun, run_id)
        run.status = status
        run.counts = counts
        run.error = error
        run.finished_at = datetime.now(timezone.utc)


def summarize(event: dict[str, Any]) -> dict[str, Any]:
    """The numbers shown on the dashboard's Runs page."""
    return {
        "antibodies": event.get("loaded_antibodies", 0),
        "antibodies_by_target": event.get("antibody_counts_by_target", {}),
        "chains": event.get("loaded_chains", 0),
        "liabilities": event.get("liabilities", 0),
        "liabilities_by_severity": event.get("liabilities_by_severity", {}),
        "chains_without_cdr3": event.get("chains_without_cdr3", 0),
        "trials": event.get("loaded_trials", 0),
        "antibody_trial_links": event.get("loaded_links", 0),
        "ctgov_failed": len(event.get("ctgov_failed", {})),
        "ctgov_data_timestamp": event.get("ctgov_data_timestamp"),
        "signals": event.get("signals", 0),
        "signals_by_kind": event.get("signals_by_kind", {}),
        "step_seconds": event.get("step_seconds", {}),
    }


def run_pipeline(ctgov_limit: int | None = None, skip_ctgov: bool = False) -> int:
    steps = ANTIBODY_STEPS + ([] if skip_ctgov else TRIAL_STEPS) + FINAL_STEPS
    run_id = start_run()
    log.info("run %d started (%d steps)", run_id, len(steps))

    event: dict[str, Any] = {"run_id": run_id, "step_seconds": {}}
    if ctgov_limit:
        event["ctgov_limit"] = ctgov_limit

    for step in steps:
        name = step.__name__
        started = time.monotonic()
        try:
            event = step(event)
        except Exception as exc:  # record the failure on the run, then stop
            log.exception("run %d failed in %s", run_id, name)
            event["step_seconds"][name] = round(time.monotonic() - started, 2)
            finish_run(
                run_id,
                RunStatus.FAILED,
                summarize(event),
                error=f"{name}: {exc}\n{traceback.format_exc(limit=5)}",
            )
            return run_id
        event["step_seconds"][name] = round(time.monotonic() - started, 2)
        log.info("run %d: %s done in %.1fs", run_id, name, event["step_seconds"][name])

    # Some antibodies' CT.gov fetch failed: data is loaded, but incomplete.
    status = RunStatus.PARTIAL if event.get("ctgov_failed") else RunStatus.SUCCEEDED
    finish_run(run_id, status, summarize(event))
    log.info("run %d %s: %s", run_id, status.value, summarize(event))
    return run_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the antibody landscape pipeline locally.")
    parser.add_argument("--ctgov-limit", type=int, help="only query CT.gov for the first N antibodies")
    parser.add_argument("--skip-ctgov", action="store_true", help="skip the CT.gov steps")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    run_id = run_pipeline(ctgov_limit=args.ctgov_limit, skip_ctgov=args.skip_ctgov)

    with session_scope() as session:
        status = session.get(PipelineRun, run_id).status
    sys.exit(0 if status in (RunStatus.SUCCEEDED, RunStatus.PARTIAL) else 1)


if __name__ == "__main__":
    main()