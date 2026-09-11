"""Multi-user login, opt-in via environment variables for the owner account.

Local/laptop usage needs no setup: if AUTH_EMAIL and AUTH_PASSWORD_HASH
aren't both set, the app runs exactly as before with no login wall — every
request is transparently scoped to one fixed local account (see
LOCAL_USER_EMAIL) so the rest of the app can always assume a current user
without extra branching between "auth on" and "auth off".

Once deployed somewhere reachable from outside your own machine, set both
env vars (see README) to bootstrap the owner account and require a login
before anything — pages or API — is reachable. Each account's data
(exercises, groups, workout history) is completely separate from every
other account's.

To let someone else use the same deployment with their own separate data,
add them with `python3 -m app.create_user <email>` (see app/create_user.py
and the README) — there's no public signup page. This app has no email
verification, password reset, or login rate limiting, so every account is
created deliberately by the owner rather than through open registration.
"""
import os

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession
from starlette.responses import RedirectResponse

from app import models
from app.database import get_db

AUTH_EMAIL = os.environ.get("AUTH_EMAIL")
AUTH_PASSWORD_HASH = os.environ.get("AUTH_PASSWORD_HASH")
AUTH_ENABLED = bool(AUTH_EMAIL and AUTH_PASSWORD_HASH)

SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 90  # 90 days — "stay logged in"

_EXEMPT_PATHS = {"/login", "/logout"}

# Fixed account used for every request when login is disabled, so unscoped
# local/dev usage still has a concrete user_id to hang data off of.
LOCAL_USER_EMAIL = "local@localhost"


class NotAuthenticated(Exception):
    """Raised by require_login for page routes; the registered exception
    handler turns this into a redirect to /login (see app/main.py)."""


def bootstrap_owner_and_seed(db: DBSession) -> None:
    """Idempotent: ensure the owner account (or, with login disabled, the
    fixed local account) exists and has its default exercise list seeded.
    Safe to call on every startup — a no-op once the account already exists
    (including one created by the database migration for a pre-existing
    single-user deployment, see app/database.py)."""
    from app import seed_data  # local import: avoids a circular import at module load

    if AUTH_ENABLED:
        email = AUTH_EMAIL.strip().lower()
        password_hash = AUTH_PASSWORD_HASH
    else:
        email = LOCAL_USER_EMAIL
        password_hash = ""

    user = db.query(models.User).filter(models.User.email == email).first()
    if user is None:
        user = models.User(email=email, password_hash=password_hash)
        db.add(user)
        db.commit()
        db.refresh(user)
    seed_data.seed_if_empty(db, user_id=user.id)


def verify_credentials(db: DBSession, email: str, password: str) -> "models.User | None":
    if not AUTH_ENABLED:
        return None
    user = db.query(models.User).filter(
        models.User.email == email.strip().lower()
    ).first()
    if user is None:
        return None
    if bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8")):
        return user
    return None


def require_login(request: Request) -> None:
    if not AUTH_ENABLED:
        return
    if request.url.path in _EXEMPT_PATHS or request.url.path.startswith("/static/"):
        return
    if request.session.get("user_id"):
        return
    if request.url.path.startswith("/api/"):
        raise HTTPException(status_code=401, detail="Not authenticated")
    raise NotAuthenticated()


def not_authenticated_handler(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=302)


def get_current_user(request: Request, db: DBSession = Depends(get_db)) -> models.User:
    """The account whose data this request should see. With login disabled
    this is always the same fixed local account; with login enabled it's
    whoever request.session["user_id"] says (set on successful /login)."""
    if not AUTH_ENABLED:
        user = db.query(models.User).filter(models.User.email == LOCAL_USER_EMAIL).first()
        if user is not None:
            return user
        raise HTTPException(status_code=500, detail="Local account not initialized")
    user_id = request.session.get("user_id")
    user = db.query(models.User).filter(models.User.id == user_id).first() if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
