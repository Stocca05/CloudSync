import json
import threading
from types import SimpleNamespace

import httpx
from conftest import login

from cloudsync import icloud_auth
from cloudsync.models import AuthAnswer, Remote

WORKER = {"Authorization": "Bearer worker-secret"}


def test_icloud_challenge_ownership_encryption_ack(client, app):
    login(client)
    remote = client.post(
        "/api/remotes",
        json={
            "name": "Apple",
            "provider": "iclouddrive",
            "config": {"apple_id": "example@icloud.com", "password": "fake-password"},
        },
    )
    assert remote.status_code == 201
    remote_id = remote.json()["id"]
    assert client.post("/api/jobs", json={"source_id": remote_id, "operation": "list"}).status_code == 409
    job = client.post("/api/remotes/" + remote_id + "/connect").json()
    assert client.post("/api/remotes/" + remote_id + "/connect").json()["id"] == job["id"]
    assigned = client.post("/internal/claim", headers=WORKER).json()["job"]
    heartbeat = {
        "lease_token": assigned["lease_token"],
        "challenge": {"id": "challenge-one", "name": "2FA", "help": "Codice Apple", "secret": True},
    }
    endpoint = "/internal/jobs/" + job["id"]
    assert client.post(endpoint + "/heartbeat", headers=WORKER, json=heartbeat).status_code == 200
    assert (
        client.post(
            "/api/jobs/" + job["id"] + "/answer", json={"challenge_id": "wrong", "answer": "123456"}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/jobs/" + job["id"] + "/answer", json={"challenge_id": "challenge-one", "answer": "123456"}
        ).status_code
        == 200
    )
    with app.state.factory() as db:
        answer = db.get(AuthAnswer, job["id"])
        assert "123456" not in answer.encrypted_value
    assert "123456" not in client.get("/api/jobs/" + job["id"]).text
    control = client.post(endpoint + "/heartbeat", headers=WORKER, json=heartbeat).json()
    assert control["answer"]["answer"] == "123456"
    ack = {**heartbeat, "ack_answer": control["answer"]["id"], "challenge": {}}
    assert "answer" not in client.post(endpoint + "/heartbeat", headers=WORKER, json=ack).json()
    client.post("/api/auth/register", json={"username": "friend", "password": "x"})
    login(client, "friend", "x")
    assert (
        client.post(
            "/api/jobs/" + job["id"] + "/answer", json={"challenge_id": "challenge-one", "answer": "123456"}
        ).status_code
        == 404
    )


def test_full_icloud_conversation_protocol(client, app, monkeypatch):
    """Real application endpoints, simulated Apple/Rclone response; no Apple account used."""
    login(client)
    remote = client.post(
        "/api/remotes",
        json={
            "name": "Apple",
            "provider": "iclouddrive",
            "config": {"apple_id": "example@icloud.com", "password": "fake-password"},
        },
    ).json()
    job = client.post("/api/remotes/" + remote["id"] + "/connect").json()
    assigned = client.post("/internal/claim", headers=WORKER).json()["job"]
    original_client = httpx.Client
    calls = []

    def rclone(request):
        data = json.loads(request.content or b"{}")
        calls.append(request.url.path)
        if request.url.path == "/config/create":
            assert data["type"] == "iclouddrive"
            assert data["opt"]["nonInteractive"] and data["opt"]["obscure"]
            return httpx.Response(
                200, json={"State": "2fa_do", "Option": {"Name": "config_2fa", "Help": "Enter code"}}
            )
        if request.url.path == "/config/update":
            assert data["opt"]["result"] == "123456"
            assert data["opt"]["state"] == "2fa_do"
            return httpx.Response(200, json={"State": ""})
        if request.url.path == "/config/get":
            return httpx.Response(
                200,
                json={
                    "trust_token": "fake-trust",
                    "cookies": "fake-cookies",
                    "password": "must-not-overwrite",
                },
            )
        return httpx.Response(200, json={})

    class API:
        answered = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, path, json):
            response = client.post(path, headers=WORKER, json=json)
            challenge = json.get("challenge")
            if challenge and not self.answered:
                reply = client.post(
                    "/api/jobs/" + job["id"] + "/answer",
                    json={"challenge_id": challenge["id"], "answer": "123456"},
                )
                assert reply.status_code == 200, reply.text
                self.answered = True
            return response

    def clients(base_url, **kwargs):
        return (
            API()
            if base_url == "http://control"
            else original_client(base_url=base_url, transport=httpx.MockTransport(rclone), **kwargs)
        )

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, *args):
            pass

        def getsockname(self):
            return ("127.0.0.1", 12345)

    class Process:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 0

        def wait(self, **kwargs):
            return self.returncode

    monkeypatch.setattr(icloud_auth, "socket", SimpleNamespace(socket=Socket))
    monkeypatch.setattr(
        icloud_auth, "subprocess", SimpleNamespace(Popen=lambda *a, **k: Process(), DEVNULL=-3)
    )
    monkeypatch.setattr(icloud_auth.httpx, "Client", clients)
    icloud_auth.configure_icloud("http://control", "worker-secret", assigned, threading.Event())
    result = client.get("/api/jobs/" + job["id"]).json()
    assert result["status"] == "completed", result
    with app.state.factory() as db:
        saved = app.state.vault.decrypt(db.get(Remote, remote["id"]).encrypted_config)
        assert saved["trust_token"] == "fake-trust"
        assert saved["password"] == "fake-password"
        assert db.get(AuthAnswer, job["id"]) is None
    assert "/config/create" in calls and "/config/update" in calls
