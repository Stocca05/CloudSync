import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from cloudsync.api import create_app
from cloudsync.models import Remote, User
from cloudsync.settings import Settings


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        database_url="sqlite:///" + str(tmp_path / "test.db"),
        encryption_key=Fernet.generate_key().decode(),
        admin_password="a",
        bootstrap_worker_token="worker-secret",
        cookie_secure=False,
        global_bps=12 * 1024 * 1024,
    )
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, headers={"X-CloudSync-Request": "1"}) as client:
        yield client


def login(client, name="admin", password="a"):
    result = client.post("/api/auth/login", json={"username": name, "password": password})
    assert result.status_code == 200, result.text
    return result.json()


def seed_remotes(app, username, tmp_path):
    with app.state.factory.begin() as db:
        user = db.scalar(select(User).where(User.username == username))
        result = []
        for name in ["source", "destination"]:
            directory = tmp_path / username / name
            directory.mkdir(parents=True, exist_ok=True)
            # Internal test fixture only. Public API never accepts local/alias configs.
            remote = Remote(
                user_id=user.id,
                name=name,
                provider="local",
                encrypted_config=app.state.vault.encrypt({"type": "alias", "remote": str(directory)}),
            )
            db.add(remote)
            db.flush()
            result.append(remote.id)
        return result
