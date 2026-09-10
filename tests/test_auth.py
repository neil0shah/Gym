from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import HTTPException

from app import auth as auth_module


def make_request(path, authenticated=False):
    session = {"authenticated": True} if authenticated else {}
    return SimpleNamespace(url=SimpleNamespace(path=path), session=session)


def test_verify_credentials_disabled_returns_false(monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", False)
    assert auth_module.verify_credentials("a@b.com", "whatever") is False


def test_verify_credentials_correct_password(monkeypatch):
    pw_hash = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth_module, "AUTH_EMAIL", "me@example.com")
    monkeypatch.setattr(auth_module, "AUTH_PASSWORD_HASH", pw_hash)

    assert auth_module.verify_credentials("me@example.com", "secret123") is True
    assert auth_module.verify_credentials("ME@EXAMPLE.COM", "secret123") is True
    assert auth_module.verify_credentials("me@example.com", "wrong-password") is False
    assert auth_module.verify_credentials("someone-else@example.com", "secret123") is False


def test_require_login_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", False)
    auth_module.require_login(make_request("/import"))  # must not raise


def test_require_login_allows_login_and_static_paths_unauthenticated(monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_ENABLED", True)
    auth_module.require_login(make_request("/login"))
    auth_module.require_login(make_request("/logout"))
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
    auth_module.require_login(make_request("/import", authenticated=True))
    auth_module.require_login(make_request("/api/exercises", authenticated=True))
