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
