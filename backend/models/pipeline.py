"""Pipeline runs and the signals they emit."""
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, str_enum
from models.enums import EntityType, RunStatus, SignalKind


class PipelineRun(Base):
    __tablename__ = "pipeline_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[RunStatus] = mapped_column(str_enum(RunStatus), default=RunStatus.RUNNING)
    # e.g. {"antibodies": 42, "chains": 88, "trials": 310, "signals": 3}
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # Where this run's raw snapshot lives: data/raw/<id>/ or s3://bucket/raw/<id>/
    raw_location: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    signals: Mapped[list["Signal"]] = relationship(back_populates="run")

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()


class Signal(Base):
    """One detected change. Every signal must carry a source."""

    __tablename__ = "signal"
    __table_args__ = (
        CheckConstraint("source_url <> ''", name="source_required"),
        Index("ix_signal_created_at_desc", "created_at", postgresql_using="btree"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_run.id"), index=True)
    kind: Mapped[SignalKind] = mapped_column(str_enum(SignalKind), index=True)
    entity_type: Mapped[EntityType] = mapped_column(str_enum(EntityType))
    entity_id: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))   # "NCT04... moved RECRUITING -> COMPLETED"
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    seen: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    run: Mapped[PipelineRun] = relationship(back_populates="signals")