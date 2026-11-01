"""Declarative base and shared column mixins.

CHECK and EXCLUDE constraints and triggers live in the Alembic migrations, which are
the source of truth for them. `tests/data/test_constraints.py` verifies them.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, Enum, FetchedValue, MetaData, Numeric, Text, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012
        datetime: DateTime(timezone=True),
        Decimal: Numeric(10, 2),
        str: Text(),
    }


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TimestampMixin(CreatedAtMixin):
    # A database trigger sets updated_at. eager_defaults reads it back after each flush.
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), server_onupdate=FetchedValue()
    )


def pg_enum(enum_class: type[StrEnum], name: str) -> Enum:
    """Map a StrEnum to a native Postgres enum, stored by value."""
    return Enum(
        enum_class,
        name=name,
        values_callable=lambda members: [member.value for member in members],
    )
