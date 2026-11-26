"""Login/logout/me endpoints, and the dependency that protects every other API route."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ingestion_pipeline.config import get_settings
from ingestion_pipeline.core import auth as auth_core

COOKIE_NAME = "archive_session"

# Everything not listed here requires a signed-in user, so a new endpoint is protected by default.
PUBLIC_PATHS = frozenset({"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/signup", "/api/auth/config", "/api/nango/webhook", "/openapi.json", "/docs", "/redoc"})

router = APIRouter(prefix="/api/auth", tags=["auth"])
throttle = auth_core.LoginThrottle()
signup_throttle = auth_core.LoginThrottle(max_failures=10, window_seconds=3600)  # counts every attempt per client


class LoginBody(BaseModel):
    email: str
    password: str


class SignupBody(BaseModel):
    email: str
    password: str
    invite_code: str = ""


def require_user(request: Request) -> None:
    """Global dependency: 401 unless the request carries a valid session cookie for an active user."""
    if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return
    user = _user_from_request(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    request.state.user = user


def _user_from_request(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE_NAME)
    user_id = auth_core.read_token(token) if token else None
    return auth_core.get_active_user(user_id) if user_id else None


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=settings.auth_session_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.auth_cookie_secure,
        path="/",
    )


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response) -> dict:
    key = f"{request.client.host if request.client else 'unknown'}|{auth_core.normalize_email(body.email)}"
    if throttle.blocked(key):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a few minutes.")
    user = auth_core.authenticate(body.email, body.password)
    if user is None:
        throttle.record_failure(key)
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    throttle.reset(key)
    _set_cookie(response, auth_core.create_token(user["user_id"]))
    return {"user": user}


@router.get("/config")
def auth_config() -> dict:
    settings = get_settings()
    return {"signup_enabled": settings.allow_signup, "invite_required": bool(settings.signup_invite_code)}


@router.post("/signup")
def signup(body: SignupBody, request: Request, response: Response) -> dict:
    settings = get_settings()
    if not settings.allow_signup:
        raise HTTPException(status_code=403, detail="Sign-up is turned off. Ask an administrator for an account.")
    client = request.client.host if request.client else "unknown"
    if signup_throttle.blocked(client):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")
    signup_throttle.record_failure(client)
    if settings.signup_invite_code and not hmac.compare_digest(body.invite_code.strip(), settings.signup_invite_code):
        raise HTTPException(status_code=403, detail="That invite code is not valid.")
    try:
        user = auth_core.create_user(body.email, body.password)
    except auth_core.AuthError as exc:
        status = 409 if "already exists" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc))
    _set_cookie(response, auth_core.create_token(user["user_id"]))
    return {"user": user}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    # require_user already ran (this path is not public), so request.state.user is set.
    return {"user": request.state.user}
