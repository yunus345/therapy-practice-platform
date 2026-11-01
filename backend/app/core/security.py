"""Cryptographic primitives. This module holds no business rules."""

import asyncio
import base64
import hashlib
import hmac
import secrets
from functools import cached_property

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_password_hasher = PasswordHasher()  # argon2id with the library's recommended parameters
_dummy_password_hash = _password_hasher.hash(secrets.token_urlsafe(16))

TOKEN_BYTES = 32


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_password_hasher.hash, password)


async def verify_password(password_hash: str | None, password: str) -> bool:
    """Check a password. A missing hash costs the same time, so timing reveals no account."""
    try:
        return await asyncio.to_thread(
            _password_hasher.verify, password_hash or _dummy_password_hash, password
        ) and (password_hash is not None)
    except VerificationError, InvalidHashError:
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _password_hasher.check_needs_rehash(password_hash)


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Hash a high-entropy token. A fast hash is enough, as tokens cannot be guessed."""
    return hashlib.sha256(token.encode()).hexdigest()


def _derive_key(secret: str, purpose: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose).derive(
        secret.encode()
    )


class SecretBox:
    """Encrypts short secrets at rest, using keys derived from one environment secret."""

    def __init__(self, secret: str) -> None:
        self._secret = secret

    @cached_property
    def _fernet(self) -> Fernet:
        return Fernet(base64.urlsafe_b64encode(_derive_key(self._secret, b"seans-secret-box")))

    @cached_property
    def _hmac_key(self) -> bytes:
        return _derive_key(self._secret, b"seans-keyed-hash")

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode()).decode()

    def keyed_hash(self, value: str) -> str:
        """Hash low-entropy values, which a plain hash would not protect after a leak."""
        return hmac.new(self._hmac_key, value.encode(), hashlib.sha256).hexdigest()


def create_csrf_token(secret: str, binding: str) -> str:
    nonce = secrets.token_urlsafe(16)
    return f"{nonce}.{_csrf_signature(secret, binding, nonce)}"


def verify_csrf_token(secret: str, binding: str, token: str) -> bool:
    nonce, _, signature = token.partition(".")
    if not nonce or not signature:
        return False
    return hmac.compare_digest(signature, _csrf_signature(secret, binding, nonce))


def _csrf_signature(secret: str, binding: str, nonce: str) -> str:
    message = f"{binding}:{nonce}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
