"""Authentication: registration, login, sessions, one-time tokens and 2FA."""

import hmac
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import pyotp

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import (
    SecretBox,
    generate_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_password,
)
from app.modules.audit.service import AuditAction, AuditService
from app.modules.auth.models import AuthSession, AuthToken, AuthTokenPurpose
from app.modules.auth.passwords import ensure_acceptable_password
from app.modules.auth.repository import AuthRepository
from app.modules.clients.service import ClientService
from app.modules.consent.service import ConsentService
from app.modules.notifications.service import Email, EmailQueue, EmailTemplate
from app.modules.users.actor import STAFF_ROLES, Actor
from app.modules.users.models import User, UserRole
from app.modules.users.service import EmailTakenError, UserService

TOTP_PERIOD_SECONDS = 30
# Accepted clock drift, in time steps on either side of the current one.
TOTP_DRIFT_STEPS = 1
RECOVERY_CODE_COUNT = 8
SESSION_TOUCH_INTERVAL = timedelta(minutes=1)

SESSION_ENTITY = "auth_session"
USER_ENTITY = "user"


class NotAuthenticatedError(AppError):
    status_code = 401
    code = "NOT_AUTHENTICATED"
    message = "Authentication is required."


class InvalidCredentialsError(AppError):
    status_code = 401
    code = "INVALID_CREDENTIALS"
    message = "The email or password is incorrect."
    keep_transaction = True


class InvalidCodeError(AppError):
    status_code = 401
    code = "INVALID_CODE"
    message = "The code is incorrect."
    keep_transaction = True


class InvalidTokenError(AppError):
    status_code = 400
    code = "INVALID_TOKEN"
    message = "The link is invalid or has expired."


class MfaRequiredError(AppError):
    status_code = 403
    code = "MFA_REQUIRED"
    message = "Two-factor verification is required."


class TwoFactorSetupRequiredError(AppError):
    status_code = 403
    code = "TWO_FACTOR_SETUP_REQUIRED"
    message = "Two-factor authentication must be enabled first."


class TwoFactorStateError(AppError):
    status_code = 409
    code = "TWO_FACTOR_STATE_INVALID"
    message = "This action does not apply to the current two-factor state."


class SessionNotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Not Found"


@dataclass(frozen=True)
class RequestMeta:
    ip_address: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class SessionContext:
    auth_session: AuthSession
    user: User
    mfa_pending: bool
    setup_required: bool

    @property
    def actor(self) -> Actor:
        return Actor(user_id=self.user.id, role=self.user.role)


@dataclass(frozen=True)
class IssuedSession:
    token: str
    context: SessionContext


@dataclass(frozen=True)
class EnabledTwoFactor:
    session: IssuedSession
    recovery_codes: list[str]


@dataclass(frozen=True)
class TwoFactorSetup:
    provisioning_uri: str


class AuthService:
    def __init__(
        self,
        repository: AuthRepository,
        users: UserService,
        clients: ClientService,
        consents: ConsentService,
        audit: AuditService,
        emails: EmailQueue,
        settings: Settings,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._users = users
        self._clients = clients
        self._consents = consents
        self._audit = audit
        self._emails = emails
        self._settings = settings
        self._clock = clock
        self._secrets = SecretBox(settings.totp_encryption_key.get_secret_value())

    async def register_client(
        self,
        *,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        phone: str,
        meta: RequestMeta,
    ) -> None:
        """Create a client account. An existing email gets the same response and a notice."""
        ensure_acceptable_password(password, email)
        password_hash = await hash_password(password)

        user = User(email=email, role=UserRole.CLIENT, password_hash=password_hash)
        try:
            await self._users.create(user)
        except EmailTakenError:
            self._emails.enqueue(Email(to=email, template=EmailTemplate.ACCOUNT_EXISTS))
            return

        client = await self._clients.create_for_registered_user(
            user.id, first_name, last_name, email, phone
        )
        await self._consents.record_kvkk_disclosure(
            client.id, self._settings.kvkk_disclosure_version, ip_address=meta.ip_address
        )
        await self._audit.record(
            AuditAction.USER_REGISTERED,
            actor_user_id=user.id,
            entity_type=USER_ENTITY,
            entity_id=user.id,
            ip_address=meta.ip_address,
        )
        await self._send_verification_email(user)

    async def resend_verification_email(self, actor: Actor) -> None:
        user = await self._require_user(actor)
        if user.email_verified_at is None:
            await self._repository.invalidate_tokens(
                user.id, [AuthTokenPurpose.EMAIL_VERIFICATION], self._clock()
            )
            await self._send_verification_email(user)

    async def verify_email(self, token: str, meta: RequestMeta) -> None:
        record = await self._consume_token(token, AuthTokenPurpose.EMAIL_VERIFICATION)
        user = await self._require_user_by_id(record.user_id)
        user.email_verified_at = self._clock()
        await self._users.save()
        await self._audit.record(
            AuditAction.EMAIL_VERIFIED,
            actor_user_id=user.id,
            entity_type=USER_ENTITY,
            entity_id=user.id,
            ip_address=meta.ip_address,
        )

    async def login(self, email: str, password: str, meta: RequestMeta) -> IssuedSession:
        now = self._clock()
        user = await self._users.get_by_email(email)
        password_matches = await verify_password(user.password_hash if user else None, password)

        if user is None or not password_matches or not self._can_log_in(user, now):
            if user is not None and not self._is_locked(user, now):
                await self._record_failed_attempt(user)
            await self._audit.record(
                AuditAction.LOGIN_FAILED,
                actor_user_id=user.id if user else None,
                ip_address=meta.ip_address,
            )
            raise InvalidCredentialsError

        if user.password_hash and password_needs_rehash(user.password_hash):
            user.password_hash = await hash_password(password)
        await self._users.record_successful_login(user, now)
        await self._audit.record(
            AuditAction.LOGIN_SUCCEEDED,
            actor_user_id=user.id,
            entity_type=SESSION_ENTITY,
            ip_address=meta.ip_address,
        )
        return await self._create_session(user, meta)

    async def logout(self, context: SessionContext, meta: RequestMeta) -> None:
        context.auth_session.revoked_at = self._clock()
        await self._repository.flush()
        await self._audit.record(
            AuditAction.LOGOUT,
            actor_user_id=context.user.id,
            entity_type=SESSION_ENTITY,
            entity_id=context.auth_session.id,
            ip_address=meta.ip_address,
        )

    async def resolve_session(self, token: str) -> SessionContext:
        """Return the live session for a token, enforcing expiry and account state."""
        now = self._clock()
        auth_session = await self._repository.get_session_by_token_hash(hash_token(token))
        if auth_session is None or auth_session.revoked_at is not None:
            raise NotAuthenticatedError

        idle_limit = timedelta(minutes=self._settings.session_idle_timeout_minutes)
        if now >= auth_session.expires_at or now - auth_session.last_seen_at >= idle_limit:
            raise NotAuthenticatedError

        user = await self._require_user_by_id(auth_session.user_id)
        if not user.is_active:
            raise NotAuthenticatedError

        if now - auth_session.last_seen_at >= SESSION_TOUCH_INTERVAL:
            auth_session.last_seen_at = now
            await self._repository.flush()
        return self._build_context(auth_session, user)

    async def list_sessions(self, actor: Actor) -> Sequence[AuthSession]:
        return await self._repository.list_active_sessions(actor.user_id, self._clock())

    async def revoke_session(self, actor: Actor, session_id: uuid.UUID, meta: RequestMeta) -> None:
        auth_session = await self._repository.get_session(session_id)
        if auth_session is None or auth_session.user_id != actor.user_id:
            raise SessionNotFoundError
        if auth_session.revoked_at is None:
            auth_session.revoked_at = self._clock()
            await self._repository.flush()
            await self._audit.record(
                AuditAction.SESSION_REVOKED,
                actor_user_id=actor.user_id,
                entity_type=SESSION_ENTITY,
                entity_id=auth_session.id,
                ip_address=meta.ip_address,
            )

    async def revoke_all_sessions(self, user_id: uuid.UUID) -> None:
        await self._repository.revoke_sessions(user_id, self._clock())

    async def request_password_reset(self, email: str, meta: RequestMeta) -> None:
        """Send a reset link if the account exists. The response never reveals which."""
        user = await self._users.get_by_email(email)
        if user is None or not user.is_active:
            return

        purposes = [AuthTokenPurpose.PASSWORD_RESET]
        await self._repository.invalidate_tokens(user.id, purposes, self._clock())
        token = await self._issue_token(
            user.id,
            AuthTokenPurpose.PASSWORD_RESET,
            timedelta(minutes=self._settings.password_reset_token_ttl_minutes),
        )
        await self._audit.record(
            AuditAction.PASSWORD_RESET_REQUESTED,
            actor_user_id=user.id,
            entity_type=USER_ENTITY,
            entity_id=user.id,
            ip_address=meta.ip_address,
        )
        self._emails.enqueue(
            Email(
                to=user.email,
                template=EmailTemplate.PASSWORD_RESET,
                link=self._frontend_link("reset-password", token),
            )
        )

    async def confirm_password_reset(
        self, token: str, new_password: str, meta: RequestMeta
    ) -> None:
        """Set a password from a reset or invitation link and end all sessions."""
        record = await self._consume_token(
            token, AuthTokenPurpose.PASSWORD_RESET, AuthTokenPurpose.INVITATION
        )
        user = await self._require_user_by_id(record.user_id)
        ensure_acceptable_password(new_password, user.email)

        user.password_hash = await hash_password(new_password)
        user.failed_login_attempts = 0
        user.locked_until = None
        if record.purpose is AuthTokenPurpose.INVITATION:
            # The invitation arrived at this address, which proves ownership.
            user.email_verified_at = self._clock()
        await self._users.save()
        await self.revoke_all_sessions(user.id)
        await self._audit.record(
            AuditAction.PASSWORD_RESET_COMPLETED,
            actor_user_id=user.id,
            entity_type=USER_ENTITY,
            entity_id=user.id,
            ip_address=meta.ip_address,
        )

    async def invite(self, user: User) -> None:
        token = await self._issue_token(
            user.id,
            AuthTokenPurpose.INVITATION,
            timedelta(hours=self._settings.invitation_token_ttl_hours),
        )
        self._emails.enqueue(
            Email(
                to=user.email,
                template=EmailTemplate.INVITATION,
                link=self._frontend_link("set-password", token),
            )
        )

    async def start_two_factor_setup(self, actor: Actor) -> TwoFactorSetup:
        actor.require_role(*STAFF_ROLES)
        user = await self._require_user(actor)
        if user.totp_enabled:
            raise TwoFactorStateError

        secret = pyotp.random_base32()
        user.totp_secret_encrypted = self._secrets.encrypt(secret)
        await self._users.save()
        uri = pyotp.TOTP(secret).provisioning_uri(
            name=user.email, issuer_name=self._settings.totp_issuer
        )
        return TwoFactorSetup(provisioning_uri=uri)

    async def enable_two_factor(
        self, context: SessionContext, code: str, meta: RequestMeta
    ) -> EnabledTwoFactor:
        context.actor.require_role(*STAFF_ROLES)
        user = context.user
        if user.totp_enabled or user.totp_secret_encrypted is None:
            raise TwoFactorStateError

        if not await self._accept_totp(user, code):
            await self._record_failed_two_factor(user, meta)
            raise InvalidCodeError

        user.totp_enabled = True
        recovery_codes = await self._issue_recovery_codes(user)
        await self._users.save()
        await self._audit.record(
            AuditAction.TWO_FACTOR_ENABLED,
            actor_user_id=user.id,
            entity_type=USER_ENTITY,
            entity_id=user.id,
            ip_address=meta.ip_address,
        )
        return EnabledTwoFactor(
            session=await self._elevate_session(context), recovery_codes=recovery_codes
        )

    async def verify_two_factor(
        self,
        context: SessionContext,
        *,
        code: str | None,
        recovery_code: str | None,
        meta: RequestMeta,
    ) -> IssuedSession:
        user = context.user
        if not user.totp_enabled or not context.mfa_pending:
            raise TwoFactorStateError
        if self._is_locked(user, self._clock()):
            raise InvalidCodeError

        accepted = (
            await self._accept_recovery_code(user, recovery_code, meta)
            if recovery_code
            else await self._accept_totp(user, code or "")
        )
        if not accepted:
            await self._record_failed_two_factor(user, meta)
            raise InvalidCodeError

        await self._audit.record(
            AuditAction.TWO_FACTOR_VERIFIED,
            actor_user_id=user.id,
            entity_type=SESSION_ENTITY,
            entity_id=context.auth_session.id,
            ip_address=meta.ip_address,
        )
        return await self._elevate_session(context)

    async def disable_two_factor(
        self, context: SessionContext, password: str, code: str, meta: RequestMeta
    ) -> None:
        user = context.user
        if not user.totp_enabled:
            raise TwoFactorStateError

        password_matches = await verify_password(user.password_hash, password)
        if not password_matches or not await self._accept_totp(user, code):
            await self._record_failed_two_factor(user, meta)
            raise InvalidCodeError

        await self.clear_two_factor(user)
        await self._repository.revoke_sessions(
            user.id, self._clock(), except_session_id=context.auth_session.id
        )
        await self._audit.record(
            AuditAction.TWO_FACTOR_DISABLED,
            actor_user_id=user.id,
            entity_type=USER_ENTITY,
            entity_id=user.id,
            ip_address=meta.ip_address,
        )

    async def clear_two_factor(self, user: User) -> None:
        user.totp_enabled = False
        user.totp_secret_encrypted = None
        user.totp_last_used_step = None
        await self._users.save()
        await self._repository.delete_recovery_codes(user.id)

    def _build_context(self, auth_session: AuthSession, user: User) -> SessionContext:
        return SessionContext(
            auth_session=auth_session,
            user=user,
            mfa_pending=user.totp_enabled and not auth_session.mfa_verified,
            setup_required=(
                self._settings.require_staff_2fa
                and user.role in STAFF_ROLES
                and not user.totp_enabled
            ),
        )

    async def _create_session(self, user: User, meta: RequestMeta) -> IssuedSession:
        now = self._clock()
        token = generate_token()
        auth_session = AuthSession(
            user_id=user.id,
            token_hash=hash_token(token),
            last_seen_at=now,
            expires_at=now + timedelta(hours=self._settings.session_absolute_lifetime_hours),
            ip_address=meta.ip_address,
            user_agent=meta.user_agent[:512] if meta.user_agent else None,
        )
        await self._repository.add(auth_session)
        return IssuedSession(token=token, context=self._build_context(auth_session, user))

    async def _elevate_session(self, context: SessionContext) -> IssuedSession:
        """Mark the session as MFA-verified and rotate its token: privileges changed."""
        token = generate_token()
        context.auth_session.token_hash = hash_token(token)
        context.auth_session.mfa_verified = True
        await self._repository.flush()
        return IssuedSession(
            token=token, context=self._build_context(context.auth_session, context.user)
        )

    def _can_log_in(self, user: User, now: datetime) -> bool:
        return user.is_active and not self._is_locked(user, now)

    @staticmethod
    def _is_locked(user: User, now: datetime) -> bool:
        return user.locked_until is not None and user.locked_until > now

    async def _record_failed_attempt(self, user: User) -> None:
        await self._users.record_failed_attempt(
            user,
            self._settings.login_max_failed_attempts,
            timedelta(minutes=self._settings.login_lockout_minutes),
        )

    async def _record_failed_two_factor(self, user: User, meta: RequestMeta) -> None:
        await self._record_failed_attempt(user)
        await self._audit.record(
            AuditAction.TWO_FACTOR_FAILED,
            actor_user_id=user.id,
            entity_type=USER_ENTITY,
            entity_id=user.id,
            ip_address=meta.ip_address,
        )

    async def _accept_totp(self, user: User, code: str) -> bool:
        step = self._matching_totp_step(user, code)
        return step is not None and await self._users.claim_totp_step(user, step)

    def _matching_totp_step(self, user: User, code: str) -> int | None:
        if user.totp_secret_encrypted is None:
            return None
        totp = pyotp.TOTP(self._secrets.decrypt(user.totp_secret_encrypted))
        current_step = int(self._clock().timestamp()) // TOTP_PERIOD_SECONDS
        for step in range(current_step - TOTP_DRIFT_STEPS, current_step + TOTP_DRIFT_STEPS + 1):
            if hmac.compare_digest(totp.generate_otp(step), code):
                return step
        return None

    async def _issue_recovery_codes(self, user: User) -> list[str]:
        codes = [
            f"{secrets.token_hex(5)}-{secrets.token_hex(5)}" for _ in range(RECOVERY_CODE_COUNT)
        ]
        await self._repository.replace_recovery_codes(
            user.id, [self._secrets.keyed_hash(code) for code in codes]
        )
        return codes

    async def _accept_recovery_code(
        self, user: User, recovery_code: str, meta: RequestMeta
    ) -> bool:
        code_hash = self._secrets.keyed_hash(recovery_code.strip().lower())
        accepted = await self._repository.consume_recovery_code(user.id, code_hash, self._clock())
        if accepted:
            await self._audit.record(
                AuditAction.RECOVERY_CODE_USED,
                actor_user_id=user.id,
                entity_type=USER_ENTITY,
                entity_id=user.id,
                ip_address=meta.ip_address,
            )
        return accepted

    async def _send_verification_email(self, user: User) -> None:
        token = await self._issue_token(
            user.id,
            AuthTokenPurpose.EMAIL_VERIFICATION,
            timedelta(hours=self._settings.email_verification_token_ttl_hours),
        )
        self._emails.enqueue(
            Email(
                to=user.email,
                template=EmailTemplate.VERIFY_EMAIL,
                link=self._frontend_link("verify-email", token),
            )
        )

    async def _issue_token(
        self, user_id: uuid.UUID, purpose: AuthTokenPurpose, lifetime: timedelta
    ) -> str:
        token = generate_token()
        await self._repository.add(
            AuthToken(
                user_id=user_id,
                purpose=purpose,
                token_hash=hash_token(token),
                expires_at=self._clock() + lifetime,
            )
        )
        return token

    async def _consume_token(self, token: str, *purposes: AuthTokenPurpose) -> AuthToken:
        record = await self._repository.consume_token(hash_token(token), purposes, self._clock())
        if record is None:
            raise InvalidTokenError
        return record

    def _frontend_link(self, page: str, token: str) -> str:
        # The token sits in the fragment, which browsers never send to servers or in Referer.
        return f"{self._settings.frontend_origin}/{page}#token={token}"

    async def _require_user(self, actor: Actor) -> User:
        return await self._require_user_by_id(actor.user_id)

    async def _require_user_by_id(self, user_id: uuid.UUID) -> User:
        user = await self._users.get(user_id)
        if user is None:
            raise NotAuthenticatedError
        return user
