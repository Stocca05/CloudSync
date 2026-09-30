"""Root-only deployment check: two real worker processes, owned fixtures, cleanup.
Run on the native target with /opt/cloudsync/.venv/bin/python scripts/deployment_smoke.py.
No cloud credentials are printed or required.
"""

import hashlib
import json
import os
import pwd
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/cloudsync")
import httpx
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from cloudsync.models import Job, LoginSession, Node, Remote, User
from cloudsync.security import Vault

settings = dict(
    line.split("=", 1) for line in Path("/etc/cloudsync/server.env").read_text().splitlines() if "=" in line
)
engine = create_engine(settings["DATABASE_URL"])
vault = Vault(settings["ENCRYPTION_KEY"])
identifier = "verify-" + secrets.token_hex(5)
root = Path("/var/lib/cloudsync") / identifier
root.mkdir(mode=0o755)
account = pwd.getpwnam("cloudsync")
users = []
remotes = []
job_ids = []
extra = None
original_slots = None
headers = {"X-CloudSync-Request": "1"}
started = time.monotonic()
try:
    with httpx.Client(base_url="http://127.0.0.1:8080", headers=headers, timeout=10) as admin:
        admin.post(
            "/api/auth/login",
            json={"username": settings["ADMIN_USER"], "password": settings["ADMIN_PASSWORD"]},
        ).raise_for_status()
        cluster = admin.get("/api/admin/cluster").json()
        local = next(node for node in cluster["nodes"] if node["id"] == "local")
        original_slots = local["slots"]
        admin.patch(
            "/api/admin/nodes/local",
            json={"name": "local", "slots": 1, "bandwidth_bps": local["bandwidth_bps"]},
        ).raise_for_status()
        node_result = admin.post(
            "/api/admin/nodes", json={"name": identifier, "slots": 1, "bandwidth_bps": 1048576}
        )
        node_result.raise_for_status()
        worker_token = node_result.json()["token"]
        for index in range(2):
            username = identifier + "-" + str(index)
            password = secrets.token_hex(12)
            with httpx.Client(base_url="http://127.0.0.1:8080", headers=headers) as user_client:
                user_client.post(
                    "/api/auth/register", json={"username": username, "password": password}
                ).raise_for_status()
                response = user_client.post(
                    "/api/auth/login", json={"username": username, "password": password}
                )
                response.raise_for_status()
                user_id = response.json()["id"]
                users.append(user_id)
                admin.patch(
                    "/api/admin/users/" + user_id, json={"weight": 1, "max_jobs": 2, "bandwidth_bps": 524288}
                ).raise_for_status()
                source, dest = root / f"source-{index}", root / f"dest-{index}"
                source.mkdir()
                dest.mkdir()
                payload = secrets.token_bytes(2 * 1024 * 1024)
                (source / "payload.bin").write_bytes(payload)
                for directory in [source, dest]:
                    os.chown(directory, account.pw_uid, account.pw_gid)
                with Session(engine) as db:
                    ids = []
                    for path in [source, dest]:
                        remote = Remote(
                            user_id=user_id,
                            name="Verification fixture",
                            provider="local",
                            encrypted_config=vault.encrypt({"type": "alias", "remote": str(path)}),
                        )
                        db.add(remote)
                        db.flush()
                        remotes.append(remote.id)
                        ids.append(remote.id)
                    db.commit()
                for repeat in range(2):
                    job = user_client.post(
                        "/api/jobs",
                        json={
                            "source_id": ids[0],
                            "destination_id": ids[1],
                            "destination_path": f"copy-{repeat}",
                        },
                    )
                    job.raise_for_status()
                    job_ids.append(job.json()["id"])
        log_file = (root / "worker.log").open("w")
        extra = subprocess.Popen(
            ["/usr/sbin/runuser", "-u", "cloudsync", "--", "/opt/cloudsync/.venv/bin/python", "-m", "cloudsync.worker"],
            cwd="/opt/cloudsync",
            env={
                "PATH": "/opt/cloudsync/.venv/bin:/usr/local/bin:/usr/bin:/bin",
                "CONTROL_URL": "http://127.0.0.1:8080",
                "WORKER_TOKEN": worker_token,
                "WORKER_SLOTS": "1",
            },
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
        observed = set()
        recovery_job = None
        interrupted = False
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            with Session(engine) as db:
                jobs = [db.get(Job, key) for key in job_ids]
                observed.update(job.node_id for job in jobs if job.node_id)
                candidate = next(
                    (job for job in jobs if job.node_id == identifier and job.status == "running"), None
                )
                if candidate and not interrupted:
                    recovery_job = candidate.id
                    # Terminate the worker supervisor; it requeues the incomplete job.
                    os.killpg(extra.pid, signal.SIGTERM)
                    extra.wait(timeout=15)
                    interrupted = True
                if all(job.status == "completed" for job in jobs):
                    assert all(job.attempts >= 1 for job in jobs)
                    assert next(job for job in jobs if job.id == recovery_job).attempts >= 2
                    break
            time.sleep(2)
        else:
            raise AssertionError("Timed out waiting for real transfers")
        assert observed == {"local", identifier}, observed
        for index in range(2):
            source_hash = hashlib.sha256((root / f"source-{index}" / "payload.bin").read_bytes()).digest()
            for repeat in range(2):
                destination_hash = hashlib.sha256(
                    (root / f"dest-{index}" / f"copy-{repeat}" / "payload.bin").read_bytes()
                ).digest()
                assert source_hash == destination_hash
        print(
            json.dumps(
                {
                    "result": "passed",
                    "users": 2,
                    "real_worker_processes": 2,
                    "copies_verified_sha256": 4,
                    "worker_interruption_recovered": interrupted,
                    "elapsed_seconds": round(time.monotonic() - started, 1),
                }
            ),
            flush=True,
        )
        admin.patch(
            "/api/admin/nodes/local",
            json={"name": "local", "slots": original_slots, "bandwidth_bps": local["bandwidth_bps"]},
        ).raise_for_status()
finally:
    if extra and extra.poll() is None:
        os.killpg(extra.pid, signal.SIGTERM)
        extra.wait(timeout=15)
    with Session(engine) as db:
        # Only fixtures created by this invocation are removed.
        if job_ids:
            active = [db.get(Job, key) for key in job_ids]
            if any(job and job.status == "running" for job in active):
                for job in active:
                    if job and job.status in {"queued", "running"}:
                        job.cancel_requested = True
                        if job.status == "queued":
                            job.status = "cancelled"
                db.commit()
                time.sleep(12)
        db.execute(delete(LoginSession).where(LoginSession.user_id.in_(users)))
        db.execute(delete(Job).where(Job.id.in_(job_ids)))
        db.execute(delete(Remote).where(Remote.id.in_(remotes)))
        db.execute(delete(User).where(User.id.in_(users)))
        db.execute(delete(Node).where(Node.id == identifier))
        node = db.get(Node, "local")
        if original_slots is not None:
            node.slots = original_slots
        db.commit()
    shutil.rmtree(root)
    engine.dispose()
