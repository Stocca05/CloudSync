import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    encryption_key: str = field(default_factory=lambda: os.environ.get("ENCRYPTION_KEY", ""))
    admin_user: str = field(default_factory=lambda: os.environ.get("ADMIN_USER", "admin"))
    admin_password: str = field(default_factory=lambda: os.environ.get("ADMIN_PASSWORD", ""))
    bootstrap_worker_token: str = field(default_factory=lambda: os.environ.get("WORKER_TOKEN", ""))
    cookie_secure: bool = field(default_factory=lambda: os.environ.get("COOKIE_SECURE", "true") == "true")
    allow_registration: bool = field(
        default_factory=lambda: os.environ.get("ALLOW_REGISTRATION", "true") == "true"
    )
    session_seconds: int = 7 * 86400
    lease_seconds: int = 90
    global_bps: int = field(default_factory=lambda: int(os.environ.get("GLOBAL_BPS", "52428800")))
    max_active_jobs: int = field(default_factory=lambda: int(os.environ.get("MAX_ACTIVE_JOBS", "64")))
    max_pending_per_user: int = 200
