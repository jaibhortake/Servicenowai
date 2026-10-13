import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from app import auth
from app.config import settings
from app.routers import auth as auth_router


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "app_secret_key", "test-secret")
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(auth, "_ADMIN_DIGEST", auth._digest("pass@123"))

    app = FastAPI()
    app.include_router(auth_router.router)

    @app.get("/protected", dependencies=[Depends(auth.require_user)])
    def protected():
        return {"ok": True}

    @app.get("/admin-only", dependencies=[Depends(auth.require_admin)])
    def admin_only():
        return {"ok": True}

    return TestClient(app)


def _login(client, password="pass@123"):
    return client.post("/api/auth/login", json={"username": "admin", "password": password})


def test_login_success_returns_token(client):
    res = _login(client)
    assert res.status_code == 200
    assert res.json()["user"] == {"username": "admin", "role": "admin"}


def test_login_rejects_wrong_password(client):
    assert _login(client, "nope").status_code == 401


def test_protected_route_requires_token(client):
    assert client.get("/protected").status_code == 401


def test_protected_route_accepts_bearer_token(client):
    token = _login(client).json()["access_token"]
    res = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200


def test_token_via_query_param_for_websockets(client):
    token = _login(client).json()["access_token"]
    assert client.get(f"/protected?token={token}").status_code == 200


def test_tampered_token_is_rejected(client):
    token = _login(client).json()["access_token"]
    assert client.get("/protected", headers={"Authorization": f"Bearer {token}x"}).status_code == 401


def test_admin_route_allows_admin(client):
    token = _login(client).json()["access_token"]
    assert client.get("/admin-only", headers={"Authorization": f"Bearer {token}"}).status_code == 200
