"""Create users and authentication tables.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.migrations import add_updated_at_trigger, id_column, timestamp_columns

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPTZ = sa.DateTime(timezone=True)
TABLES = ("auth_recovery_codes", "auth_tokens", "auth_sessions", "users")


def user_fk() -> sa.Column[sa.Uuid]:
    return sa.Column(
        "user_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.execute("CREATE TYPE user_role AS ENUM ('admin', 'psychologist', 'client')")
    op.execute(
        "CREATE TYPE auth_token_purpose AS ENUM "
        "('email_verification', 'password_reset', 'invitation')"
    )

    op.create_table(
        "users",
        id_column(),
        sa.Column("email", postgresql.CITEXT(), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text()),
        sa.Column(
            "role", postgresql.ENUM(name="user_role", create_type=False), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("email_verified_at", TIMESTAMPTZ),
        sa.Column("totp_secret_encrypted", sa.Text()),
        sa.Column("totp_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("totp_last_used_step", sa.BigInteger()),
        sa.Column(
            "failed_login_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("locked_until", TIMESTAMPTZ),
        sa.Column("last_login_at", TIMESTAMPTZ),
        *timestamp_columns(),
        sa.CheckConstraint("failed_login_attempts >= 0", name="failed_login_attempts_not_negative"),
        sa.CheckConstraint(
            "NOT totp_enabled OR totp_secret_encrypted IS NOT NULL",
            name="totp_enabled_requires_secret",
        ),
    )

    op.create_table(
        "auth_sessions",
        id_column(),
        user_fk(),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("last_seen_at", TIMESTAMPTZ, nullable=False),
        sa.Column("expires_at", TIMESTAMPTZ, nullable=False),
        sa.Column("revoked_at", TIMESTAMPTZ),
        sa.Column("ip_address", postgresql.INET()),
        sa.Column("user_agent", sa.String(512)),
        sa.Column("mfa_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *timestamp_columns(),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.create_table(
        "auth_tokens",
        id_column(),
        user_fk(),
        sa.Column(
            "purpose",
            postgresql.ENUM(name="auth_token_purpose", create_type=False),
            nullable=False,
        ),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("expires_at", TIMESTAMPTZ, nullable=False),
        sa.Column("used_at", TIMESTAMPTZ),
        *timestamp_columns(),
    )
    op.create_index("ix_auth_tokens_user_id", "auth_tokens", ["user_id"])

    op.create_table(
        "auth_recovery_codes",
        id_column(),
        user_fk(),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("used_at", TIMESTAMPTZ),
        *timestamp_columns(),
        sa.UniqueConstraint("user_id", "code_hash"),
    )

    for table in TABLES:
        add_updated_at_trigger(table)


def downgrade() -> None:
    for table in TABLES:
        op.drop_table(table)
    op.execute("DROP TYPE auth_token_purpose")
    op.execute("DROP TYPE user_role")
