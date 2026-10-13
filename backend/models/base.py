"""Declarative base, shared mixins and enum helper."""
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Predictable constraint names make Alembic migrations stable and readable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def str_enum(enum_cls: type[enum.Enum]) -> Enum:
    """Store enums as VARCHAR values, not native Postgres enums.

    Native enums need a migration every time a value is added; VARCHAR does not.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        length=40,
        values_callable=lambda e: [m.value for m in e],
    )