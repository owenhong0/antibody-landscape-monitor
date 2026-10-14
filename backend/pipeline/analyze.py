"""Analyze step: scan every heavy and light chain for developability liabilities.

Deliberately lightweight and explainable: regexes over the variable-domain
sequence, no residue numbering. Production tools number residues with a
scheme (IMGT, Chothia, Kabat via e.g. ANARCI) to place CDRs exactly; the
CDR3 patterns below are an approximation anchored on conserved residues.

Rules (severity in brackets):
    N-linked glycosylation  N[^P][ST]          [high]
    Deamidation             NG [high], NS      [medium]
    Isomerization           DG, DS             [medium]
    Unpaired cysteine       odd number of C    [high]   the non-canonical C(s)
    Oxidation               M, W inside CDR3   [low]

Liabilities are derived data: they can always be recomputed from the
sequence, so each run replaces a chain's liabilities instead of keeping history.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, insert, select

from db import session_scope
from models import Chain, ChainType, Liability, LiabilityKind, Severity

log = logging.getLogger(__name__)

# CDR3 = the residues between the conserved Cys and the conserved
# W-G-x-G (heavy) / F-G-x-G (light) motif that starts framework 4.
CDR3_PATTERNS = {
    ChainType.HEAVY: re.compile(r"C(.{3,25}?)WG.G"),
    ChainType.LIGHT: re.compile(r"C(.{3,15}?)FG.G"),
}

# Lookaheads so overlapping motifs are all found (e.g. "NNG" -> NG at 2).
MOTIF_RULES: list[tuple[LiabilityKind, re.Pattern[str], Any]] = [
    (LiabilityKind.N_GLYCOSYLATION, re.compile(r"(?=(N[^P][ST]))"), lambda m: Severity.HIGH),
    (
        LiabilityKind.DEAMIDATION,
        re.compile(r"(?=(N[GS]))"),
        lambda m: Severity.HIGH if m == "NG" else Severity.MEDIUM,
    ),
    (LiabilityKind.ISOMERIZATION, re.compile(r"(?=(D[GS]))"), lambda m: Severity.MEDIUM),
]

CANONICAL_CYS_WINDOW = 30  # the first framework Cys sits around position 22-23


@dataclass(frozen=True)
class Cdr3:
    start: int  # 0-based index of the first CDR3 residue
    end: int    # 0-based, exclusive
    sequence: str

    def contains(self, index: int, length: int = 1) -> bool:
        """True if any residue of a motif at `index` falls inside CDR3."""
        return index < self.end and index + length > self.start


def find_cdr3(sequence: str, chain_type: ChainType) -> Cdr3 | None:
    match = CDR3_PATTERNS[chain_type].search(sequence)
    if not match:
        return None
    return Cdr3(start=match.start(1), end=match.end(1), sequence=match.group(1))


def scan_chain(sequence: str, chain_type: ChainType) -> tuple[Cdr3 | None, list[dict[str, Any]]]:
    """Pure function: sequence in, (CDR3, liability dicts) out. No database,
    so it is easy to unit test."""
    cdr3 = find_cdr3(sequence, chain_type)
    found: list[dict[str, Any]] = []

    def add(kind: LiabilityKind, motif: str, index: int, severity: Severity) -> None:
        found.append(
            {
                "kind": kind,
                "motif": motif,
                "position": index + 1,  # 1-based for humans
                "in_cdr3": bool(cdr3 and cdr3.contains(index, len(motif))),
                "severity": severity,
            }
        )

    # Sequence motifs anywhere in the chain
    for kind, pattern, severity_for in MOTIF_RULES:
        for m in pattern.finditer(sequence):
            motif = m.group(1)
            add(kind, motif, m.start(), severity_for(motif))

    # Unpaired cysteine: a variable domain normally has exactly two Cys that
    # form the intra-domain disulfide (one early in framework 1, one just
    # before CDR3). With an odd count, flag every Cys that isn't one of those.
    cys = [i for i, aa in enumerate(sequence) if aa == "C"]
    if len(cys) % 2 == 1:
        canonical = set()
        if cys and cys[0] < CANONICAL_CYS_WINDOW:
            canonical.add(cys[0])
        if cdr3:
            canonical.add(cdr3.start - 1)  # the C the CDR3 pattern anchored on
        extras = [i for i in cys if i not in canonical] or [cys[-1]]
        for i in extras:
            add(LiabilityKind.UNPAIRED_CYSTEINE, "C", i, Severity.HIGH)

    # Oxidation only matters where it can disrupt binding: inside CDR3
    if cdr3:
        for offset, aa in enumerate(cdr3.sequence):
            if aa in "MW":
                add(LiabilityKind.OXIDATION, aa, cdr3.start + offset, Severity.LOW)

    found.sort(key=lambda r: (r["position"], r["kind"].value))
    return cdr3, found


def analyze_chains(event: dict[str, Any]) -> dict[str, Any]:
    """Scan every chain in the database; refresh CDR3 fields and liabilities.

    Runs after load_antibodies. 357 chains take well under a second, so it
    simply re-analyzes everything rather than tracking which chains changed.

    event in:  {"run_id"}
    event out: + {"analyzed_chains", "liabilities", "liabilities_by_kind",
                  "liabilities_by_severity", "chains_without_cdr3"}
    """
    by_kind: Counter[str] = Counter()
    by_severity: Counter[str] = Counter()
    no_cdr3 = 0
    total = 0

    with session_scope() as session:
        chains = session.scalars(select(Chain)).all()

        rows: list[dict[str, Any]] = []
        for chain in chains:
            cdr3, liabilities = scan_chain(chain.sequence, ChainType(chain.chain_type))
            chain.cdr3_sequence = cdr3.sequence[:60] if cdr3 else None
            chain.cdr3_len = len(cdr3.sequence) if cdr3 else None
            no_cdr3 += cdr3 is None
            for lia in liabilities:
                rows.append({"chain_id": chain.id, **lia})
                by_kind[lia["kind"].value] += 1
                by_severity[lia["severity"].value] += 1

        # Replace, don't accumulate: derived data, recomputed every run.
        chain_ids = [c.id for c in chains]
        if chain_ids:
            session.execute(delete(Liability).where(Liability.chain_id.in_(chain_ids)))
        if rows:
            session.execute(insert(Liability), rows)
        total = len(rows)

    log.info(
        "analyze: %d chains, %d liabilities %s, %d chains without CDR3 match",
        len(chain_ids), total, dict(by_severity), no_cdr3,
    )
    return {
        **event,
        "analyzed_chains": len(chain_ids),
        "liabilities": total,
        "liabilities_by_kind": dict(by_kind),
        "liabilities_by_severity": dict(by_severity),
        "chains_without_cdr3": no_cdr3,
    }