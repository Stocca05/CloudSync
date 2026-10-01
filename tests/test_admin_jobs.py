from conftest import login, seed_remotes

from cloudsync.models import Job
from cloudsync.scheduler import bandwidths


def test_admin_monitor_priority_and_cancel_other_user(client, app, tmp_path):
    login(client)
    client.post("/api/auth/register", json={"username": "friend", "password": "x"})
    ids = seed_remotes(app, "friend", tmp_path)
    login(client, "friend", "x")
    jobs = [
        client.post("/api/jobs", json={"source_id": ids[0], "destination_id": ids[1]}).json()
        for _ in range(2)
    ]
    assert client.get("/api/admin/jobs").status_code == 403
    assert client.patch("/api/admin/jobs/" + jobs[0]["id"], json={"priority": 2}).status_code == 403
    login(client)
    result = client.get("/api/admin/jobs").json()
    assert len(result["items"]) == 2 and result["items"][0]["username"] == "friend"
    assert client.patch("/api/admin/jobs/" + jobs[0]["id"], json={"priority": 2}).status_code == 200
    claim = client.post("/internal/claim", headers={"Authorization": "Bearer worker-secret"}).json()["job"]
    assert claim["id"] == jobs[0]["id"]
    client.post("/internal/claim", headers={"Authorization": "Bearer worker-secret"})
    with app.state.factory() as db:
        rates = bandwidths(db)
        assert rates[jobs[0]["id"]] == 3 * rates[jobs[1]["id"]]
        assert sum(rates.values()) <= app.state.settings.global_bps
    assert client.patch("/api/admin/jobs/" + jobs[1]["id"], json={"priority": 2}).status_code == 200
    with app.state.factory() as db:
        rates = bandwidths(db)
        assert rates[jobs[0]["id"]] == rates[jobs[1]["id"]]
    assert client.post("/api/admin/jobs/" + jobs[0]["id"] + "/cancel").status_code == 200
    with app.state.factory() as db:
        assert db.get(Job, jobs[0]["id"]).cancel_requested


def test_admin_cluster_metrics_and_node_management(client, app, tmp_path):
    login(client)
    # Check enriched cluster metrics
    cluster = client.get("/api/admin/cluster").json()
    assert "total_active_slots" in cluster
    assert "success_rate" in cluster
    assert "used_slots" in cluster
    assert "online_nodes" in cluster
    assert "total_nodes" in cluster

    # Create node
    res_create = client.post("/api/admin/nodes", json={"name": "worker-01", "slots": 2, "bandwidth_bps": 52428800})
    assert res_create.status_code == 201

    # Update node limits and maintenance status
    res = client.patch("/api/admin/nodes/worker-01", json={"slots": 4, "bandwidth_bps": 104857600, "enabled": False})
    assert res.status_code == 200

    # Regenerate node token
    res_tok = client.post("/api/admin/nodes/worker-01/token")
    assert res_tok.status_code == 200
    assert "token" in res_tok.json()
    assert res_tok.json()["id"] == "worker-01"


def test_user_management_and_job_deletion(client, app, tmp_path):
    login(client)
    client.post("/api/auth/register", json={"username": "alice", "password": "password123"})
    users = client.get("/api/admin/cluster").json()["users"]
    alice = next(u for u in users if u["username"] == "alice")

    # Update alice: make admin and change password
    res = client.patch(
        f"/api/admin/users/{alice['id']}",
        json={"weight": 3, "max_jobs": 4, "bandwidth_bps": 52428800, "enabled": True, "admin": True, "password": "newpassword123"},
    )
    assert res.status_code == 200
    assert res.json()["admin"] is True

    # Test login with new password
    login(client, "alice", "newpassword123")
    ids = seed_remotes(app, "alice", tmp_path)
    job = client.post("/api/jobs", json={"source_id": ids[0], "destination_id": ids[1]}).json()

    # Deleting running/queued job fails
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 400

    # Cancel job
    client.post(f"/api/jobs/{job['id']}/cancel")

    # Deleting cancelled job succeeds
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 200
    assert not any(j["id"] == job["id"] for j in client.get("/api/jobs").json())

