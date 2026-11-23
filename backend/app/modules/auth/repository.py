import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import AuthRecoveryCode, AuthSession, AuthToken, AuthTokenPurpose


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, row: AuthSession | AuthToken) -> None:
        self._session.add(row)
        await self._session.flush()

    async def flush(self) -> None:
        await self._session.flush()

    async def get_session_by_token_hash(self, token_hash: str) -> AuthSession | None:
        return await self._session.scalar(
            select(AuthSession).where(AuthSession.token_hash == token_hash)
        )

    async def get_session(self, session_id: uuid.UUID) -> AuthSession | None:
        return await self._session.get(AuthSession, session_id)

    async def list_active_sessions(
        self, user_id: uuid.UUID, now: datetime
    ) -> Sequence[AuthSession]:
        result = await self._session.scalars(
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
            )
            .order_by(AuthSession.last_seen_at.desc())
        )
        return result.all()

    async def revoke_sessions(
        self, user_id: uuid.UUID, now: datetime, except_session_id: uuid.UUID | None = None
    ) -> None:
        statement = update(AuthSession).where(
            AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
        )
        if except_session_id is not None:
            statement = statement.where(AuthSession.id != except_session_id)
        await self._session.execute(statement.values(revoked_at=now))

    async def consume_token(
        self, token_hash: str, purposes: Sequence[AuthTokenPurpose], now: datetime
    ) -> AuthToken | None:
        """Mark a valid token used in one statement, so that it works only once."""
        return await self._session.scalar(
            update(AuthToken)
            .where(
                AuthToken.token_hash == token_hash,
                AuthToken.purpose.in_(purposes),
                AuthToken.used_at.is_(None),
                AuthToken.expires_at > now,
            )
            .values(used_at=now)
            .returning(AuthToken)
        )

    async def invalidate_tokens(
        self, user_id: uuid.UUID, purposes: Sequence[AuthTokenPurpose], now: datetime
    ) -> None:
        await self._session.execute(
            update(AuthToken)
            .where(
                AuthToken.user_id == user_id,
                AuthToken.purpose.in_(purposes),
                AuthToken.used_at.is_(None),
            )
            .values(used_at=now)
        )

    async def replace_recovery_codes(self, user_id: uuid.UUID, code_hashes: Sequence[str]) -> None:
        await self.delete_recovery_codes(user_id)
        self._session.add_all(
            AuthRecoveryCode(user_id=user_id, code_hash=code_hash) for code_hash in code_hashes
        )
        await self._session.flush()

    async def delete_recovery_codes(self, user_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(AuthRecoveryCode).where(AuthRecoveryCode.user_id == user_id)
        )

    async def consume_recovery_code(
        self, user_id: uuid.UUID, code_hash: str, now: datetime
    ) -> bool:
        result = await self._session.execute(
            update(AuthRecoveryCode)
            .where(
                AuthRecoveryCode.user_id == user_id,
                AuthRecoveryCode.code_hash == code_hash,
                AuthRecoveryCode.used_at.is_(None),
            )
            .values(used_at=now)
            .returning(AuthRecoveryCode.id)
        )
        return result.scalar_one_or_none() is not None
