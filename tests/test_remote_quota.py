import pytest
import shutil
import socket
import threading
import time
from conftest import login, seed_remotes
import uvicorn

from cloudsync.worker import STOP, run_job


def test_remote_quota_job_lifecycle(app, client, tmp_path):
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)
    remote_id = ids[0]

    # Initial check: no quota job yet
    res_get = client.get(f"/api/remotes/{remote_id}/quota")
    assert res_get.status_code == 200
    assert res_get.json()["status"] == "none"

    # Trigger quota job
    res_post = client.post(f"/api/remotes/{remote_id}/quota")
    assert res_post.status_code == 201
    job_data = res_post.json()
    assert job_data["operation"] == "about"
    assert job_data["status"] == "queued"

    # Redundant request reuses active job
    res_repeat = client.post(f"/api/remotes/{remote_id}/quota")
    assert res_repeat.status_code == 201
    assert res_repeat.json()["id"] == job_data["id"]

    # Verify about jobs are not returned in main jobs list
    jobs_list = client.get("/api/jobs").json()
    assert not any(j["id"] == job_data["id"] for j in jobs_list)


@pytest.mark.skipif(
    not shutil.which("rclone"), reason="Install rclone to test real quota execution"
)
def test_real_remote_quota_execution(app, client, tmp_path):
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)
    remote_id = ids[0]

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    STOP.clear()

    try:
        # Create quota job
        res_post = client.post(f"/api/remotes/{remote_id}/quota")
        assert res_post.status_code == 201
        job_id = res_post.json()["id"]

        # Worker claims job
        claim_res = client.post("/internal/claim", headers={"Authorization": "Bearer worker-secret"})
        assert claim_res.status_code == 200
        assigned = claim_res.json()["job"]
        assert assigned["id"] == job_id
        assert assigned["operation"] == "about"

        # Worker runs job
        run_job(f"http://127.0.0.1:{port}", "worker-secret", assigned)

        # Verify job completed and quota returned
        job_status = client.get(f"/api/jobs/{job_id}").json()
        assert job_status["status"] == "completed"
        result = job_status["result"]
        assert "supported" in result

        # Check GET /api/remotes/{remote_id}/quota returns cached result
        res_get = client.get(f"/api/remotes/{remote_id}/quota")
        assert res_get.status_code == 200
        data = res_get.json()
        assert data["status"] == "completed"
        assert data["result"]["supported"] is True
        assert data["result"]["total"] > 0
    finally:
        server.should_exit = True
        thread.join(timeout=3)
