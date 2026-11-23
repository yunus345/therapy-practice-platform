from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.consent.models import Consent


class ConsentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, consent: Consent) -> None:
        self._session.add(consent)
        await self._session.flush()
