"""All dispatch transitions run inside one short PostgreSQL transaction advisory lock.
No leader process required: multiple APIs/workers can safely request dispatch.
"""

import secrets
import time
from collections import Counter

from sqlalchemy import delete, select, text

from .models import AuthAnswer, ClusterConfig, Job, Node, Remote, Schedule, User

ACTIVE = ("running",)
TERMINAL = ("completed", "failed", "cancelled")


def lock_scheduler(db):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(73420125)"))


def maintain(db, now=None):
    now = now or time.time()
    for job in db.scalars(select(Job).where(Job.status == "running", Job.lease_until < now)):
        job.status = (
            "cancelled"
            if job.cancel_requested
            else ("queued" if job.attempts < job.max_attempts else "failed")
        )
        db.execute(delete(AuthAnswer).where(AuthAnswer.job_id == job.id))
        job.error = "Worker non raggiungibile: esecuzione interrotta"
        job.lease_token = None
        job.lease_until = None
        job.node_id = None
        job.available_at = now + 5
        if job.status != "queued":
            job.finished = now
    for schedule in db.scalars(select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_run <= now)):
        previous = db.get(Job, schedule.last_job_id) if schedule.last_job_id else None
        user = db.get(User, schedule.user_id)
        if not user.enabled or (previous and previous.status not in TERMINAL):
            continue
        source = db.get(Remote, schedule.template["source_id"])
        dest = db.get(Remote, schedule.template.get("destination_id"))
        if not source or not dest or not source.enabled or not dest.enabled:
            schedule.enabled = False
            continue
        job = Job(user_id=schedule.user_id, **schedule.template)
        db.add(job)
        db.flush()
        schedule.last_job_id = job.id
        schedule.next_run = now + schedule.interval_seconds
    db.flush()


def bandwidths(db) -> dict[str, int]:
    jobs = list(db.scalars(select(Job).where(Job.status == "running")))
    if not jobs:
        return {}
    cfg = db.get(ClusterConfig, 1)
    counts = Counter(j.user_id for j in jobs)
    nodes = Counter(j.node_id for j in jobs)
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(counts)))}
    total_weight = sum(u.weight for u in users.values())
    result = {}
    for job in jobs:
        user = users[job.user_id]
        user_share = cfg.global_bps * user.weight // total_weight
        if user.bandwidth_bps:
            user_share = min(user_share, user.bandwidth_bps)
        node = db.get(Node, job.node_id)
        result[job.id] = max(1, min(user_share // counts[user.id], node.bandwidth_bps // nodes[node.id]))
    return result


def claim(db, node, lease_seconds):
    lock_scheduler(db)
    now = time.time()
    maintain(db, now)
    active = list(db.scalars(select(Job).where(Job.status == "running")))
    cfg = db.get(ClusterConfig, 1)
    if not node.enabled or len(active) >= cfg.max_active_jobs:
        return None
    if sum(j.node_id == node.id for j in active) >= node.slots:
        return None
    counts = Counter(j.user_id for j in active)
    saturated = [user_id for user_id, count in counts.items() if count >= db.get(User, user_id).max_jobs]
    query = (
        select(Job)
        .join(User, Job.user_id == User.id)
        .where(Job.status == "queued", Job.available_at <= now, User.enabled.is_(True))
    )
    if saturated:
        query = query.where(Job.user_id.not_in(saturated))
    # Least recently served user prevents starvation. Priority orders only that user's queue.
    job = db.scalar(query.order_by(User.last_dispatch, Job.priority.desc(), Job.created).limit(1))
    if not job:
        return None
    db.execute(delete(AuthAnswer).where(AuthAnswer.job_id == job.id))
    job.result = {}
    job.status = "running"
    job.node_id = node.id
    job.lease_token = secrets.token_hex(24)
    job.lease_until = now + lease_seconds
    job.started = now
    job.finished = None
    job.attempts += 1
    job.error = ""
    job.stats = {}
    db.get(User, job.user_id).last_dispatch = now
    db.flush()
    return job
