"""Enum values shared by models, pipeline and API.

Each inherits from str, so values serialize straight to JSON ("H", "high", ...).
"""
import enum


class ChainType(str, enum.Enum):
    HEAVY = "H"
    LIGHT = "L"


class LiabilityKind(str, enum.Enum):
    N_GLYCOSYLATION = "n_glycosylation"
    DEAMIDATION = "deamidation"
    ISOMERIZATION = "isomerization"
    UNPAIRED_CYSTEINE = "unpaired_cysteine"
    OXIDATION = "oxidation"


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"      # some steps failed (e.g. CT.gov rate-limited) but data was loaded
    FAILED = "failed"


class SignalKind(str, enum.Enum):
    TRIAL_STATUS_CHANGED = "TRIAL_STATUS_CHANGED"
    TRIAL_PHASE_CHANGED = "TRIAL_PHASE_CHANGED"
    NEW_TRIAL = "NEW_TRIAL"
    ANTIBODY_STAGE_CHANGED = "ANTIBODY_STAGE_CHANGED"
    NEW_ANTIBODY = "NEW_ANTIBODY"


class EntityType(str, enum.Enum):
    ANTIBODY = "antibody"
    TRIAL = "trial"