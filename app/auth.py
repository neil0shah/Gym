"""Single-user login, opt-in via environment variables.

Local/laptop usage needs no setup: if AUTH_EMAIL and AUTH_PASSWORD_HASH
aren't both set, the app runs exactly as before with no login wall. Once
deployed somewhere reachable from outside your own machine, set both env
vars (see README) to require a login before anything — pages or API — is
reachable.

This is deliberately a single hardcoded account, not a user table: there's
only one person using this app, so a full signup/reset flow would be
solving a problem that doesn't exist.
"""
import os

import bcrypt
from fastapi import HTTPException, Request
from starlette.responses import RedirectResponse

AUTH_EMAIL = os.environ.get("AUTH_EMAIL")
AUTH_PASSWORD_HASH = os.environ.get("AUTH_PASSWORD_HASH")
AUTH_ENABLED = bool(AUTH_EMAIL and AUTH_PASSWORD_HASH)

SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 90  # 90 days — "stay logged in"

_EXEMPT_PATHS = {"/login", "/logout"}


class NotAuthenticated(Exception):
    """Raised by require_login for page routes; the registered exception
    handler turns this into a redirect to /login (see app/main.py)."""


def verify_credentials(email: str, password: str) -> bool:
    if not AUTH_ENABLED:
        return False
    if email.strip().lower() != AUTH_EMAIL.strip().lower():
        return False
    return bcrypt.checkpw(password.encode("utf-8"), AUTH_PASSWORD_HASH.encode("utf-8"))


def require_login(request: Request) -> None:
    if not AUTH_ENABLED:
        return
    if request.url.path in _EXEMPT_PATHS or request.url.path.startswith("/static/"):
        return
    if request.session.get("authenticated"):
        return
    if request.url.path.startswith("/api/"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    raise NotAuthenticated()


def not_authenticated_handler(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)
