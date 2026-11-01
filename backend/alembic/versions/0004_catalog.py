"""Create the therapy services catalog.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.migrations import add_updated_at_trigger, id_column, timestamp_columns

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
MONEY = sa.Numeric(10, 2)
TABLES = ("psychologist_services", "services")


def upgrade() -> None:
    op.execute("CREATE TYPE delivery_mode AS ENUM ('in_person', 'online')")

    op.create_table(
        "services",
        id_column(),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("price_amount", MONEY, nullable=False),
        sa.Column("currency", sa.CHAR(3), server_default=sa.text("'TRY'"), nullable=False),
        sa.Column(
            "delivery_mode",
            postgresql.ENUM(name="delivery_mode", create_type=False),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *timestamp_columns(),
        sa.CheckConstraint("duration_minutes > 0", name="duration_positive"),
        sa.CheckConstraint("price_amount >= 0", name="price_not_negative"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
    )

    op.create_table(
        "psychologist_services",
        id_column(),
        sa.Column(
            "psychologist_id",
            UUID,
            sa.ForeignKey("psychologist_profiles.user_id"),
            nullable=False,
        ),
        sa.Column("service_id", UUID, sa.ForeignKey("services.id"), nullable=False),
        sa.Column("custom_price_amount", MONEY),
        *timestamp_columns(),
        sa.UniqueConstraint("psychologist_id", "service_id"),
        sa.CheckConstraint(
            "custom_price_amount IS NULL OR custom_price_amount >= 0",
            name="custom_price_not_negative",
        ),
    )
    op.create_index("ix_psychologist_services_service_id", "psychologist_services", ["service_id"])

    for table in TABLES:
        add_updated_at_trigger(table)


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)
    op.execute("DROP TYPE delivery_mode")
