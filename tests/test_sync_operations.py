from conftest import login, seed_remotes

from cloudsync.worker import command


def test_sync_and_bisync_job_creation(client, app, tmp_path):
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)

    # Creating sync job
    res = client.post(
        "/api/jobs",
        json={"source_id": ids[0], "destination_id": ids[1], "operation": "sync", "source_path": "folderA", "destination_path": "folderB"},
    )
    assert res.status_code == 201
    assert res.json()["operation"] == "sync"

    # Creating bisync job
    res = client.post(
        "/api/jobs",
        json={"source_id": ids[0], "destination_id": ids[1], "operation": "bisync", "source_path": "folderA", "destination_path": "folderB"},
    )
    assert res.status_code == 201
    assert res.json()["operation"] == "bisync"


def test_sync_rejects_single_file(client, app, tmp_path):
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)

    res = client.post(
        "/api/jobs",
        json={
            "source_id": ids[0],
            "destination_id": ids[1],
            "operation": "sync",
            "source_path": "file.txt",
            "destination_path": "file.txt",
            "is_file": True,
        },
    )
    assert res.status_code == 400
    assert "richiede directory e non singoli file" in res.json()["detail"]

    res_bi = client.post(
        "/api/jobs",
        json={
            "source_id": ids[0],
            "destination_id": ids[1],
            "operation": "bisync",
            "source_path": "file.txt",
            "destination_path": "file.txt",
            "is_file": True,
        },
    )
    assert res_bi.status_code == 400
    assert "richiede directory e non singoli file" in res_bi.json()["detail"]


def test_continuous_schedule_lifecycle(client, app, tmp_path):
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)

    # Interval down to 30s allowed
    spec = {
        "job": {
            "source_id": ids[0],
            "destination_id": ids[1],
            "operation": "bisync",
            "source_path": "dirA",
            "destination_path": "dirB",
        },
        "interval_seconds": 30,
    }
    res = client.post("/api/schedules", json=spec)
    assert res.status_code == 201
    schedule_id = res.json()["id"]

    # Listing schedules
    schedules = client.get("/api/schedules").json()
    assert any(s["id"] == schedule_id for s in schedules)
    item = next(s for s in schedules if s["id"] == schedule_id)
    assert item["enabled"] is True
    assert item["interval_seconds"] == 30
    assert item["template"]["operation"] == "bisync"

    # Toggle pause
    res_pause = client.post(f"/api/schedules/{schedule_id}/toggle")
    assert res_pause.status_code == 200
    assert res_pause.json()["enabled"] is False

    # Toggle resume
    res_resume = client.post(f"/api/schedules/{schedule_id}/toggle")
    assert res_resume.status_code == 200
    assert res_resume.json()["enabled"] is True

    # Immediate trigger
    res_run = client.post(f"/api/schedules/{schedule_id}/run")
    assert res_run.status_code == 200
    assert res_run.json()["triggered"] is True

    # Delete schedule
    res_del = client.delete(f"/api/schedules/{schedule_id}")
    assert res_del.status_code == 200
    assert not any(s["id"] == schedule_id for s in client.get("/api/schedules").json())


def test_schedule_interval_validation(client, app, tmp_path):
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)

    # Less than 30s rejected
    res = client.post(
        "/api/schedules",
        json={
            "job": {
                "source_id": ids[0],
                "destination_id": ids[1],
                "operation": "sync",
            },
            "interval_seconds": 10,
        },
    )
    assert res.status_code == 422


def test_worker_command_sync_and_bisync(tmp_path):
    config = tmp_path / "rclone.conf"
    log = tmp_path / "rclone.log"

    # Sync
    sync_job = {
        "operation": "sync",
        "source_id": "src1",
        "destination_id": "dst1",
        "source_path": "photos",
        "destination_path": "backup/photos",
        "bandwidth_bps": 50000,
    }
    cmd_sync = command(sync_job, config, 44239, log)
    assert cmd_sync[:4] == ["rclone", "sync", "rsrc1:photos", "rdst1:backup/photos"]

    # Bisync
    bisync_job = {
        "operation": "bisync",
        "source_id": "src1",
        "destination_id": "dst1",
        "source_path": "shared",
        "destination_path": "cloud/shared",
        "bandwidth_bps": 50000,
    }
    cmd_bisync = command(bisync_job, config, 44239, log)
    assert cmd_bisync[:4] == ["rclone", "bisync", "rsrc1:shared", "rdst1:cloud/shared"]
    assert "--create-empty-src-dirs" in cmd_bisync
    assert "--resilient" in cmd_bisync
    assert "--recover" in cmd_bisync
    assert "--resync-mode" in cmd_bisync
    assert "newer" in cmd_bisync
    assert "--resync" not in cmd_bisync

    # Bisync with resync
    bisync_resync = {**bisync_job, "resync": True}
    cmd_resync = command(bisync_resync, config, 44239, log)
    assert "--resync" in cmd_resync
