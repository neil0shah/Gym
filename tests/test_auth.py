from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app import models
from app import auth as auth_module
from app import main as main_module


def make_request(path, user_id=None):
    session = {"user_id": user_id} if user_id else {}
    return SimpleNamespace(url=SimpleNamespace(path=path), session=session)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_verify_credentials_disabled_returns_none(db, monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", False)
    assert auth_module.verify_credentials(db, "a@b.com", "whatever") is None


def test_verify_credentials_correct_password(db, monkeypatch):
    pw_hash = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_module, "AUTH_EMAIL", "me@example.com")
    monkeypatch.setattr(auth_module, "AUTH_PASSWORD_HASH", pw_hash)
    db.add(models.User(email="me@example.com", password_hash=pw_hash))
    db.commit()

    user = auth_module.verify_credentials(db, "me@example.com", "secret123")
    assert user is not None and user.email == "me@example.com"
    assert auth_module.verify_credentials(db, "ME@EXAMPLE.COM", "secret123") is not None
    assert auth_module.verify_credentials(db, "me@example.com", "wrong-password") is None
    assert auth_module.verify_credentials(db, "someone-else@example.com", "secret123") is None


def test_verify_credentials_unknown_email_returns_none(db, monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_module, "AUTH_EMAIL", "me@example.com")
    monkeypatch.setattr(auth_module, "AUTH_PASSWORD_HASH", "unused")
    assert auth_module.verify_credentials(db, "nobody@example.com", "whatever") is None


def test_require_login_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", False)
    auth_module.require_login(make_request("/import"))  # must not raise


def test_require_login_allows_login_and_static_paths_unauthenticated(monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    auth_module.require_login(make_request("/login"))
    auth_module.require_login(make_request("/logout"))
    auth_module.require_login(make_request("/signup"))
    auth_module.require_login(make_request("/static/js/import.js"))


def test_require_login_redirects_page_routes_when_unauthenticated(monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    with pytest.raises(auth_module.NotAuthenticated):
        auth_module.require_login(make_request("/import"))


def test_require_login_returns_401_for_api_routes_when_unauthenticated(monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    with pytest.raises(HTTPException) as exc_info:
        auth_module.require_login(make_request("/api/exercises"))
    assert exc_info.value.status_code == 401


def test_require_login_passes_once_authenticated(monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    auth_module.require_login(make_request("/import", user_id=1))
    auth_module.require_login(make_request("/api/exercises", user_id=1))


def test_get_current_user_disabled_returns_fixed_local_account(db, monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", False)
    local = models.User(email=auth_module.LOCAL_USER_EMAIL, password_hash="")
    db.add(local)
    db.commit()

    user = auth_module.get_current_user(make_request("/import"), db=db)
    assert user.email == auth_module.LOCAL_USER_EMAIL


def test_get_current_user_enabled_resolves_session_user_id(db, monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    user = models.User(email="me@example.com", password_hash="x")
    db.add(user)
    db.commit()
    db.refresh(user)

    resolved = auth_module.get_current_user(make_request("/import", user_id=user.id), db=db)
    assert resolved.id == user.id


def test_get_current_user_enabled_raises_401_when_no_session(db, monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    with pytest.raises(HTTPException) as exc_info:
        auth_module.get_current_user(make_request("/import"), db=db)
    assert exc_info.value.status_code == 401


def test_bootstrap_owner_and_seed_creates_owner_from_env(db, monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_module, "AUTH_EMAIL", "owner@example.com")
    monkeypatch.setattr(auth_module, "AUTH_PASSWORD_HASH", "some-hash")

    auth_module.bootstrap_owner_and_seed(db)

    user = db.query(models.User).filter(models.User.email == "owner@example.com").first()
    assert user is not None
    assert user.password_hash == "some-hash"
    assert db.query(models.Exercise).filter(models.Exercise.user_id == user.id).count() > 0


def test_bootstrap_owner_and_seed_is_idempotent(db, monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_module, "AUTH_EMAIL", "owner@example.com")
    monkeypatch.setattr(auth_module, "AUTH_PASSWORD_HASH", "some-hash")

    auth_module.bootstrap_owner_and_seed(db)
    auth_module.bootstrap_owner_and_seed(db)  # must not raise or duplicate

    assert db.query(models.User).filter(models.User.email == "owner@example.com").count() == 1


def test_create_account_success_hashes_password_and_seeds(db):
    user = auth_module.create_account(db, "New.Friend@Example.com", "longenoughpw")
    assert user.email == "new.friend@example.com"  # normalized
    assert bcrypt.checkpw(b"longenoughpw", user.password_hash.encode())
    assert db.query(models.Exercise).filter(models.Exercise.user_id == user.id).count() > 0


def test_create_account_rejects_invalid_email(db):
    with pytest.raises(auth_module.SignupError):
        auth_module.create_account(db, "not-an-email", "longenoughpw")


def test_create_account_rejects_short_password(db):
    with pytest.raises(auth_module.SignupError):
        auth_module.create_account(db, "someone@example.com", "short")


def test_create_account_rejects_duplicate_email_case_insensitive(db):
    auth_module.create_account(db, "someone@example.com", "longenoughpw")
    with pytest.raises(auth_module.SignupError):
        auth_module.create_account(db, "Someone@Example.com", "anotherlongpw")


def test_create_account_leaves_other_accounts_untouched(db):
    alice = auth_module.create_account(db, "alice@example.com", "longenoughpw1")
    bob = auth_module.create_account(db, "bob@example.com", "longenoughpw2")
    assert alice.id != bob.id
    alice_count = db.query(models.Exercise).filter(models.Exercise.user_id == alice.id).count()
    bob_count = db.query(models.Exercise).filter(models.Exercise.user_id == bob.id).count()
    assert alice_count == bob_count > 0


def test_signup_page_redirects_when_auth_disabled(monkeypatch):
    monkeypatch.setattr(main_module, "AUTH_ENABLED", False)
    response = main_module.signup_page(make_request("/signup"))
    assert response.status_code == 302
    assert response.headers["location"] == "/import"


def test_signup_submit_creates_account_and_logs_in(db, monkeypatch):
    monkeypatch.setattr(main_module, "AUTH_ENABLED", True)
    request = make_request("/signup")
    response = main_module.signup_submit(
        request, email="new@example.com", password="longenoughpw",
        confirm_password="longenoughpw", db=db,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/import"

    user = db.query(models.User).filter(models.User.email == "new@example.com").first()
    assert user is not None
    assert request.session["user_id"] == user.id
