"""
Authentication core: password hashing (argon2id), signed session tokens (JWT), user lookup/creation,
and a small in-memory login throttle. No web framework imports here, so it is unit-testable.
"""
from __future__ import annotations

import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select

from ingestion_pipeline.config import get_settings
from ingestion_pipeline.db.models import AppUser
from ingestion_pipeline.db.session import session_scope

logger = logging.getLogger(__name__)

_hasher = PasswordHasher()
# Verified against when the email is unknown, so "no such user" costs the same as "wrong password".
_DUMMY_HASH = _hasher.hash("not-a-real-password")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 10
_ALGORITHM = "HS256"

_ephemeral_secret: str | None = None


class AuthError(Exception):
    pass


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# ------------------------------------------------------------------------------ tokens


def _secret() -> str:
    global _ephemeral_secret
    configured = get_settings().auth_secret
    if configured:
        return configured
    if _ephemeral_secret is None:
        _ephemeral_secret = secrets.token_urlsafe(48)
        logger.warning("AUTH_SECRET is not set: using a temporary secret, so everyone is signed out on restart.")
    return _ephemeral_secret


def create_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": user_id, "iat": now, "exp": now + timedelta(hours=get_settings().auth_session_hours)}
    return jwt.encode(claims, _secret(), algorithm=_ALGORITHM)


def read_token(token: str) -> str | None:
    """The user_id inside a valid, unexpired token, else None."""
    try:
        return jwt.decode(token, _secret(), algorithms=[_ALGORITHM], options={"require": ["exp", "sub"]})["sub"]
    except jwt.PyJWTError:
        return None


# ------------------------------------------------------------------------------- users


def user_dict(user: AppUser) -> dict:
    return {"user_id": user.user_id, "email": user.email}


def create_user(email: str, password: str) -> dict:
    email = normalize_email(email)
    if not _EMAIL_RE.match(email):
        raise AuthError("That does not look like an email address.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    with session_scope() as session:
        if session.scalar(select(AppUser).where(func.lower(AppUser.email) == email)) is not None:
            raise AuthError("A user with that email already exists.")
        user = AppUser(email=email, password_hash=hash_password(password))
        session.add(user)
        session.flush()
        return user_dict(user)


def set_password(email: str, password: str) -> bool:
    """Replace a user's password (operator action). Returns False if there is no such user."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    with session_scope() as session:
        user = session.scalar(select(AppUser).where(func.lower(AppUser.email) == normalize_email(email)))
        if user is None:
            return False
        user.password_hash = hash_password(password)
        return True


def authenticate(email: str, password: str) -> dict | None:
    """The user if the credentials are valid and the account is active, else None."""
    email = normalize_email(email)
    with session_scope() as session:
        user = session.scalar(select(AppUser).where(func.lower(AppUser.email) == email))
        valid = verify_password(password, user.password_hash if user else _DUMMY_HASH)
        if user is None or not valid or not user.is_active:
            return None
        user.last_login_at = datetime.now(timezone.utc)
        return user_dict(user)


def get_active_user(user_id: str) -> dict | None:
    with session_scope() as session:
        user = session.get(AppUser, user_id)
        return user_dict(user) if user is not None and user.is_active else None


def seed_admin_if_empty() -> bool:
    """Create the first user from ADMIN_EMAIL/ADMIN_PASSWORD when no users exist. Returns True if created."""
    settings = get_settings()
    if not (settings.admin_email and settings.admin_password):
        return False
    with session_scope() as session:
        if session.scalar(select(func.count()).select_from(AppUser)):
            return False
    try:
        create_user(settings.admin_email, settings.admin_password)
    except AuthError as exc:
        logger.error("Could not create the ADMIN_EMAIL user: %s", exc)
        return False
    logger.info("Created the first user (%s) from ADMIN_EMAIL/ADMIN_PASSWORD. Remove them from .env now.", settings.admin_email)
    return True


# ---------------------------------------------------------------------------- throttle


class LoginThrottle:
    """Blocks a (client, email) pair after too many failures in a window. In-memory: fine for one API process."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 600) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        cutoff = time.monotonic() - self.window
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.max_failures

    def record_failure(self, key: str) -> None:
        self._recent(key)
        self._failures.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
