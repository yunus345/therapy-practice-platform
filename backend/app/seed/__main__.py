"""Load synthetic demo data: `python -m app.seed [--today YYYY-MM-DD]`.

Runs only with `ENVIRONMENT=dev` and only on a database without users. To start over,
recreate the schema first, as the README describes.
"""

import argparse
import asyncio
import sys
from datetime import date

from app.core.clock import utcnow
from app.core.config import Environment, get_settings
from app.core.database import Database, write_transaction
from app.modules.users.models import UserRole
from app.seed.fixtures import ADMIN_EMAIL, PSYCHOLOGISTS
from app.seed.generator import TIMEZONE, DemoData
from app.seed.loader import DEMO_PASSWORD, DatabaseNotEmptyError, load_demo_data


async def seed(today: date) -> DemoData:
    database = Database(get_settings().database_url.get_secret_value())
    try:
        async with write_transaction(database) as session:
            return await load_demo_data(session, today)
    finally:
        await database.dispose()


def report(data: DemoData) -> str:
    accounts = [user.email for user in data.users if user.role is UserRole.CLIENT][:2]
    lines = [
        "Demo data loaded:",
        f"  {len(data.users)} users, {len(data.clients)} clients, {len(data.services)} services",
        f"  {len(data.appointments)} appointments:",
        *(f"    {status}: {count}" for status, count in sorted(data.status_counts().items())),
        "",
        f"Demo logins (password for all: {DEMO_PASSWORD}):",
        f"  admin         {ADMIN_EMAIL}",
        *(f"  psychologist  {spec.email}" for spec in PSYCHOLOGISTS),
        *(f"  client        {email}" for email in accounts),
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.seed")
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=utcnow().astimezone(TIMEZONE).date(),
        help="The day the data is built around, as YYYY-MM-DD. Defaults to today.",
    )
    arguments = parser.parse_args()

    if get_settings().environment is not Environment.DEV:
        sys.exit("The seed runs only with ENVIRONMENT=dev.")
    try:
        data = asyncio.run(seed(arguments.today))
    except DatabaseNotEmptyError:
        sys.exit("The database already has users. The seed only fills an empty database.")
    sys.stdout.write(report(data))


if __name__ == "__main__":
    main()
