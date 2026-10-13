"""Clinical trials, their many-to-many link to antibodies, and per-run snapshots."""
from datetime import date, datetime

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base, TimestampMixin

# One trial can test several antibodies (combination studies), and one
# antibody has many trials, so this is a plain association table.
antibody_trial = Table(
    "antibody_trial",
    Base.metadata,
    Column("antibody_id", ForeignKey("antibody.id", ondelete="CASCADE"), primary_key=True),
    Column("trial_id", ForeignKey("trial.id", ondelete="CASCADE"), primary_key=True),
)


class Trial(TimestampMixin, Base):
    __tablename__ = "trial"

    id: Mapped[int] = mapped_column(primary_key=True)
    nct_id: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    sponsor: Mapped[str | None] = mapped_column(String(255))
    # Current values; they mirror the latest snapshot for fast reads.
    status: Mapped[str | None] = mapped_column(String(60))     # e.g. "RECRUITING"
    phase: Mapped[str | None] = mapped_column(String(60))      # e.g. "PHASE2" or "PHASE1|PHASE2"
    enrollment: Mapped[int | None] = mapped_column(Integer)
    last_update_date: Mapped[date | None] = mapped_column(Date)

    antibodies: Mapped[list["Antibody"]] = relationship(  # noqa: F821
        secondary=antibody_trial, back_populates="trials"
    )
    snapshots: Mapped[list["TrialSnapshot"]] = relationship(
        back_populates="trial", cascade="all, delete-orphan"
    )

    @property
    def source_url(self) -> str:
        return f"https://clinicaltrials.gov/study/{self.nct_id}"

    def __repr__(self) -> str:
        return f"<Trial {self.nct_id} {self.status}>"


class TrialSnapshot(Base):
    """Trial state as seen in one pipeline run. Never updated, only inserted."""

    __tablename__ = "trial_snapshot"
    __table_args__ = (
        UniqueConstraint("trial_id", "run_id", name="uq_trial_snapshot_run"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    trial_id: Mapped[int] = mapped_column(
        ForeignKey("trial.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[int] = mapped_column(ForeignKey("pipeline_run.id"), index=True)
    status: Mapped[str | None] = mapped_column(String(60))
    phase: Mapped[str | None] = mapped_column(String(60))
    enrollment: Mapped[int | None] = mapped_column(Integer)
    last_update_date: Mapped[date | None] = mapped_column(Date)
    content_hash: Mapped[str] = mapped_column(String(64))
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    trial: Mapped[Trial] = relationship(back_populates="snapshots")