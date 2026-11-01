from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.clients.models import Client


class ClientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, client: Client) -> None:
        self._session.add(client)
        await self._session.flush()
