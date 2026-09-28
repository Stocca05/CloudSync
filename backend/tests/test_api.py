"""Integration and Unit Tests for CloudSync FastAPI Endpoints."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.main import app
from backend.models import JobInfo
from backend.rclone_client import rclone_client


@pytest_asyncio.fixture(autouse=True)
async def setup_client():
    """Ensure rclone client is started for tests."""
    await rclone_client.start()
    yield
    await rclone_client.close()


@pytest.mark.asyncio
async def test_health_endpoint():
    """Verify healthcheck returns expected structure."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "rclone_connected" in data


@pytest.mark.asyncio
async def test_remotes_status_mocked(monkeypatch):
    """Test /api/remotes/status endpoint with mocked Rclone remotes."""

    async def mock_list_remotes():
        return ["gdrive", "icloud"]

    async def mock_get_about(remote):
        return {"total": 1000000000, "used": 500000000, "free": 500000000}

    monkeypatch.setattr(rclone_client, "list_remotes", mock_list_remotes)
    monkeypatch.setattr(rclone_client, "get_about", mock_get_about)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/remotes/status")
        assert response.status_code == 200
        data = response.json()
        assert data["gdrive_configured"] is True
        assert data["icloud_configured"] is True
        assert data["gdrive"]["used_bytes"] == 500000000
        assert data["icloud"]["total_bytes"] == 1000000000


@pytest.mark.asyncio
async def test_list_files_mocked(monkeypatch):
    """Test listing files endpoint formatting Google Docs properly."""

    async def mock_list_directory(remote, path):
        return [
            {
                "Name": "FolderA",
                "Path": "FolderA",
                "Size": 0,
                "IsDir": True,
                "MimeType": "inode/directory",
            },
            {
                "Name": "Report.gdoc",
                "Path": "Report.gdoc",
                "Size": 1024,
                "IsDir": False,
                "MimeType": "application/vnd.google-apps.document",
            },
        ]

    monkeypatch.setattr(rclone_client, "list_directory", mock_list_directory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/fs/list", json={"remote": "gdrive", "path": ""})
        assert response.status_code == 200
        data = response.json()
        assert data["total_items"] == 2
        # Verify directory first
        assert data["items"][0]["is_dir"] is True
        assert data["items"][0]["name"] == "FolderA"
        # Verify Google Doc detection
        assert data["items"][1]["is_gdoc"] is True


@pytest.mark.asyncio
async def test_transfer_move_mocked(monkeypatch):
    """Test dispatching move jobs for file and directory."""
    job_counter = 100

    async def mock_move_file(src_remote, src_path, dst_remote, dst_path, dry_run=False):
        nonlocal job_counter
        job_counter += 1
        return job_counter

    async def mock_move_dir(
        src_remote,
        src_path,
        dst_remote,
        dst_path,
        delete_empty_src_dirs,
        export_formats,
        dry_run=False,
    ):
        nonlocal job_counter
        job_counter += 1
        return job_counter

    monkeypatch.setattr(rclone_client, "move_file", mock_move_file)
    monkeypatch.setattr(rclone_client, "move_directory", mock_move_dir)

    payload = {
        "src_remote": "gdrive",
        "dst_remote": "icloud",
        "dst_path": "Backups",
        "items": [
            {"path": "file1.txt", "is_dir": False},
            {"path": "my_folder", "is_dir": True},
        ],
        "export_docs": True,
        "delete_empty_src_dirs": True,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/transfer/move", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert len(data["jobs"]) == 2
        assert data["jobs"][0]["job_id"] == 101
        assert data["jobs"][1]["job_id"] == 102
        assert data["jobs"][0]["dst_path"] == "Backups/file1.txt"
        assert data["jobs"][1]["dst_path"] == "Backups/my_folder"


@pytest.mark.asyncio
async def test_job_status_mocked(monkeypatch):
    """Test job status retrieval and update."""
    from backend.main import JOB_REGISTRY

    JOB_REGISTRY[999] = JobInfo(
        job_id=999,
        item_path="test.pdf",
        is_dir=False,
        dst_path="test.pdf",
        status="running",
    )

    async def mock_get_job_status(job_id):
        return {
            "jobid": 999,
            "finished": True,
            "success": True,
            "duration": 1.25,
            "error": "",
        }

    monkeypatch.setattr(rclone_client, "get_job_status", mock_get_job_status)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/jobs/999")
        assert response.status_code == 200
        data = response.json()
        assert data["finished"] is True
        assert data["success"] is True
        assert JOB_REGISTRY[999].status == "completed"


@pytest.mark.asyncio
async def test_tunnel_endpoint():
    """Verify tunnel status endpoint."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/system/tunnel")
        assert response.status_code == 200
        data = response.json()
        assert "active" in data


@pytest.mark.asyncio
async def test_bwlimit_endpoint(monkeypatch):
    """Verify bandwidth throttling endpoint."""

    async def mock_set_bwlimit(rate):
        return {"rate": rate}

    monkeypatch.setattr(rclone_client, "set_bwlimit", mock_set_bwlimit)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/system/bwlimit", json={"rate": "10M"})
        assert response.status_code == 200
        data = response.json()
        assert data["rate"] == "10M"


@pytest.mark.asyncio
async def test_history_endpoints():
    """Verify history retrieval and clearing."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        get_res = await ac.get("/api/history")
        assert get_res.status_code == 200
        assert isinstance(get_res.json(), list)

        del_res = await ac.delete("/api/history")
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "success"


@pytest.mark.asyncio
async def test_transfer_copy_mocked(monkeypatch):
    """Test dispatching copy jobs (delete_source=False) without deleting source files."""
    copy_file_called = False
    copy_dir_called = False

    async def mock_copy_file(src_remote, src_path, dst_remote, dst_path, dry_run=False):
        nonlocal copy_file_called
        copy_file_called = True
        return 201

    async def mock_copy_dir(
        src_remote, src_path, dst_remote, dst_path, export_formats, dry_run=False
    ):
        nonlocal copy_dir_called
        copy_dir_called = True
        return 202

    monkeypatch.setattr(rclone_client, "copy_file", mock_copy_file)
    monkeypatch.setattr(rclone_client, "copy_directory", mock_copy_dir)

    payload = {
        "src_remote": "gdrive",
        "dst_remote": "icloud",
        "dst_path": "BackupCopy",
        "items": [
            {"path": "file1.txt", "is_dir": False},
            {"path": "my_folder", "is_dir": True},
        ],
        "delete_source": False,  # COPY MODE!
        "export_docs": True,
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/transfer/move", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert len(data["jobs"]) == 2
        assert copy_file_called is True
        assert copy_dir_called is True
        assert "copia" in data["message"].lower()


@pytest.mark.asyncio
async def test_google_service_account_auth(monkeypatch):
    """Test Service Account JSON authentication endpoint."""
    config_created = {}

    async def mock_config_create(name, remote_type, parameters, obscure=False):
        nonlocal config_created
        config_created = {"name": name, "type": remote_type, "params": parameters}
        return {"status": "ok"}

    monkeypatch.setattr(rclone_client, "config_create", mock_config_create)

    valid_sa = {
        "type": "service_account",
        "project_id": "test-project",
        "client_email": "bot@test-project.iam.gserviceaccount.com",
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\n",
    }

    import json

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Valid payload
        res = await ac.post(
            "/api/auth/google/service-account",
            json={
                "service_account_json": json.dumps(valid_sa),
                "folder_id": "https://drive.google.com/drive/folders/ABC12345",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["email"] == "bot@test-project.iam.gserviceaccount.com"
        assert config_created["name"] == "gdrive"
        assert config_created["params"]["root_folder_id"] == "ABC12345"

        # Invalid payload (not a service account)
        invalid_res = await ac.post(
            "/api/auth/google/service-account",
            json={"service_account_json": json.dumps({"foo": "bar"})},
        )
        assert invalid_res.status_code == 400


@pytest.mark.asyncio
async def test_disconnect_remote(monkeypatch):
    """Test remote disconnect endpoint."""
    deleted_remote = None

    async def mock_config_delete(name):
        nonlocal deleted_remote
        deleted_remote = name
        return {}

    monkeypatch.setattr(rclone_client, "config_delete", mock_config_delete)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post("/api/remotes/disconnect", json={"remote": "gdrive"})
        assert res.status_code == 200
        assert deleted_remote == "gdrive"
