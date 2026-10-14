"""Normalize steps: raw source files -> clean dicts shaped like the models.

Output is written as JSON next to the raw files (data/raw/<run_id>/), and the
event carries only the paths. The load step turns these dicts into rows.

Mapping decisions are documented in the project doc `claude/data-mapping.md`.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd

from pipeline.fetch import run_dir

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Thera-SAbDab vocabulary
# --------------------------------------------------------------------------

# Curated target buckets. A row matches a bucket when any "/"-separated alias
# of any ";"-separated arm equals one of these gene symbols.
TARGET_BUCKETS: dict[str, set[str]] = {
    "PD-1": {"PDCD1"},
    "PD-L1": {"CD274"},
    "HER2": {"ERBB2"},
    "CD20": {"MS4A1"},
    "IL-23": {"IL23A", "IL12B"},  # IL12B = p40, shared IL-12/IL-23 (e.g. ustekinumab)
}
VETERINARY_MARKERS = ("(canine)", "(feline)")

# Raw "Highest_Clin_Trial" value -> (normalized stage, rank). Rank orders the
# landscape columns; None means unknown and sorts last.
STAGE_MAP: dict[str, tuple[str, int | None]] = {
    "preclinical": ("preclinical", 0),
    "phase-i": ("phase_1", 1),
    "phase-i/ii": ("phase_2", 2),
    "phase-i/phase-ii": ("phase_2", 2),
    "phase-ii": ("phase_2", 2),
    "phase-ii/iii": ("phase_3", 3),
    "phase-iii": ("phase_3", 3),
    "preregistration": ("filed", 4),
    "approved": ("approved", 5),
}
WITHDRAWN_SUFFIX = re.compile(r"\s*\((w|withdrawn)\)\s*$", re.IGNORECASE)

STATUS_MAP = {"active": "active", "discontinued": "discontinued"}

MISSING = {"", "na", "n/a", "none", "tbc", "unknown", "nd"}

MIN_SEQUENCE_LEN = 50  # a few rows hold placeholders (len 2) instead of a sequence
AMINO_ACIDS = re.compile(r"^[ACDEFGHIKLMNPQRSTVWYX]+$")

# Thera-SAbDab name search. TODO: confirm in a browser that this opens the
# entry; the source_url column is required for every signal.
THERASABDAB_ENTRY_URL = (
    "https://opig.stats.ox.ac.uk/webapps/sabdab-sabpred/therasabdab/search/"
    "?therapeutic={name}"
)

COL_NAME = "Therapeutic"
COL_FORMAT = "Format"
COL_STATUS = "Est. Status"
COL_TARGET = "Target"
COL_COMPANIES = "Companies"
COL_ALIASES = "Alternative Therapeutic Names"
COL_HEAVY = "HeavySequence"
COL_LIGHT = "LightSequence"
COL_HEAVY_2 = "HeavySequence(ifbispec)"
COL_LIGHT_2 = "LightSequence(ifbispec)"
STAGE_PREFIX = "Highest_Clin_Trial"  # full name carries a date, e.g. "(Feb '25)"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def clean(value: Any) -> str | None:
    """Strip a raw cell; map every missing-value token to None."""
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in MISSING else text


def split_list(value: Any, sep: str = ";") -> list[str]:
    text = clean(value)
    if text is None:
        return []
    return [part for part in (p.strip() for p in text.split(sep)) if clean(part)]


def content_hash(record: dict[str, Any]) -> str:
    """sha256 of a canonical JSON encoding. Same content -> same hash, so the
    diff step can skip unchanged records without comparing field by field."""
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def target_buckets(target_raw: str | None) -> list[str]:
    """'PDCD1/CD279/PD1;ERBB2/CD340/HER2' -> ['PD-1', 'HER2']."""
    if not target_raw or any(m in target_raw.lower() for m in VETERINARY_MARKERS):
        return []
    symbols = {
        alias.strip().split(" ")[0].upper()  # "ERBB2/CD340/HER2 (Domain II)" -> ERBB2
        for arm in target_raw.split(";")
        for alias in arm.split("/")
        if alias.strip()
    }
    return [bucket for bucket, genes in TARGET_BUCKETS.items() if symbols & genes]


def normalize_stage(raw: str | None) -> dict[str, Any]:
    if raw is None:
        return {"clinical_stage": "unknown", "stage_rank": None, "withdrawn": False}
    withdrawn = bool(WITHDRAWN_SUFFIX.search(raw))
    key = WITHDRAWN_SUFFIX.sub("", raw).strip().lower()
    stage, rank = STAGE_MAP.get(key, ("unknown", None))
    if stage == "unknown" and clean(raw) is not None:
        log.warning("thera-sabdab: unmapped stage %r", raw)
    return {"clinical_stage": stage, "stage_rank": rank, "withdrawn": withdrawn}


def clean_sequence(value: Any) -> str | None:
    seq = clean(value)
    if seq is None:
        return None
    seq = re.sub(r"\s+", "", seq).upper()
    if len(seq) < MIN_SEQUENCE_LEN or not AMINO_ACIDS.match(seq):
        return None
    return seq


def build_chains(row: pd.Series) -> list[dict[str, Any]]:
    """Up to four chains: (H,1) (L,1) from the main columns, (H,2) (L,2)
    from the bispecific columns. Variable domains only."""
    slots = [
        ("H", 1, COL_HEAVY),
        ("L", 1, COL_LIGHT),
        ("H", 2, COL_HEAVY_2),
        ("L", 2, COL_LIGHT_2),
    ]
    chains = []
    for chain_type, index, col in slots:
        seq = clean_sequence(row.get(col))
        if seq:
            chains.append(
                {"chain_type": chain_type, "chain_index": index, "sequence": seq, "length": len(seq)}
            )
    return chains


# --------------------------------------------------------------------------
# Step: antibodies
# --------------------------------------------------------------------------

def normalize_antibody_row(row: pd.Series, stage_col: str) -> dict[str, Any] | None:
    name = clean(row.get(COL_NAME))
    target_raw = clean(row.get(COL_TARGET))
    buckets = target_buckets(target_raw)
    if not name or not buckets:
        return None

    fmt = clean(row.get(COL_FORMAT))
    if fmt and fmt.lower().startswith(("canine", "feline")):
        return None

    stage_raw = clean(row.get(stage_col))
    status_raw = clean(row.get(COL_STATUS))
    chains = build_chains(row)

    record: dict[str, Any] = {
        "inn_name": name.lower(),
        "display_name": name,
        "target": buckets[0],          # primary bucket (antibody.target column)
        "targets": buckets,            # all buckets; bispecifics can hit two
        "target_raw": target_raw,
        "format": fmt,
        "is_bispecific": any(c["chain_index"] == 2 for c in chains),
        "stage_raw": stage_raw,
        **normalize_stage(stage_raw),
        "status": STATUS_MAP.get((status_raw or "").lower(), "unknown"),
        "status_raw": status_raw,
        "aliases": split_list(row.get(COL_ALIASES)),
        "companies": split_list(row.get(COL_COMPANIES)),
        "source_url": THERASABDAB_ENTRY_URL.format(name=quote(name)),
        "chains": chains,
    }
    # Hash only what a signal should fire on. Companies and aliases churn
    # without meaning anything for the landscape.
    record["content_hash"] = content_hash(
        {
            "stage_raw": stage_raw,
            "status_raw": status_raw,
            "targets": buckets,
            "format": fmt,
            "chains": [(c["chain_type"], c["chain_index"], c["sequence"]) for c in chains],
        }
    )
    return record


def normalize_antibodies(event: dict[str, Any]) -> dict[str, Any]:
    """Thera-SAbDab CSV -> curated antibody dicts (with chains).

    event in:  {"run_id", "therasabdab_path"}
    event out: + {"antibodies_path", "antibody_count", "chain_count", "antibody_skipped"}
    """
    df = pd.read_csv(event["therasabdab_path"], dtype=str, keep_default_na=False)

    stage_cols = [c for c in df.columns if c.startswith(STAGE_PREFIX)]
    missing = [c for c in (COL_NAME, COL_TARGET, COL_HEAVY, COL_LIGHT) if c not in df.columns]
    if missing or not stage_cols:
        raise ValueError(
            f"Thera-SAbDab columns changed: missing {missing or [STAGE_PREFIX + '*']}; "
            f"got {list(df.columns)}"
        )
    stage_col = stage_cols[0]

    by_inn: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for _, row in df.iterrows():
        record = normalize_antibody_row(row, stage_col)
        if record is None:
            continue
        if record["inn_name"] in by_inn:  # inn_name is unique in the DB
            duplicates += 1
            log.warning("thera-sabdab: duplicate INN %s, keeping first", record["inn_name"])
            continue
        by_inn[record["inn_name"]] = record

    antibodies = sorted(by_inn.values(), key=lambda r: r["inn_name"])
    no_chains = [a["inn_name"] for a in antibodies if not a["chains"]]
    if no_chains:
        log.warning("thera-sabdab: %d antibodies without a usable sequence: %s",
                    len(no_chains), no_chains)

    out_path = run_dir(event["run_id"]) / "antibodies.json"
    out_path.write_text(json.dumps(antibodies, indent=1))

    counts_by_target: dict[str, int] = {}
    for ab in antibodies:
        for t in ab["targets"]:
            counts_by_target[t] = counts_by_target.get(t, 0) + 1
    log.info("thera-sabdab: %d antibodies %s", len(antibodies), counts_by_target)

    return {
        **event,
        "antibodies_path": str(out_path),
        "antibody_count": len(antibodies),
        "antibody_counts_by_target": counts_by_target,
        "chain_count": sum(len(a["chains"]) for a in antibodies),
        "antibody_skipped": {"duplicate_inn": duplicates, "no_chains": len(no_chains)},
    }


# --------------------------------------------------------------------------
# Step: trials
# --------------------------------------------------------------------------

def normalize_study(study: dict[str, Any]) -> dict[str, Any] | None:
    """One CT.gov v2 study -> trial dict. Missing modules become None."""
    ps = study.get("protocolSection", {})
    ident = ps.get("identificationModule", {})
    status = ps.get("statusModule", {})
    design = ps.get("designModule", {})
    sponsor = ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})

    nct_id = ident.get("nctId")
    if not nct_id:
        return None

    phases = design.get("phases") or []
    record = {
        "nct_id": nct_id,
        "title": ident.get("briefTitle"),
        "sponsor": sponsor.get("name"),
        "status": status.get("overallStatus"),          # e.g. RECRUITING
        "phase": "|".join(phases) or None,              # e.g. PHASE1|PHASE2, NA
        "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
        "last_update_date": (status.get("lastUpdatePostDateStruct") or {}).get("date"),
        "source_url": f"https://clinicaltrials.gov/study/{nct_id}",
    }
    # last_update_date is left out of the hash: it moves on every edit, even
    # ones we don't signal on. Status, phase and enrollment are what matter.
    record["content_hash"] = content_hash(
        {k: record[k] for k in ("title", "sponsor", "status", "phase", "enrollment")}
    )
    return record


def normalize_trials(event: dict[str, Any]) -> dict[str, Any]:
    """CT.gov JSON files -> deduplicated trial dicts with linked INNs.

    One trial often appears under several antibodies (combination studies),
    so trials are keyed by NCT ID and collect every INN that matched them.

    event in:  {"run_id", "ctgov_dir"}
    event out: + {"trials_path", "trial_count", "antibody_trial_links"}
    """
    trials: dict[str, dict[str, Any]] = {}
    links = 0

    for path in sorted(Path(event["ctgov_dir"]).glob("*.json")):
        payload = json.loads(path.read_text())
        inn = path.stem
        for study in payload.get("studies", []):
            record = normalize_study(study)
            if record is None:
                continue
            existing = trials.setdefault(record["nct_id"], {**record, "inn_names": []})
            # Same trial fetched under several antibodies: if it was updated
            # between those requests, keep the newest version of its fields.
            if (record["last_update_date"] or "") > (existing["last_update_date"] or ""):
                existing.update(record)
            if inn not in existing["inn_names"]:
                existing["inn_names"].append(inn)
                links += 1

    out = sorted(trials.values(), key=lambda t: t["nct_id"])
    out_path = run_dir(event["run_id"]) / "trials.json"
    out_path.write_text(json.dumps(out, indent=1))
    log.info("ct.gov: %d unique trials, %d antibody-trial links", len(out), links)

    return {
        **event,
        "trials_path": str(out_path),
        "trial_count": len(out),
        "antibody_trial_links": links,
    }