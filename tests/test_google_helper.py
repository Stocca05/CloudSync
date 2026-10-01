"""Actual helper HTTP server and real API, with a fake OAuth-producing Rclone executable."""

import json
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
from conftest import login
from sqlalchemy import select

from cloudsync.models import Remote


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_local_helper_confirmation_handoff_and_persisted_result(client, app, tmp_path):
    login(client)
    api_port, helper_port = free_port(), free_port()
    api_url = f"http://127.0.0.1:{api_port}"
    helper_url = f"http://127.0.0.1:{helper_port}"
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=api_port, lifespan="off", log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    fake = tmp_path / "rclone-fake"
    fake.write_text(
        '#!/bin/sh\necho "NOTICE http://127.0.0.1:53682/auth?state=fixture"\nsleep 1\necho "Paste --->"\necho \'{"access_token":"fixture-access","refresh_token":"fixture-refresh"}\'\necho "<---End paste"\n'
    )
    fake.chmod(0o700)
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"from scripts import google_helper as h; h.LOCAL={helper_url!r}; h.serve({api_url!r},{str(fake)!r})",
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        with httpx.Client(timeout=5) as browser:
            for _ in range(100):
                try:
                    if server.started and browser.get(helper_url).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("Helper startup failed")
            result = client.post("/api/google/start", json={"name": "Google via helper"}).json()
            ticket = result["ticket"]
            response = browser.get(
                helper_url + "/connect", params={"ticket": ticket, "site": "https://wrong.example.org"}
            )
            assert "indirizzo CloudSync è cambiato" in response.text
            response = browser.get(helper_url + "/connect", params={"ticket": ticket, "site": api_url})
            assert "admin" in response.text
            nonce = re.search('name="nonce" value="([^"]+)"', response.text)[1]
            assert (
                browser.post(
                    helper_url + "/authorize",
                    data={"nonce": nonce},
                    headers={"Origin": "https://evil.example.org"},
                ).status_code
                == 403
            )
            response = browser.post(
                helper_url + "/authorize", data={"nonce": nonce}, headers={"Origin": helper_url}
            )
            assert response.status_code == 303 and response.headers["location"].startswith(
                "http://127.0.0.1:53682/auth"
            )
            for _ in range(50):
                status = client.post("/api/google/progress", json={"ticket": ticket}).json()["status"]
                if status == "connected":
                    break
                time.sleep(0.1)
            assert status == "connected"
            with app.state.factory() as db:
                remote = db.scalar(select(Remote).where(Remote.name == "Google via helper"))
                token = json.loads(app.state.vault.decrypt(remote.encrypted_config)["token"])
                assert token["refresh_token"] == "fixture-refresh"
    finally:
        process.terminate()
        process.wait(timeout=5)
        server.should_exit = True
        thread.join(timeout=5)
