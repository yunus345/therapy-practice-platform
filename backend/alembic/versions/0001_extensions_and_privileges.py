"""Enable extensions, add the updated_at trigger function and grant table access.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

from alembic import context, op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("citext", "btree_gist", "pgcrypto", "vector")


def upgrade() -> None:
    for extension in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{extension}"')

    op.execute(
        """
        CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    app_role = context.config.attributes["app_role"]
    op.execute(f'GRANT USAGE ON SCHEMA public TO "{app_role}"')
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{app_role}"'
    )


def downgrade() -> None:
    app_role = context.config.attributes["app_role"]
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f'REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM "{app_role}"'
    )
    op.execute(f'REVOKE USAGE ON SCHEMA public FROM "{app_role}"')
    op.execute("DROP FUNCTION set_updated_at()")
    for extension in reversed(EXTENSIONS):
        op.execute(f'DROP EXTENSION "{extension}"')
