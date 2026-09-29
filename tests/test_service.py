import time

import pytest
from conftest import login, seed_remotes
from sqlalchemy import select

from cloudsync.models import Job, Remote, User
from cloudsync.providers import safe_path
from cloudsync.scheduler import bandwidths, maintain
from cloudsync.security import verify_password

WORKER = {"Authorization": "Bearer worker-secret"}


def new_job(client, ids, **options):
    result = client.post("/api/jobs", json={"source_id": ids[0], "destination_id": ids[1], **options})
    assert result.status_code == 201, result.text
    return result.json()


def test_simple_login_and_isolation(client, app, tmp_path):
    assert client.get("/api/jobs").status_code == 401
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)
    job = new_job(client, ids)
    assert client.post("/api/auth/register", json={"username": "friend", "password": "x"}).status_code == 201
    login(client, "friend", "x")
    assert client.get("/api/jobs").json() == []
    assert client.get("/api/remotes").json() == []
    assert client.get("/api/jobs/" + job["id"]).status_code == 404
    assert client.post("/api/jobs/" + job["id"] + "/cancel").status_code == 404
    assert client.post("/api/jobs", json={"source_id": ids[0], "destination_id": ids[1]}).status_code == 404
    assert client.get("/api/admin/cluster").status_code == 403
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/me").status_code == 401
    with app.state.factory() as db:
        user = db.scalar(select(User).where(User.username == "friend"))
        assert user.password_hash != "x" and verify_password("x", user.password_hash)


def test_csrf_and_provider_boundary(client):
    login(client)
    assert client.post("/api/auth/logout", headers={"X-CloudSync-Request": ""}).status_code == 403
    assert client.post("/api/auth/logout", headers={"Origin": "https://attacker.invalid"}).status_code == 403
    assert (
        client.post("/api/remotes", json={"name": "bad", "provider": "local", "config": {}}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/remotes",
            json={
                "name": "bad",
                "provider": "webdav",
                "config": {"url": "https://127.0.0.1", "user": "a", "pass": "b"},
            },
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/remotes",
            json={"name": "bad", "provider": "sftp", "config": {"host": "example.com", "shell_type": "unix"}},
        ).status_code
        == 400
    )
    assert client.post("/internal/claim", headers={"Authorization": "Bearer bad"}).status_code == 401


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "remote:x", "a/../../b", "a\\b", "a\nkey"])
def test_paths(path):
    with pytest.raises(ValueError):
        safe_path(path)


def test_queue_lease_and_cancellation(client, app, tmp_path):
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)
    job = new_job(client, ids)
    assignment = client.post("/internal/claim", headers=WORKER).json()["job"]
    assert assignment["id"] == job["id"]
    assert client.post("/internal/claim", headers=WORKER).json()["job"] is None
    wrong = {"lease_token": "wrong", "stats": {}}
    endpoint = "/internal/jobs/" + job["id"]
    assert client.post(endpoint + "/heartbeat", headers=WORKER, json=wrong).status_code == 409
    client.post("/api/jobs/" + job["id"] + "/cancel")
    payload = {"lease_token": assignment["lease_token"], "stats": {"bytes": 300}}
    assert client.post(endpoint + "/heartbeat", headers=WORKER, json=payload).json()["cancel"]
    assert (
        client.post(
            endpoint + "/complete", headers=WORKER, json={**payload, "success": False, "cancelled": True}
        ).status_code
        == 200
    )
    assert client.get("/api/jobs/" + job["id"]).json()["status"] == "cancelled"
    assert (
        client.post(endpoint + "/complete", headers=WORKER, json={**payload, "success": True}).status_code
        == 409
    )
    assert client.post("/api/jobs/" + job["id"] + "/retry").json()["status"] == "queued"


def test_expired_lease_fenced_and_requeued(client, app, tmp_path):
    login(client)
    job = new_job(client, seed_remotes(app, "admin", tmp_path))
    old = client.post("/internal/claim", headers=WORKER).json()["job"]
    with app.state.factory.begin() as db:
        db.get(Job, job["id"]).lease_until = time.time() - 1
        db.flush()
        maintain(db)
        db.get(Job, job["id"]).available_at = 0
    new = client.post("/internal/claim", headers=WORKER).json()["job"]
    assert new["id"] == old["id"] and new["lease_token"] != old["lease_token"]
    result = client.post(
        "/internal/jobs/" + job["id"] + "/complete",
        headers=WORKER,
        json={"lease_token": old["lease_token"], "success": True},
    )
    assert result.status_code == 409


def test_weighted_bandwidth_is_per_user_not_job(client, app, tmp_path):
    admin = login(client)
    admin_ids = seed_remotes(app, "admin", tmp_path)
    client.post("/api/auth/register", json={"username": "friend", "password": "x"})
    login(client, "friend", "x")
    friend_ids = seed_remotes(app, "friend", tmp_path)
    friend_job = new_job(client, friend_ids)
    login(client)
    admin_job = new_job(client, admin_ids)
    client.patch("/api/admin/users/" + admin["id"], json={"weight": 2, "max_jobs": 3, "bandwidth_bps": 0})
    client.post("/internal/claim", headers=WORKER)
    client.post("/internal/claim", headers=WORKER)
    with app.state.factory() as db:
        rates = bandwidths(db)
        assert rates[admin_job["id"]] == rates[friend_job["id"]] * 2
        assert sum(rates.values()) <= app.state.settings.global_bps
    # A per-user cap cannot be bypassed by adding more jobs.
    client.patch(
        "/api/admin/users/" + admin["id"], json={"weight": 2, "max_jobs": 3, "bandwidth_bps": 1048576}
    )
    with app.state.factory() as db:
        assert bandwidths(db)[admin_job["id"]] == 1048576


def test_schedule_does_not_overlap(client, app, tmp_path):
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)
    result = client.post(
        "/api/schedules",
        json={"job": {"source_id": ids[0], "destination_id": ids[1]}, "interval_seconds": 300},
    )
    assert result.status_code == 201
    client.post("/internal/claim", headers=WORKER)
    from cloudsync.models import Schedule

    with app.state.factory.begin() as db:
        db.get(Schedule, result.json()["id"]).next_run = 0
        db.flush()
        maintain(db)
        assert len(list(db.scalars(select(Job)))) == 1
    assert client.delete("/api/schedules/" + result.json()["id"]).status_code == 200


def test_move_requires_explicit_confirmation(client, app, tmp_path):
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)
    assert (
        client.post(
            "/api/jobs", json={"source_id": ids[0], "destination_id": ids[1], "operation": "move"}
        ).status_code
        == 400
    )
    assert new_job(client, ids, operation="move", confirm_move=True)["operation"] == "move"


def test_encrypted_remotes_do_not_leak(client, app):
    login(client)
    token = '{"access_token":"SECRET-ACCESS","refresh_token":"SECRET-REFRESH"}'
    result = client.post(
        "/api/remotes", json={"name": "Drive", "provider": "drive", "config": {"token": token}}
    )
    assert result.status_code == 201
    assert "SECRET" not in client.get("/api/remotes").text
    with app.state.factory() as db:
        remote = db.get(Remote, result.json()["id"])
        assert "SECRET" not in remote.encrypted_config
        assert app.state.vault.decrypt(remote.encrypted_config)["token"] == token
