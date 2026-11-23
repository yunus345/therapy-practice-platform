"""Create availability, time off, appointments and their status history.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op
from app.core.migrations import add_updated_at_trigger, id_column, timestamp_columns

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPTZ = sa.DateTime(timezone=True)
UUID = postgresql.UUID(as_uuid=True)
TABLES = ("appointments", "time_off", "availability_rules")

APPOINTMENT_STATUS = postgresql.ENUM(name="appointment_status", create_type=False)
CANCELLATION_REASON = postgresql.ENUM(name="cancellation_reason", create_type=False)


def psychologist_fk() -> sa.Column[sa.Uuid]:
    return sa.Column(
        "psychologist_id",
        UUID,
        sa.ForeignKey("psychologist_profiles.user_id"),
        nullable=False,
    )


def upgrade() -> None:
    op.execute(
        "CREATE TYPE appointment_status AS ENUM "
        "('requested', 'confirmed', 'completed', 'cancelled', 'no_show')"
    )
    op.execute("CREATE TYPE booking_channel AS ENUM ('online', 'staff')")
    op.execute(
        "CREATE TYPE cancellation_reason AS ENUM ('client_request', 'psychologist_request', "
        "'late_cancellation', 'rescheduled', 'other')"
    )

    op.create_table(
        "availability_rules",
        id_column(),
        psychologist_fk(),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date()),
        *timestamp_columns(),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        sa.CheckConstraint("start_time < end_time", name="start_before_end"),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until >= valid_from", name="valid_range"
        ),
    )
    op.create_index(
        "ix_availability_rules_psychologist_id_weekday",
        "availability_rules",
        ["psychologist_id", "weekday"],
    )

    op.create_table(
        "time_off",
        id_column(),
        psychologist_fk(),
        sa.Column("starts_at", TIMESTAMPTZ, nullable=False),
        sa.Column("ends_at", TIMESTAMPTZ, nullable=False),
        sa.Column("reason", sa.String(200)),
        *timestamp_columns(),
        sa.CheckConstraint("starts_at < ends_at", name="start_before_end"),
    )
    op.create_index(
        "ix_time_off_psychologist_id_starts_at", "time_off", ["psychologist_id", "starts_at"]
    )

    op.create_table(
        "appointments",
        id_column(),
        psychologist_fk(),
        sa.Column("client_id", UUID, sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("service_id", UUID, sa.ForeignKey("services.id"), nullable=False),
        sa.Column("starts_at", TIMESTAMPTZ, nullable=False),
        sa.Column("ends_at", TIMESTAMPTZ, nullable=False),
        sa.Column("status", APPOINTMENT_STATUS, nullable=False),
        sa.Column("price_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column(
            "booking_channel",
            postgresql.ENUM(name="booking_channel", create_type=False),
            nullable=False,
        ),
        sa.Column("booked_by_user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("rescheduled_from_id", UUID, sa.ForeignKey("appointments.id"), unique=True),
        sa.Column("cancelled_at", TIMESTAMPTZ),
        sa.Column("cancelled_by_user_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("cancellation_reason", CANCELLATION_REASON),
        *timestamp_columns(),
        sa.CheckConstraint("starts_at < ends_at", name="start_before_end"),
        sa.CheckConstraint("price_amount >= 0", name="price_not_negative"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)", name="cancelled_at_matches_status"
        ),
        sa.CheckConstraint(
            "(status = 'cancelled') = (cancellation_reason IS NOT NULL)",
            name="cancellation_reason_matches_status",
        ),
    )
    op.create_index(
        "ix_appointments_psychologist_id_starts_at",
        "appointments",
        ["psychologist_id", "starts_at"],
    )
    op.create_index("ix_appointments_client_id_starts_at", "appointments", ["client_id", "starts_at"])
    op.execute(
        """
        ALTER TABLE appointments ADD CONSTRAINT ex_appointments_no_overlap
        EXCLUDE USING gist (
            psychologist_id WITH =,
            tstzrange(starts_at, ends_at, '[)') WITH &&
        ) WHERE (status IN ('requested', 'confirmed'))
        """
    )

    op.create_table(
        "appointment_status_history",
        id_column(),
        sa.Column("appointment_id", UUID, sa.ForeignKey("appointments.id"), nullable=False),
        sa.Column("from_status", APPOINTMENT_STATUS),
        sa.Column("to_status", APPOINTMENT_STATUS, nullable=False),
        sa.Column("changed_by_user_id", UUID, sa.ForeignKey("users.id")),
        sa.Column("changed_at", TIMESTAMPTZ, server_default=sa.func.now(), nullable=False),
        sa.Column("reason_category", CANCELLATION_REASON),
    )
    op.create_index(
        "ix_appointment_status_history_appointment_id_changed_at",
        "appointment_status_history",
        ["appointment_id", "changed_at"],
    )
    app_role = context.config.attributes["app_role"]
    op.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON appointment_status_history FROM "{app_role}"')

    for table in TABLES:
        add_updated_at_trigger(table)


def downgrade() -> None:
    op.drop_table("appointment_status_history")
    for table in TABLES:
        op.drop_table(table)
    op.execute("DROP TYPE cancellation_reason")
    op.execute("DROP TYPE booking_channel")
    op.execute("DROP TYPE appointment_status")
