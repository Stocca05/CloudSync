"""Run against a disposable PostgreSQL database using TEST_DATABASE_URL.
Every test uses a unique schema and removes only that schema.
"""

import concurrent.futures
import os
import secrets
import time

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from cloudsync.api import create_app
from cloudsync.models import Job, Node, Remote, User
from cloudsync.scheduler import bandwidths
from cloudsync.security import digest
from cloudsync.settings import Settings


@pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL required for PostgreSQL concurrency test",
)
def test_concurrent_workers_no_double_claim_or_overbooking():
    base_url = os.environ["TEST_DATABASE_URL"]
    schema = "cloudsync_test_" + secrets.token_hex(8)
    admin_engine = create_engine(base_url)
    with admin_engine.begin() as db:
        db.execute(text(f"CREATE SCHEMA {schema}"))
    url = make_url(base_url).update_query_dict({"options": "-csearch_path=" + schema})
    app = create_app(
        Settings(
            database_url=url.render_as_string(hide_password=False),
            encryption_key=Fernet.generate_key().decode(),
            admin_password="test",
            cookie_secure=False,
        )
    )
    started = time.monotonic()
    try:
        with TestClient(app) as client:
            with app.state.factory.begin() as db:
                for index in range(40):
                    user = User(username=f"user-{index}", password_hash="not-used", weight=1, max_jobs=2)
                    db.add(user)
                    db.flush()
                    remote = Remote(
                        user_id=user.id,
                        name="Test cloud",
                        provider="drive",
                        encrypted_config=app.state.vault.encrypt({"type": "drive", "token": "{}"}),
                    )
                    db.add(remote)
                    db.flush()
                    for _ in range(3):
                        db.add(Job(user_id=user.id, source_id=remote.id, operation="list"))
                for index in range(16):
                    db.add(Node(id=f"node-{index}", token_hash=digest(f"node-token-{index}"), slots=4))

            def take(index):
                r = client.post(
                    "/internal/claim", headers={"Authorization": f"Bearer node-token-{index % 16}"}
                )
                assert r.status_code == 200, r.text
                return r.json()["job"]

            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                results = list(pool.map(take, range(96)))
            assigned = [job for job in results if job]
            assert len(assigned) == 64
            assert len({j["id"] for j in assigned}) == 64
            with app.state.factory() as db:
                per_user = db.execute(
                    select(Job.user_id, func.count()).where(Job.status == "running").group_by(Job.user_id)
                ).all()
                per_node = db.execute(
                    select(Job.node_id, func.count()).where(Job.status == "running").group_by(Job.node_id)
                ).all()
                assert max(count for _, count in per_user) <= 2
                assert max(count for _, count in per_node) <= 4
                assert len(per_user) == 40  # Every user gets a turn before some get their second.
                assert sum(bandwidths(db).values()) <= app.state.settings.global_bps
            print(
                f"\nPostgreSQL: 40 users, 16 workers, 120 queued jobs, 96 concurrent claim requests; 64 unique assignments in {time.monotonic() - started:.2f}s"
            )
    finally:
        with admin_engine.begin() as db:
            db.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin_engine.dispose()
