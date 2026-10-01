import pytest
from conftest import login, seed_remotes


@pytest.mark.parametrize("operation", ["mkdir", "delete"])
@pytest.mark.parametrize("path", ["", ".", "./", "folder/..", "../other", "/"])
def test_mutation_cannot_target_cloud_root_or_escape(client, app, tmp_path, operation, path):
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)
    response = client.post(
        "/api/jobs",
        json={"source_id": ids[0], "operation": operation, "source_path": path, "confirm_delete": True},
    )
    assert response.status_code == 400


def test_file_operation_contract_and_ownership(client, app, tmp_path):
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)
    spec = {"source_id": ids[0], "operation": "mkdir", "source_path": "new folder"}
    assert client.post("/api/jobs", json=spec).status_code == 201
    assert client.post("/api/jobs", json={**spec, "destination_id": ids[1]}).status_code == 400
    spec["operation"] = "delete"
    assert client.post("/api/jobs", json=spec).status_code == 400
    assert client.post("/api/jobs", json={**spec, "confirm_delete": True}).status_code == 201
    client.post("/api/auth/register", json={"username": "friend", "password": "x"})
    login(client, "friend", "x")
    assert client.post("/api/jobs", json={**spec, "confirm_delete": True}).status_code == 404


def test_retry_rejects_removed_remote(client, app, tmp_path):
    login(client)
    ids = seed_remotes(app, "admin", tmp_path)
    job = client.post("/api/jobs", json={"source_id": ids[0], "operation": "list"}).json()
    client.delete("/api/remotes/" + ids[0])
    assert client.post("/api/jobs/" + job["id"] + "/retry").status_code == 404
