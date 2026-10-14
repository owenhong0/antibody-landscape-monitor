"""Antibody -> chains -> liabilities. The multi-chain part of the schema."""
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Index,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin, str_enum
from models.enums import ChainType, LiabilityKind, Severity


class Antibody(TimestampMixin, Base):
    __tablename__ = "antibody"
    __table_args__ = (
        # Landscape filter: WHERE targets @> ARRAY['PD-1']
        Index("ix_antibody_targets", "targets", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Normalized INN, e.g. "pembrolizumab". The join key to ClinicalTrials.gov.
    inn_name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    target: Mapped[str] = mapped_column(String(120), index=True)   # primary bucket, e.g. "PD-1"
    # Every curated bucket the antibody hits. A PD-1 x HER2 bispecific is ["PD-1", "HER2"].
    targets: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), default=list, server_default="{}"
    )
    format: Mapped[str | None] = mapped_column(String(120))          # e.g. "Whole mAb", "Bispecific"
    clinical_stage: Mapped[str | None] = mapped_column(String(60))   # current value, mirrors latest snapshot
    # Orders landscape columns: 0 preclinical .. 5 approved; NULL = unknown.
    stage_rank: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(40))           # active / discontinued / unknown
    source_url: Mapped[str] = mapped_column(Text)

    chains: Mapped[list["Chain"]] = relationship(
        back_populates="antibody",
        cascade="all, delete-orphan",
        order_by="(Chain.chain_type, Chain.chain_index)",
    )
    snapshots: Mapped[list["AntibodySnapshot"]] = relationship(
        back_populates="antibody", cascade="all, delete-orphan"
    )
    trials: Mapped[list["Trial"]] = relationship(  # noqa: F821
        secondary="antibody_trial", back_populates="antibodies"
    )

    def __repr__(self) -> str:
        return f"<Antibody {self.inn_name} ({self.target})>"


class Chain(Base):
    """One heavy or light chain. Bispecifics can have several of each,
    which is why chain_index exists: (antibody, H, 1), (antibody, H, 2) ..."""

    __tablename__ = "chain"
    __table_args__ = (
        UniqueConstraint("antibody_id", "chain_type", "chain_index", name="uq_chain_position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    antibody_id: Mapped[int] = mapped_column(
        ForeignKey("antibody.id", ondelete="CASCADE"), index=True
    )
    chain_type: Mapped[ChainType] = mapped_column(str_enum(ChainType))
    chain_index: Mapped[int] = mapped_column(Integer, default=1)
    sequence: Mapped[str] = mapped_column(Text)
    length: Mapped[int] = mapped_column(Integer)
    cdr3_sequence: Mapped[str | None] = mapped_column(String(60))
    cdr3_len: Mapped[int | None] = mapped_column(Integer)

    antibody: Mapped[Antibody] = relationship(back_populates="chains")
    liabilities: Mapped[list["Liability"]] = relationship(
        back_populates="chain",
        cascade="all, delete-orphan",
        order_by="Liability.position",
    )


class Liability(Base):
    __tablename__ = "liability"

    id: Mapped[int] = mapped_column(primary_key=True)
    chain_id: Mapped[int] = mapped_column(
        ForeignKey("chain.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[LiabilityKind] = mapped_column(str_enum(LiabilityKind))
    motif: Mapped[str] = mapped_column(String(10))        # e.g. "NGT", "DG"
    position: Mapped[int] = mapped_column(Integer)        # 1-based index into the chain sequence
    in_cdr3: Mapped[bool] = mapped_column(Boolean, default=False)
    severity: Mapped[Severity] = mapped_column(str_enum(Severity))

    chain: Mapped[Chain] = relationship(back_populates="liabilities")


class AntibodySnapshot(Base):
    """Antibody state as seen in one pipeline run. Diffing consecutive
    snapshots produces ANTIBODY_STAGE_CHANGED signals."""

    __tablename__ = "antibody_snapshot"
    __table_args__ = (
        UniqueConstraint("antibody_id", "run_id", name="uq_antibody_snapshot_run"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    antibody_id: Mapped[int] = mapped_column(
        ForeignKey("antibody.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_run.id"), index=True)
    clinical_stage: Mapped[str | None] = mapped_column(String(60))
    content_hash: Mapped[str] = mapped_column(String(64))  # sha256 of the normalized record
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    antibody: Mapped[Antibody] = relationship(back_populates="snapshots")