import uuid

from app.modules.clients.models import Client
from app.modules.clients.repository import ClientRepository


class ClientService:
    def __init__(self, repository: ClientRepository) -> None:
        self._repository = repository

    async def create_for_registered_user(
        self, user_id: uuid.UUID, first_name: str, last_name: str, email: str, phone: str
    ) -> Client:
        client = Client(
            user_id=user_id,
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            created_by_user_id=user_id,
        )
        await self._repository.add(client)
        return client
