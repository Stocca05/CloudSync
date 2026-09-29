import hashlib
import os
import shutil
import socket
import threading
import time

import pytest
import uvicorn
from conftest import login, seed_remotes

from cloudsync.worker import STOP, command, run_job


@pytest.mark.skipif(
    not shutil.which("rclone"), reason="Install rclone to run real transfer integration tests"
)
def test_real_rclone_transfer_and_listing(app, client, tmp_path):
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)
    source = tmp_path / "admin" / "source"
    destination = tmp_path / "admin" / "destination"
    content = os.urandom(128 * 1024)
    (source / "example.bin").write_bytes(content)
    (source / "nested").mkdir()
    (source / "nested" / "hello.txt").write_text("Ciao dal cluster")
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
        response = client.post("/api/jobs", json={"source_id": ids[0], "destination_id": ids[1]})
        assert response.status_code == 201
        assigned = client.post("/internal/claim", headers={"Authorization": "Bearer worker-secret"}).json()[
            "job"
        ]
        run_job(f"http://127.0.0.1:{port}", "worker-secret", assigned)
        job = client.get("/api/jobs/" + assigned["id"]).json()
        assert job["status"] == "completed", job
        assert (
            hashlib.sha256((destination / "example.bin").read_bytes()).digest()
            == hashlib.sha256(content).digest()
        )
        assert (destination / "nested" / "hello.txt").read_text() == "Ciao dal cluster"
        assert (source / "example.bin").exists()
        listing = client.post("/api/jobs", json={"source_id": ids[1], "operation": "list"}).json()
        assigned = client.post("/internal/claim", headers={"Authorization": "Bearer worker-secret"}).json()[
            "job"
        ]
        run_job(f"http://127.0.0.1:{port}", "worker-secret", assigned)
        result = client.get("/api/jobs/" + listing["id"]).json()
        assert result["status"] == "completed", result
        assert {i["Name"] for i in result["result"]["items"]} == {"example.bin", "nested"}
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_command_uses_argument_list_and_byte_limits(tmp_path):
    job = {
        "source_id": "a",
        "destination_id": "b",
        "source_path": "file $(touch bad)",
        "destination_path": "copy",
        "operation": "copy",
        "is_file": True,
        "bandwidth_bps": 1024,
    }
    args = command(job, tmp_path / "rclone.conf", 9876, tmp_path / "log")
    assert args[:2] == ["rclone", "copyto"]
    assert args[2] == "ra:file $(touch bad)"
    assert args[-1] == "1024B"
