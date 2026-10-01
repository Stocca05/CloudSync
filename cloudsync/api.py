import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .google_auth import install_google_routes
from .models import AuthAnswer, Base, ClusterConfig, Job, LoginSession, Node, Remote, Schedule, User
from .providers import PROVIDERS, safe_path, validate_config
from .scheduler import TERMINAL, bandwidths, claim, lock_scheduler, maintain
from .security import Vault, digest, hash_password, verify_password
from .settings import Settings


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RemoteInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str
    config: dict[str, str]


class JobInput(BaseModel):
    source_id: str
    destination_id: str | None = None
    source_path: str = ""
    destination_path: str = ""
    operation: Literal["list", "copy", "move", "mkdir", "delete"] = "copy"
    is_file: bool = False
    priority: int = Field(default=0, ge=0, le=2)
    confirm_move: bool = False
    confirm_delete: bool = False


class ScheduleInput(BaseModel):
    job: JobInput
    interval_seconds: int = Field(ge=300, le=31536000)


class UserPolicy(BaseModel):
    weight: int = Field(ge=1, le=10)
    max_jobs: int = Field(ge=1, le=16)
    bandwidth_bps: int = Field(ge=0, le=2000000000)
    enabled: bool = True


class NodeInput(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    slots: int = Field(default=2, ge=1, le=32)
    bandwidth_bps: int = Field(default=52428800, ge=65536, le=2000000000)


class JobPriority(BaseModel):
    priority: int = Field(ge=0, le=2)


class ClusterInput(BaseModel):
    global_bps: int = Field(ge=65536, le=2000000000)
    max_active_jobs: int = Field(ge=1, le=256)


class AuthReply(BaseModel):
    challenge_id: str = Field(max_length=64)
    answer: str = Field(max_length=2048)


class Heartbeat(BaseModel):
    lease_token: str
    stats: dict = Field(default_factory=dict)
    challenge: dict | None = None
    ack_answer: str | None = None


class Completion(Heartbeat):
    success: bool
    cancelled: bool = False
    error: str = Field(default="", max_length=2000)
    result: dict = Field(default_factory=dict)
    refreshed: dict = Field(default_factory=dict)


def create_app(settings=None):
    settings = settings or Settings()
    if not settings.database_url or not settings.encryption_key:
        raise RuntimeError("DATABASE_URL ed ENCRYPTION_KEY richiesti. Avvia con ./start")
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    factory = sessionmaker(engine, expire_on_commit=False)
    vault = Vault(settings.encryption_key)

    @asynccontextmanager
    async def lifespan(app):
        with engine.begin() as connection:
            if engine.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(73420124)"))
            Base.metadata.create_all(connection)
        with factory.begin() as db:
            lock_scheduler(db)
            if not db.scalar(select(User.id).limit(1)):
                if not settings.admin_password:
                    raise RuntimeError("ADMIN_PASSWORD richiesta per il primo avvio")
                db.add(
                    User(
                        username=settings.admin_user,
                        password_hash=hash_password(settings.admin_password),
                        admin=True,
                    )
                )
            if not db.get(ClusterConfig, 1):
                db.add(
                    ClusterConfig(
                        id=1, global_bps=settings.global_bps, max_active_jobs=settings.max_active_jobs
                    )
                )
            if settings.bootstrap_worker_token and not db.get(Node, "local"):
                db.add(Node(id="local", token_hash=digest(settings.bootstrap_worker_token)))
        yield
        engine.dispose()

    app = FastAPI(title="CloudSync", version="0.2.0", lifespan=lifespan)
    app.state.factory = factory
    app.state.vault = vault
    app.state.settings = settings

    @app.middleware("http")
    async def boundary(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.url.path.startswith("/internal/"):
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Origine non consentita"}, status_code=403)
            if request.headers.get("x-cloudsync-request") != "1":
                return JSONResponse({"detail": "Header applicativo richiesto"}, status_code=403)
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > 2_000_000):
            return JSONResponse({"detail": "Richiesta troppo grande"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.url.path.startswith(("/api/", "/internal/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    def get_db():
        with factory() as db:
            yield db

    def current_user(request: Request, db: Session = Depends(get_db)):
        session = db.get(LoginSession, digest(request.cookies.get("session", "")))
        user = db.get(User, session.user_id) if session and session.expires > time.time() else None
        if not user or not user.enabled:
            raise HTTPException(401, "Accedi per continuare")
        return user

    def admin(user=Depends(current_user)):
        if not user.admin:
            raise HTTPException(403, "Operazione riservata all'amministratore")
        return user

    install_google_routes(app, settings, vault, get_db, current_user)

    def worker(request: Request, db: Session = Depends(get_db)):
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        node = db.scalar(select(Node).where(Node.token_hash == digest(bearer)))
        if not node or not node.enabled:
            raise HTTPException(401, "Worker non autorizzato")
        return node

    def owned(db, model, key, user):
        obj = db.get(model, key)
        if not obj or obj.user_id != user.id or (model is Remote and not obj.enabled):
            raise HTTPException(404, "Risorsa non trovata")
        return obj

    def user_view(user):
        return {
            key: getattr(user, key)
            for key in ["id", "username", "admin", "enabled", "weight", "max_jobs", "bandwidth_bps"]
        }

    def job_view(job):
        return {
            key: getattr(job, key)
            for key in [
                "id",
                "source_id",
                "destination_id",
                "operation",
                "source_path",
                "destination_path",
                "is_file",
                "status",
                "priority",
                "created",
                "started",
                "finished",
                "node_id",
                "attempts",
                "stats",
                "result",
                "error",
                "cancel_requested",
            ]
        }

    def validate_job(db, spec, user):
        source_remote = owned(db, Remote, spec.source_id, user)
        if source_remote.provider == "iclouddrive" and not vault.decrypt(source_remote.encrypted_config).get(
            "trust_token"
        ):
            raise HTTPException(409, "Completa prima la connessione iCloud e il codice 2FA")
        if spec.operation in {"copy", "move"}:
            destination_remote = owned(db, Remote, spec.destination_id, user)
            if destination_remote.provider == "iclouddrive" and not vault.decrypt(
                destination_remote.encrypted_config
            ).get("trust_token"):
                raise HTTPException(409, "Completa prima la connessione iCloud di destinazione")
            if spec.source_id == spec.destination_id:
                raise HTTPException(400, "Scegli due collegamenti distinti")
        if spec.operation == "move" and not spec.confirm_move:
            raise HTTPException(400, "Conferma lo spostamento e la rimozione della sorgente")
        try:
            source = safe_path(spec.source_path)
            destination = safe_path(spec.destination_path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if spec.operation in {"mkdir", "delete"}:
            if not source:
                raise HTTPException(
                    400, "Indica una cartella o un file: la radice del cloud non può essere modificata"
                )
            if spec.destination_id or destination:
                raise HTTPException(400, "Questa operazione richiede solo il percorso sorgente")
            if spec.operation == "delete" and not spec.confirm_delete:
                raise HTTPException(400, "Conferma esplicitamente l’eliminazione del percorso selezionato")
            if spec.operation == "mkdir" and spec.is_file:
                raise HTTPException(400, "La creazione cartella non accetta un file")
        if spec.operation in {"copy", "move"} and spec.is_file and (not source or not destination):
            raise HTTPException(400, "Indica il nome del file sorgente e destinazione")
        return {
            **spec.model_dump(exclude={"confirm_move", "confirm_delete"}),
            "source_path": source,
            "destination_path": destination,
        }

    @app.get("/api/health")
    def health(db: Session = Depends(get_db)):
        db.execute(text("SELECT 1"))
        return {"status": "ok", "version": "0.2.0"}

    @app.get("/api/auth/options")
    def auth_options():
        return {"registration": settings.allow_registration}

    @app.post("/api/auth/register", status_code=201)
    def register(data: Credentials, db: Session = Depends(get_db)):
        if not settings.allow_registration:
            raise HTTPException(403, "Registrazione disabilitata")
        username = data.username.strip()
        if not username:
            raise HTTPException(400, "Inserisci un nome")
        user = User(username=username, password_hash=hash_password(data.password))
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Nome già utilizzato")
        return {"username": username}

    @app.post("/api/auth/login")
    def login(data: Credentials, response: Response, db: Session = Depends(get_db)):
        user = db.scalar(select(User).where(User.username == data.username.strip()))
        if not user or not user.enabled or not verify_password(data.password, user.password_hash):
            raise HTTPException(401, "Nome o password non corretti")
        token = secrets.token_urlsafe(32)
        db.execute(delete(LoginSession).where(LoginSession.expires < time.time()))
        db.add(
            LoginSession(
                token_hash=digest(token), user_id=user.id, expires=time.time() + settings.session_seconds
            )
        )
        db.commit()
        response.set_cookie(
            "session",
            token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            max_age=settings.session_seconds,
        )
        return user_view(user)

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, db: Session = Depends(get_db)):
        db.execute(
            delete(LoginSession).where(LoginSession.token_hash == digest(request.cookies.get("session", "")))
        )
        db.commit()
        response.delete_cookie("session")
        return {"ok": True}

    @app.get("/api/me")
    def me(user=Depends(current_user)):
        return user_view(user)

    @app.get("/api/providers")
    def providers(user=Depends(current_user)):
        return PROVIDERS

    @app.get("/api/remotes")
    def remotes(user=Depends(current_user), db: Session = Depends(get_db)):
        return [
            {"id": r.id, "name": r.name, "provider": r.provider}
            for r in db.scalars(
                select(Remote)
                .where(Remote.user_id == user.id, Remote.enabled.is_(True))
                .order_by(Remote.created)
            )
        ]

    @app.post("/api/remotes", status_code=201)
    def create_remote(data: RemoteInput, user=Depends(current_user), db: Session = Depends(get_db)):
        if db.scalar(select(func.count()).select_from(Remote).where(Remote.user_id == user.id)) >= 30:
            raise HTTPException(409, "Limite di 30 collegamenti raggiunto")
        try:
            config = validate_config(data.provider, data.config)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        remote = Remote(
            user_id=user.id, name=data.name, provider=data.provider, encrypted_config=vault.encrypt(config)
        )
        db.add(remote)
        db.commit()
        return {"id": remote.id, "name": remote.name, "provider": remote.provider}

    @app.patch("/api/remotes/{remote_id}")
    def update_apple(
        remote_id: str, data: RemoteInput, user=Depends(current_user), db: Session = Depends(get_db)
    ):
        lock_scheduler(db)
        remote = owned(db, Remote, remote_id, user)
        if remote.provider != "iclouddrive" or data.provider != "iclouddrive":
            raise HTTPException(400, "Aggiornamento riservato alle credenziali Apple")
        if db.scalar(
            select(Job.id)
            .where(
                (Job.source_id == remote_id) | (Job.destination_id == remote_id),
                Job.status.in_(["queued", "running"]),
            )
            .limit(1)
        ):
            raise HTTPException(409, "Attendi o annulla i lavori di questo collegamento prima di aggiornarlo")
        try:
            config = validate_config(data.provider, data.config)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        remote.encrypted_config = vault.encrypt(config)
        remote.revision += 1
        remote.name = data.name
        db.commit()
        return {"id": remote.id, "name": remote.name, "provider": remote.provider}

    @app.delete("/api/remotes/{remote_id}")
    def delete_remote(remote_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
        lock_scheduler(db)
        remote = owned(db, Remote, remote_id, user)
        remote.enabled = False
        remote.encrypted_config = vault.encrypt({})
        remote.revision += 1
        for job in db.scalars(
            select(Job).where(
                (Job.source_id == remote.id) | (Job.destination_id == remote.id),
                Job.status.in_(["queued", "running"]),
            )
        ):
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
                job.finished = time.time()
        for schedule in db.scalars(select(Schedule).where(Schedule.user_id == user.id)):
            if remote.id in {schedule.template.get("source_id"), schedule.template.get("destination_id")}:
                schedule.enabled = False
        db.commit()
        return {"ok": True}

    @app.post("/api/remotes/{remote_id}/connect", status_code=201)
    def connect_remote(remote_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
        lock_scheduler(db)
        remote = owned(db, Remote, remote_id, user)
        if remote.provider != "iclouddrive":
            raise HTTPException(400, "Questo collegamento non richiede il flusso Apple")
        previous = db.scalar(
            select(Job).where(
                Job.source_id == remote_id,
                Job.operation == "configure",
                Job.status.in_(["queued", "running"]),
            )
        )
        if previous:
            return job_view(previous)
        busy = db.scalar(
            select(Job.id)
            .where((Job.source_id == remote_id) | (Job.destination_id == remote_id), Job.status == "running")
            .limit(1)
        )
        if busy:
            raise HTTPException(
                409, "Attendi la fine dei trasferimenti su questo collegamento prima di ricollegarlo"
            )
        job = Job(user_id=user.id, source_id=remote_id, operation="configure", max_attempts=1, priority=2)
        db.add(job)
        db.commit()
        return job_view(job)

    @app.post("/api/jobs/{job_id}/answer")
    def answer_challenge(
        job_id: str, data: AuthReply, user=Depends(current_user), db: Session = Depends(get_db)
    ):
        lock_scheduler(db)
        job = owned(db, Job, job_id, user)
        challenge = job.result.get("challenge", {})
        if (
            job.operation != "configure"
            or job.status != "running"
            or challenge.get("id") != data.challenge_id
        ):
            raise HTTPException(409, "Richiesta scaduta. Attendi il nuovo passaggio o riavvia la connessione")
        if db.get(AuthAnswer, job_id):
            raise HTTPException(409, "Risposta già inviata: attendi la verifica")
        db.add(
            AuthAnswer(
                job_id=job_id,
                answer_id=secrets.token_hex(16),
                challenge_id=data.challenge_id,
                encrypted_value=vault.encrypt({"answer": data.answer}),
            )
        )
        db.commit()
        return {"ok": True}

    @app.post("/api/jobs", status_code=201)
    def create_job(data: JobInput, user=Depends(current_user), db: Session = Depends(get_db)):
        lock_scheduler(db)
        values = validate_job(db, data, user)
        pending = db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.user_id == user.id, Job.status.in_(["queued", "running"]))
        )
        if pending >= settings.max_pending_per_user:
            raise HTTPException(409, "La tua coda è piena")
        job = Job(user_id=user.id, **values)
        db.add(job)
        db.commit()
        return job_view(job)

    @app.get("/api/jobs")
    def jobs(user=Depends(current_user), db: Session = Depends(get_db)):
        lock_scheduler(db)
        maintain(db)
        db.commit()
        return [
            job_view(j)
            for j in db.scalars(
                select(Job)
                .where(Job.user_id == user.id, Job.operation != "list")
                .order_by(Job.created.desc())
                .limit(200)
            )
        ]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
        return job_view(owned(db, Job, job_id, user))

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
        lock_scheduler(db)
        job = owned(db, Job, job_id, user)
        if job.status not in TERMINAL:
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
                job.finished = time.time()
        db.commit()
        return job_view(job)

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
        lock_scheduler(db)
        job = owned(db, Job, job_id, user)
        if job.status not in {"failed", "cancelled"}:
            raise HTTPException(409, "Il lavoro non può essere riavviato")
        for remote_id in {job.source_id, job.destination_id} - {None}:
            owned(db, Remote, remote_id, user)
        job.status = "queued"
        job.attempts = 0
        job.cancel_requested = False
        job.available_at = time.time()
        job.finished = None
        job.result = {}
        db.commit()
        return job_view(job)

    @app.get("/api/schedules")
    def schedules(user=Depends(current_user), db: Session = Depends(get_db)):
        return [
            {
                "id": s.id,
                "template": s.template,
                "interval_seconds": s.interval_seconds,
                "next_run": s.next_run,
                "enabled": s.enabled,
            }
            for s in db.scalars(select(Schedule).where(Schedule.user_id == user.id))
        ]

    @app.post("/api/schedules", status_code=201)
    def create_schedule(data: ScheduleInput, user=Depends(current_user), db: Session = Depends(get_db)):
        lock_scheduler(db)
        values = validate_job(db, data.job, user)
        if data.job.operation != "copy":
            raise HTTPException(400, "Le pianificazioni supportano la copia")
        if db.scalar(select(func.count()).select_from(Schedule).where(Schedule.user_id == user.id)) >= 20:
            raise HTTPException(409, "Limite di 20 pianificazioni raggiunto")
        schedule = Schedule(
            user_id=user.id, template=values, interval_seconds=data.interval_seconds, next_run=time.time()
        )
        db.add(schedule)
        db.commit()
        return {"id": schedule.id}

    @app.delete("/api/schedules/{schedule_id}")
    def delete_schedule(schedule_id: str, user=Depends(current_user), db: Session = Depends(get_db)):
        lock_scheduler(db)
        db.delete(owned(db, Schedule, schedule_id, user))
        db.commit()
        return {"ok": True}

    @app.get("/api/admin/jobs")
    def admin_jobs(offset: int = 0, status: str = "", user=Depends(admin), db: Session = Depends(get_db)):
        query = select(Job, User.username).join(User, Job.user_id == User.id)
        if status:
            query = query.where(Job.status == status)
        rows = db.execute(query.order_by(Job.created.desc()).offset(max(0, offset)).limit(100)).all()
        rates = bandwidths(db)
        items = []
        for job, username in rows:
            view = job_view(job)
            # Administrative monitoring does not expose authentication challenges.
            view["result"] = {}
            view.update(username=username, assigned_bps=rates.get(job.id, 0))
            items.append(view)
        return {"items": items, "offset": max(0, offset), "has_more": len(items) == 100}

    @app.patch("/api/admin/jobs/{job_id}")
    def admin_priority(job_id: str, data: JobPriority, user=Depends(admin), db: Session = Depends(get_db)):
        lock_scheduler(db)
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Lavoro non trovato")
        if job.status in TERMINAL:
            raise HTTPException(409, "Il lavoro è già terminato")
        job.priority = data.priority
        db.commit()
        return {"ok": True}

    @app.post("/api/admin/jobs/{job_id}/cancel")
    def admin_cancel(job_id: str, user=Depends(admin), db: Session = Depends(get_db)):
        lock_scheduler(db)
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Lavoro non trovato")
        if job.status not in TERMINAL:
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
                job.finished = time.time()
        db.commit()
        return {"ok": True}

    @app.get("/api/admin/cluster")
    def cluster(user=Depends(admin), db: Session = Depends(get_db)):
        cfg = db.get(ClusterConfig, 1)
        return {
            "global_bps": cfg.global_bps,
            "max_active_jobs": cfg.max_active_jobs,
            "nodes": [
                {
                    k: getattr(n, k)
                    for k in ["id", "enabled", "slots", "bandwidth_bps", "last_seen", "version"]
                }
                for n in db.scalars(select(Node))
            ],
            "users": [user_view(u) for u in db.scalars(select(User).order_by(User.created))],
            "rates": bandwidths(db),
        }

    @app.patch("/api/admin/cluster")
    def set_cluster(data: ClusterInput, user=Depends(admin), db: Session = Depends(get_db)):
        lock_scheduler(db)
        cfg = db.get(ClusterConfig, 1)
        for key, value in data.model_dump().items():
            setattr(cfg, key, value)
        db.commit()
        return {"ok": True}

    @app.patch("/api/admin/users/{user_id}")
    def set_user(user_id: str, data: UserPolicy, user=Depends(admin), db: Session = Depends(get_db)):
        lock_scheduler(db)
        target = db.get(User, user_id)
        if not target:
            raise HTTPException(404)
        if target.id == user.id and not data.enabled:
            raise HTTPException(400, "Non puoi disattivare il tuo account")
        for key, value in data.model_dump().items():
            setattr(target, key, value)
        if not data.enabled:
            db.execute(delete(LoginSession).where(LoginSession.user_id == target.id))
            for job in db.scalars(
                select(Job).where(Job.user_id == target.id, Job.status.in_(["queued", "running"]))
            ):
                job.cancel_requested = True
                if job.status == "queued":
                    job.status = "cancelled"
                    job.finished = time.time()
        db.commit()
        return user_view(target)

    @app.post("/api/admin/nodes", status_code=201)
    def create_node(data: NodeInput, user=Depends(admin), db: Session = Depends(get_db)):
        if db.get(Node, data.name):
            raise HTTPException(409, "Nome nodo già utilizzato")
        token = secrets.token_urlsafe(48)
        db.add(
            Node(id=data.name, token_hash=digest(token), slots=data.slots, bandwidth_bps=data.bandwidth_bps)
        )
        db.commit()
        return {"id": data.name, "token": token}

    @app.patch("/api/admin/nodes/{node_id}")
    def update_node(node_id: str, data: NodeInput, user=Depends(admin), db: Session = Depends(get_db)):
        lock_scheduler(db)
        node = db.get(Node, node_id)
        if not node:
            raise HTTPException(404)
        node.slots, node.bandwidth_bps = data.slots, data.bandwidth_bps
        db.commit()
        return {"ok": True}

    @app.delete("/api/admin/nodes/{node_id}")
    def revoke_node(node_id: str, user=Depends(admin), db: Session = Depends(get_db)):
        lock_scheduler(db)
        node = db.get(Node, node_id)
        if not node:
            raise HTTPException(404)
        node.enabled = False
        db.commit()
        return {"ok": True}

    @app.post("/internal/claim")
    def worker_claim(node=Depends(worker), db: Session = Depends(get_db)):
        node.last_seen = time.time()
        job = claim(db, node, settings.lease_seconds)
        if not job:
            db.commit()
            return {"job": None}
        payload = job_view(job)
        payload["lease_token"] = job.lease_token
        payload["bandwidth_bps"] = bandwidths(db)[job.id]
        payload["remotes"] = {}
        for remote_id in {job.source_id, job.destination_id} - {None}:
            remote = db.get(Remote, remote_id)
            payload["remotes"][remote_id] = {
                "config": vault.decrypt(remote.encrypted_config),
                "revision": remote.revision,
            }
        db.commit()
        return {"job": payload}

    def leased(db, job_id, token, node):
        job = db.get(Job, job_id)
        if (
            not job
            or job.status != "running"
            or job.node_id != node.id
            or job.lease_token != token
            or job.lease_until < time.time()
        ):
            raise HTTPException(409, "Lease scaduto: interrompi immediatamente il processo")
        return job

    @app.post("/internal/jobs/{job_id}/heartbeat")
    def heartbeat(job_id: str, data: Heartbeat, node=Depends(worker), db: Session = Depends(get_db)):
        lock_scheduler(db)
        job = leased(db, job_id, data.lease_token, node)
        job.lease_until = time.time() + settings.lease_seconds
        job.stats = {
            k: data.stats[k]
            for k in ["bytes", "totalBytes", "speed", "eta", "transfers", "checks", "errors"]
            if isinstance(data.stats.get(k), (int, float))
        }
        node.last_seen = time.time()
        answer = db.get(AuthAnswer, job.id)
        if answer and data.ack_answer == answer.answer_id:
            db.delete(answer)
            db.flush()
            answer = None
        if job.operation == "configure" and data.challenge is not None:
            challenge = data.challenge
            if challenge:
                # Only a small UI schema is persisted; no raw config state or provider credentials.
                safe = {key: str(challenge.get(key, ""))[:3000] for key in ["id", "name", "help"]}
                safe["secret"] = bool(challenge.get("secret", True))
                safe["examples"] = [
                    {"value": str(e.get("value", ""))[:100], "label": str(e.get("label", ""))[:200]}
                    for e in challenge.get("examples", [])[:10]
                    if isinstance(e, dict)
                ]
                job.result = {"challenge": safe}
            else:
                job.result = {}
        db.flush()
        result = {"cancel": job.cancel_requested, "bandwidth_bps": bandwidths(db)[job.id]}
        if answer and job.operation == "configure":
            result["answer"] = {
                "id": answer.answer_id,
                "challenge_id": answer.challenge_id,
                **vault.decrypt(answer.encrypted_value),
            }
        db.commit()
        return result

    @app.post("/internal/jobs/{job_id}/complete")
    def complete(job_id: str, data: Completion, node=Depends(worker), db: Session = Depends(get_db)):
        lock_scheduler(db)
        job = leased(db, job_id, data.lease_token, node)
        job.stats = {
            k: data.stats[k]
            for k in ["bytes", "totalBytes", "speed", "eta", "transfers", "checks", "errors"]
            if isinstance(data.stats.get(k), (int, float))
        }
        db.execute(delete(AuthAnswer).where(AuthAnswer.job_id == job.id))
        job.result = data.result
        job.error = data.error
        job.status = "cancelled" if data.cancelled else ("completed" if data.success else "failed")
        job.finished = time.time()
        job.lease_token = None
        job.lease_until = None
        if job.status == "failed" and not job.cancel_requested and job.attempts < job.max_attempts:
            job.status = "queued"
            job.available_at = time.time() + 15 * job.attempts
            job.finished = None
        for remote_id, update in data.refreshed.items():
            if remote_id not in {job.source_id, job.destination_id}:
                continue
            remote = db.get(Remote, remote_id)
            if not remote.enabled or update.get("revision") != remote.revision:
                continue
            config = vault.decrypt(remote.encrypted_config)
            allowed = (
                ["token"]
                if remote.provider == "drive"
                else (["cookies", "trust_token", "client_id"] if remote.provider == "iclouddrive" else [])
            )
            changed = False
            for key in allowed:
                value = update.get(key)
                if isinstance(value, str) and len(value) < 64000 and value != config.get(key):
                    config[key] = value
                    changed = True
            if changed:
                remote.encrypted_config = vault.encrypt(config)
                remote.revision += 1
        db.commit()
        return {"ok": True}

    frontend = Path(__file__).parent.parent / "frontend"
    if frontend.exists():
        app.mount("/assets", StaticFiles(directory=frontend), name="assets")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(frontend / "index.html")

    return app
