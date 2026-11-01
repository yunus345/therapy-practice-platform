import uuid

from app.modules.consent.models import Consent, ConsentType
from app.modules.consent.repository import ConsentRepository


class ConsentService:
    def __init__(self, repository: ConsentRepository) -> None:
        self._repository = repository

    async def record_kvkk_disclosure(
        self,
        client_id: uuid.UUID,
        version: str,
        ip_address: str | None = None,
        recorded_by_user_id: uuid.UUID | None = None,
    ) -> None:
        await self._repository.add(
            Consent(
                client_id=client_id,
                consent_type=ConsentType.KVKK_DISCLOSURE,
                version=version,
                ip_address=ip_address,
                recorded_by_user_id=recorded_by_user_id,
            )
        )
