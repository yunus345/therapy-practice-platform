"""Operational commands. Run with `python -m app.cli`."""

import argparse
import asyncio
import getpass
import sys

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.database import Database, write_transaction
from app.core.errors import AppError
from app.modules.audit.service import build_audit_service
from app.modules.users.admin_service import create_first_admin
from app.modules.users.service import build_user_service


async def create_admin(email: str, password: str) -> None:
    database = Database(get_settings().database_url.get_secret_value())
    try:
        async with write_transaction(database) as session:
            await create_first_admin(
                build_user_service(session, utcnow),
                build_audit_service(session),
                utcnow,
                email,
                password,
            )
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("create-admin", help="Create an admin account")
    command.add_argument("--email", required=True)
    arguments = parser.parse_args()

    # Read interactively, because arguments and environment variables can leak.
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")

    try:
        asyncio.run(create_admin(arguments.email, password))
    except AppError as error:
        sys.exit(f"{error.code}: {error.message}")
    sys.stdout.write("Admin created.\n")


if __name__ == "__main__":
    main()
