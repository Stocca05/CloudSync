import json
from urllib.parse import parse_qs, urlsplit

import httpx
from conftest import login
from sqlalchemy import select

from cloudsync.models import GoogleAuthorization, Remote


def setup_google(app):
    app.state.settings.google_client_id = "client.apps.googleusercontent.com"
    app.state.settings.google_client_secret = "test-secret"
    app.state.settings.public_url = "https://cloud.example.org"


def test_google_disabled_and_state_session_binding(client, app):
    login(client)
    assert client.post("/api/google/start", json={"name": "Drive"}).json()["method"] == "rclone"
    setup_google(app)
    response = client.post("/api/google/start", json={"name": "Drive"}).json()
    fields = parse_qs(urlsplit(response["url"]).query)
    assert fields["redirect_uri"] == ["https://cloud.example.org/api/google/callback"]
    assert fields["code_challenge_method"] == ["S256"]
    assert "test-secret" not in response["url"]
    state = fields["state"][0]
    login(client)  # A different login session for the same account cannot consume it.
    result = client.get(
        "/api/google/callback", params={"state": state, "code": "fake"}, follow_redirects=False
    )
    assert result.headers["location"] == "/?google=expired"
    client.post("/api/auth/register", json={"username": "friend", "password": "x"})
    login(client, "friend", "x")
    assert (
        client.get(
            "/api/google/callback", params={"state": state, "code": "fake"}, follow_redirects=False
        ).headers["location"]
        == "/?google=expired"
    )


def test_google_exchange_encrypted_and_single_use(client, app, monkeypatch):
    login(client)
    setup_google(app)
    url = client.post("/api/google/start", json={"name": "My Google"}).json()["url"]
    state = parse_qs(urlsplit(url).query)["state"][0]
    original_client = httpx.Client
    calls = []

    def exchange(request):
        data = parse_qs(request.content.decode())
        assert data["client_secret"] == ["test-secret"]
        assert data["code_verifier"]
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "access-fixture",
                "refresh_token": "refresh-fixture",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

    monkeypatch.setattr(
        "cloudsync.google_auth.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(exchange), **kwargs),
    )
    result = client.get(
        "/api/google/callback", params={"state": state, "code": "code-fixture"}, follow_redirects=False
    )
    assert result.headers["location"] == "/?google=connected"
    assert (
        client.get(
            "/api/google/callback", params={"state": state, "code": "code-fixture"}, follow_redirects=False
        ).headers["location"]
        == "/?google=expired"
    )
    assert len(calls) == 1
    with app.state.factory() as db:
        remote = db.scalar(select(Remote))
        assert "refresh-fixture" not in remote.encrypted_config
        token = json.loads(app.state.vault.decrypt(remote.encrypted_config)["token"])
        assert token["refresh_token"] == "refresh-fixture" and token["expiry"]
        assert db.scalar(select(GoogleAuthorization)) is None
    assert "refresh-fixture" not in client.get("/api/remotes").text


def test_google_denial_and_expiry(client, app):
    login(client)
    setup_google(app)

    def start():
        return parse_qs(
            urlsplit(client.post("/api/google/start", json={"name": "Drive"}).json()["url"]).query
        )["state"][0]

    state = start()
    assert (
        client.get(
            "/api/google/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False
        ).headers["location"]
        == "/?google=cancelled"
    )
    state = start()
    with app.state.factory.begin() as db:
        db.scalar(select(GoogleAuthorization)).expires = 0
    assert (
        client.get(
            "/api/google/callback", params={"state": state, "code": "x"}, follow_redirects=False
        ).headers["location"]
        == "/?google=expired"
    )


def test_rclone_pairing_single_use_bound_to_owner(client, app):
    login(client)
    result = client.post("/api/google/start", json={"name": "Rclone Drive"}).json()
    ticket = parse_qs(urlsplit(result["url"]).query)["ticket"][0]
    headers = {"Authorization": "Bearer " + ticket}
    assert client.get("/api/google/rclone/check", headers=headers).json()["username"] == "admin"
    client.cookies.clear()  # Helper does not have the browser cookie.
    result = client.post(
        "/api/google/rclone/complete",
        headers=headers,
        json={
            "token": {"access_token": "access", "refresh_token": "refresh", "expiry": "2030-01-01T00:00:00Z"}
        },
    )
    assert result.status_code == 200
    assert (
        client.post(
            "/api/google/rclone/complete",
            headers=headers,
            json={"token": {"access_token": "access", "refresh_token": "refresh"}},
        ).status_code
        == 401
    )
    with app.state.factory() as db:
        remote = db.scalar(select(Remote))
        config = app.state.vault.decrypt(remote.encrypted_config)
        assert config["type"] == "drive" and "client_id" not in config


def test_rclone_pairing_revoked_on_logout(client, app):
    login(client)
    result = client.post("/api/google/start", json={"name": "Drive"}).json()
    ticket = parse_qs(urlsplit(result["url"]).query)["ticket"][0]
    client.post("/api/auth/logout")
    assert (
        client.get("/api/google/rclone/check", headers={"Authorization": "Bearer " + ticket}).status_code
        == 401
    )
