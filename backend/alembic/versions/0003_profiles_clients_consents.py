"""Create psychologist profiles, clients, care relationships and consents.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.migrations import add_updated_at_trigger, id_column, timestamp_columns

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPTZ = sa.DateTime(timezone=True)
UUID = postgresql.UUID(as_uuid=True)
TABLES = ("consents", "psychologist_clients", "clients", "psychologist_profiles")


def upgrade() -> None:
    op.execute(
        "CREATE TYPE consent_type AS ENUM "
        "('kvkk_disclosure', 'data_processing', 'analytics', 'ai_processing')"
    )

    op.create_table(
        "psychologist_profiles",
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("last_name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("bio", sa.Text()),
        sa.Column("phone", sa.Text()),
        sa.Column(
            "timezone", sa.Text(), server_default=sa.text("'Europe/Istanbul'"), nullable=False
        ),
        sa.Column(
            "min_booking_notice_hours", sa.Integer(), server_default=sa.text("24"), nullable=False
        ),
        sa.Column(
            "max_booking_advance_days", sa.Integer(), server_default=sa.text("60"), nullable=False
        ),
        sa.Column(
            "cancellation_window_hours", sa.Integer(), server_default=sa.text("24"), nullable=False
        ),
        sa.Column(
            "auto_confirm_bookings", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("buffer_minutes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="slug_format"),
        sa.CheckConstraint("min_booking_notice_hours >= 0", name="notice_not_negative"),
        sa.CheckConstraint("max_booking_advance_days > 0", name="advance_positive"),
        sa.CheckConstraint("cancellation_window_hours >= 0", name="cancellation_not_negative"),
        sa.CheckConstraint("buffer_minutes BETWEEN 0 AND 120", name="buffer_range"),
    )

    op.create_table(
        "clients",
        id_column(),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), unique=True),
        sa.Column("first_name", sa.Text(), nullable=False),
        sa.Column("last_name", sa.Text(), nullable=False),
        sa.Column("email", postgresql.CITEXT()),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("birth_date", sa.Date()),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("deleted_at", TIMESTAMPTZ),
        *timestamp_columns(),
    )
    op.create_index("ix_clients_email", "clients", ["email"])
    op.create_index("ix_clients_last_name_first_name", "clients", ["last_name", "first_name"])

    op.create_table(
        "psychologist_clients",
        id_column(),
        sa.Column(
            "psychologist_id",
            UUID,
            sa.ForeignKey("psychologist_profiles.user_id"),
            nullable=False,
        ),
        sa.Column("client_id", UUID, sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("ended_at", TIMESTAMPTZ),
        *timestamp_columns(),
        sa.UniqueConstraint("psychologist_id", "client_id"),
    )
    op.create_index("ix_psychologist_clients_client_id", "psychologist_clients", ["client_id"])

    op.create_table(
        "consents",
        id_column(),
        sa.Column("client_id", UUID, sa.ForeignKey("clients.id"), nullable=False),
        sa.Column(
            "consent_type", postgresql.ENUM(name="consent_type", create_type=False), nullable=False
        ),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("granted_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", TIMESTAMPTZ),
        sa.Column("ip_address", postgresql.INET()),
        sa.Column("recorded_by_user_id", UUID, sa.ForeignKey("users.id")),
        *timestamp_columns(),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at", name="revoked_after_granted"
        ),
    )
    op.create_index(
        "uq_consents_active",
        "consents",
        ["client_id", "consent_type", "version"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    for table in TABLES:
        add_updated_at_trigger(table)


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)
    op.execute("DROP TYPE consent_type")
