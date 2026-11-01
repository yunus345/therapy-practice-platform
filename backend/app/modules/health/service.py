import structlog
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import Database
from app.core.errors import ServiceUnavailableError

logger = structlog.get_logger(__name__)


async def check_health(database: Database) -> None:
    try:
        await database.ping()
    except (SQLAlchemyError, OSError) as error:
        logger.error("database_unavailable", exc_info=error)
        raise ServiceUnavailableError from error
