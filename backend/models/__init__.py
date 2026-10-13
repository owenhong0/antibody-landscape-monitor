"""Import every model here so Base.metadata knows all tables.

Alembic autogenerate only sees tables whose classes have been imported.
"""
from models.base import Base
from models.enums import (
    ChainType,
    EntityType,
    LiabilityKind,
    RunStatus,
    Severity,
    SignalKind,
)
from models.antibody import Antibody, AntibodySnapshot, Chain, Liability
from models.trial import Trial, TrialSnapshot, antibody_trial
from models.pipeline import PipelineRun, Signal

__all__ = [
    "Base",
    "Antibody",
    "AntibodySnapshot",
    "Chain",
    "Liability",
    "Trial",
    "TrialSnapshot",
    "antibody_trial",
    "PipelineRun",
    "Signal",
    "ChainType",
    "EntityType",
    "LiabilityKind",
    "RunStatus",
    "Severity",
    "SignalKind",
]