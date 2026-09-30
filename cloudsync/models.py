import time
import uuid

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    admin: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    weight: Mapped[int] = mapped_column(Integer, default=1)
    max_jobs: Mapped[int] = mapped_column(Integer, default=2)
    bandwidth_bps: Mapped[int] = mapped_column(Integer, default=0)
    last_dispatch: Mapped[float] = mapped_column(Float, default=0)
    created: Mapped[float] = mapped_column(Float, default=time.time)


class LoginSession(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires: Mapped[float] = mapped_column(Float)


class Remote(Base):
    __tablename__ = "remotes"
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(32))
    encrypted_config: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created: Mapped[float] = mapped_column(Float, default=time.time)


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    slots: Mapped[int] = mapped_column(Integer, default=2)
    bandwidth_bps: Mapped[int] = mapped_column(Integer, default=52428800)
    last_seen: Mapped[float] = mapped_column(Float, default=0)
    version: Mapped[str] = mapped_column(String(100), default="")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("remotes.id"))
    destination_id: Mapped[str | None] = mapped_column(ForeignKey("remotes.id"), nullable=True)
    operation: Mapped[str] = mapped_column(String(16))
    source_path: Mapped[str] = mapped_column(Text, default="")
    destination_path: Mapped[str] = mapped_column(Text, default="")
    is_file: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[float] = mapped_column(Float, default=time.time, index=True)
    started: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished: Mapped[float | None] = mapped_column(Float, nullable=True)
    available_at: Mapped[float] = mapped_column(Float, default=time.time)
    node_id: Mapped[str | None] = mapped_column(ForeignKey("nodes.id"), nullable=True, index=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    template: Mapped[dict] = mapped_column(JSON)
    interval_seconds: Mapped[int] = mapped_column(Integer)
    next_run: Mapped[float] = mapped_column(Float)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ClusterConfig(Base):
    __tablename__ = "cluster_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    global_bps: Mapped[int] = mapped_column(Integer)
    max_active_jobs: Mapped[int] = mapped_column(Integer)


class AuthAnswer(Base):
    """Transient encrypted answer, acknowledged only by the current job lease holder."""

    __tablename__ = "auth_answers"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    answer_id: Mapped[str] = mapped_column(String(32))
    challenge_id: Mapped[str] = mapped_column(String(64))
    encrypted_value: Mapped[str] = mapped_column(Text)
