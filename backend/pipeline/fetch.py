"""Fetch steps: download raw source data into data/raw/<run_id>/.

Every step follows the Lambda task contract: ``step(event: dict) -> dict``.
The returned dict is the input event plus whatever the step added, so steps
can be chained by a local runner or a Step Functions state machine.

Large payloads never travel in the event (Step Functions caps state at 256 KB);
steps pass file paths instead.

Order matters: CT.gov is queried per antibody, so ``fetch_ctgov`` runs after
``normalize.normalize_antibodies`` has written the curated antibody list.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import RAW_DATA_DIR

log = logging.getLogger(__name__)

THERASABDAB_CSV_URL = (
    "https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/static/downloads/"
    "TheraSAbDab_SeqStruc_OnlineDownload.csv"
)
CTGOV_STUDIES_URL = "https://clinicaltrials.gov/api/v2/studies"
CTGOV_VERSION_URL = "https://clinicaltrials.gov/api/v2/version"

# Only the fields normalize_trials reads. The response keeps the nested
# protocolSection layout; `fields` trims it, it does not flatten it.
CTGOV_FIELDS = ",".join(
    [
        "NCTId",
        "BriefTitle",
        "OverallStatus",
        "LastUpdatePostDate",
        "Phase",
        "EnrollmentCount",
        "LeadSponsorName",
    ]
)
CTGOV_MAX_TRIALS_PER_ANTIBODY = 50   # newest by last update; pembrolizumab alone has ~3000
CTGOV_REQUEST_DELAY_S = 0.3          # be polite between requests
HTTP_TIMEOUT_S = 30
USER_AGENT = "antibody-landscape-monitor/0.1 (portfolio project)"


def run_dir(run_id: int | str) -> Path:
    path = RAW_DATA_DIR / str(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session() -> requests.Session:
    """HTTP session that retries 429 and 5xx with exponential backoff
    (and honors Retry-After when the server sends it)."""
    retry = Retry(
        total=5,
        backoff_factor=1.0,  # 1s, 2s, 4s, 8s ...
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


# --------------------------------------------------------------------------
# Thera-SAbDab
# --------------------------------------------------------------------------

def fetch_therasabdab(event: dict[str, Any]) -> dict[str, Any]:
    """Download the full Thera-SAbDab CSV for this run.

    event in:  {"run_id"}
    event out: + {"therasabdab_path", "therasabdab_bytes"}
    """
    out_path = run_dir(event["run_id"]) / "therasabdab.csv"

    with _session() as session:
        resp = session.get(THERASABDAB_CSV_URL, timeout=HTTP_TIMEOUT_S)
        resp.raise_for_status()

    body = resp.content
    # If the download link moves, the server tends to return an HTML page with
    # a 200. Fail loudly instead of parsing a web page as CSV.
    if body.lstrip()[:1] == b"<":
        raise RuntimeError(
            f"Thera-SAbDab returned HTML instead of CSV; check {THERASABDAB_CSV_URL}"
        )

    out_path.write_bytes(body)
    log.info("thera-sabdab: wrote %s (%d bytes)", out_path, len(body))
    return {**event, "therasabdab_path": str(out_path), "therasabdab_bytes": len(body)}


# --------------------------------------------------------------------------
# ClinicalTrials.gov
# --------------------------------------------------------------------------

def fetch_ctgov_version(session: requests.Session) -> str | None:
    """CT.gov's dataTimestamp: when its data was last refreshed.
    Stored per run so a later run can skip CT.gov if nothing changed."""
    try:
        resp = session.get(CTGOV_VERSION_URL, timeout=HTTP_TIMEOUT_S)
        resp.raise_for_status()
        return resp.json().get("dataTimestamp")
    except requests.RequestException as exc:
        log.warning("ct.gov: version lookup failed: %s", exc)
        return None


def fetch_trials_for(
    session: requests.Session,
    inn_name: str,
    max_trials: int = CTGOV_MAX_TRIALS_PER_ANTIBODY,
) -> dict[str, Any]:
    """Newest `max_trials` studies whose intervention matches `inn_name`.

    Follows nextPageToken only until the cap is reached, so for the default
    cap of 50 this is a single request.
    """
    studies: list[dict[str, Any]] = []
    total_count: int | None = None
    page_token: str | None = None

    while len(studies) < max_trials:
        params: dict[str, Any] = {
            "query.intr": inn_name,
            "fields": CTGOV_FIELDS,
            "sort": "LastUpdatePostDate:desc",
            "pageSize": min(max_trials - len(studies), 1000),
            "countTotal": "true",
        }
        if page_token:
            params["pageToken"] = page_token

        resp = session.get(CTGOV_STUDIES_URL, params=params, timeout=HTTP_TIMEOUT_S)
        resp.raise_for_status()
        payload = resp.json()

        if total_count is None:
            total_count = payload.get("totalCount")
        studies.extend(payload.get("studies", []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            break
        time.sleep(CTGOV_REQUEST_DELAY_S)

    return {
        "query": inn_name,
        "total_count": total_count,
        "fetched": len(studies),
        "studies": studies[:max_trials],
    }


def ctgov_query_name(inn_name: str) -> str:
    """Thera-SAbDab suffixes multi-entry therapeutics with a digit
    ('pumitamig2'); CT.gov knows the drug by its plain INN ('pumitamig')."""
    return re.sub(r"\d+$", "", inn_name)


def fetch_ctgov(event: dict[str, Any]) -> dict[str, Any]:
    """Query CT.gov once per curated antibody and save one JSON file each.

    A failure for one antibody does not fail the run: it is recorded in
    `ctgov_failed`, and the runner can mark the run PARTIAL.

    event in:  {"run_id", "antibodies_path"}
    event out: + {"ctgov_dir", "ctgov_data_timestamp", "ctgov_fetched", "ctgov_failed"}
    """
    antibodies = json.loads(Path(event["antibodies_path"]).read_text())
    if event.get("ctgov_limit"):  # quick test runs: only the first N antibodies
        antibodies = antibodies[: int(event["ctgov_limit"])]
    max_trials = int(event.get("ctgov_max_trials", CTGOV_MAX_TRIALS_PER_ANTIBODY))

    out_dir = run_dir(event["run_id"]) / "ctgov"
    out_dir.mkdir(exist_ok=True)

    fetched: dict[str, int] = {}
    failed: dict[str, str] = {}

    with _session() as session:
        data_timestamp = fetch_ctgov_version(session)

        for i, ab in enumerate(antibodies, start=1):
            inn = ab["inn_name"]
            try:
                result = fetch_trials_for(session, ctgov_query_name(inn), max_trials=max_trials)
            except requests.RequestException as exc:
                log.warning("ct.gov: %s failed: %s", inn, exc)
                failed[inn] = str(exc)[:300]
                continue

            (out_dir / f"{inn}.json").write_text(json.dumps(result, indent=1))
            fetched[inn] = result["fetched"]
            log.info(
                "ct.gov: [%d/%d] %s -> %d of %s",
                i, len(antibodies), inn, result["fetched"], result["total_count"],
            )
            time.sleep(CTGOV_REQUEST_DELAY_S)

    return {
        **event,
        "ctgov_dir": str(out_dir),
        "ctgov_data_timestamp": data_timestamp,
        "ctgov_fetched": sum(fetched.values()),
        "ctgov_failed": failed,
    }