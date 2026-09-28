"""CloudSync Backend Application.

FastAPI REST API and real-time WebSocket / SSE service orchestrating
transfers between Google Drive and iCloud Drive via Rclone Remote Control.
"""

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.demo import generate_demo_rclone_conf, setup_demo_environment
from backend.history import HistoryEntry, HistoryItem, history_manager
from backend.models import (
    BandwidthLimitRequest,
    DisconnectRemoteRequest,
    FSItem,
    FSListRequest,
    FSListResponse,
    GoogleAuthResponse,
    GoogleOAuthExchangeRequest,
    GoogleOAuthTokenAuthRequest,
    GoogleServiceAccountAuthRequest,
    HealthResponse,
    JobInfo,
    JobStatusResponse,
    MkdirRequest,
    ObscurePasswordRequest,
    ObscurePasswordResponse,
    RemoteConfigRequest,
    RemoteDetail,
    RemotesStatusResponse,
    RemoteTestRequest,
    RemoteTestResponse,
    TransferMoveRequest,
    TransferMoveResponse,
    TransferringItem,
    TunnelStatusResponse,
)
from backend.rclone_client import RcloneAPIError, rclone_client

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cloudsync.main")

# In-memory job registry for user tracking
# job_id -> JobInfo
JOB_REGISTRY: dict[int, JobInfo] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for startup and shutdown routines."""
    logger.info("Initializing CloudSync backend...")
    await rclone_client.start()

    # Verify Rclone connectivity
    alive = await rclone_client.is_alive()
    if alive:
        version_data = await rclone_client.get_version()
        logger.info(
            "Connected to Rclone daemon successfully. Version: %s",
            version_data.get("version", "unknown"),
        )
    else:
        logger.warning(
            "Could not connect to Rclone daemon at %s. Please check if daemon is running.",
            settings.rclone_rc_url,
        )

    yield

    logger.info("Shutting down CloudSync backend...")
    await rclone_client.close()


app = FastAPI(
    title="CloudSync API",
    description="Secure, private cloud transfer orchestrator between Google Drive and iCloud Drive via Rclone.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RcloneAPIError)
async def rclone_api_exception_handler(request: Request, exc: RcloneAPIError):
    """Translate internal Rclone client errors into structured JSON responses."""
    return JSONResponse(
        status_code=exc.status_code if 400 <= exc.status_code <= 599 else 500,
        content={"error": exc.message, "details": exc.details},
    )


# ---------------------------------------------------------------------------
# Health & Status Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health", response_model=HealthResponse)
async def get_health():
    """Healthcheck endpoint for Docker container and orchestrator."""
    alive = await rclone_client.is_alive()
    version = None
    if alive:
        try:
            v_data = await rclone_client.get_version()
            version = v_data.get("version")
        except (RcloneAPIError, httpx.HTTPError) as exc:
            logger.debug("Could not retrieve Rclone version: %s", exc)

    config_exists = os.path.exists(settings.rclone_config_path)

    return HealthResponse(
        status="healthy" if alive else "degraded",
        rclone_connected=alive,
        rclone_version=version,
        config_file_found=config_exists,
    )


@app.get("/api/remotes/status", response_model=RemotesStatusResponse)
async def get_remotes_status():
    """Verify that 'gdrive' and 'icloud' remotes are configured in rclone.conf and test connectivity."""
    try:
        remotes_list = await rclone_client.list_remotes()
    except (RcloneAPIError, httpx.HTTPError) as e:
        logger.error("Failed to list remotes: %s", e)
        return RemotesStatusResponse(
            gdrive_configured=False,
            icloud_configured=False,
            all_remotes=[],
            error=str(e),
        )

    clean_remotes = [r.rstrip(":") for r in remotes_list]
    gdrive_name = settings.gdrive_remote
    icloud_name = settings.icloud_remote

    gdrive_exists = gdrive_name in clean_remotes
    icloud_exists = icloud_name in clean_remotes

    gdrive_detail = None
    if gdrive_exists:
        about = await rclone_client.get_about(gdrive_name)
        gdrive_detail = RemoteDetail(
            name=gdrive_name,
            connected="error" not in about,
            total_bytes=about.get("total"),
            used_bytes=about.get("used"),
            free_bytes=about.get("free"),
            error=about.get("error"),
        )

    icloud_detail = None
    if icloud_exists:
        about = await rclone_client.get_about(icloud_name)
        icloud_detail = RemoteDetail(
            name=icloud_name,
            connected="error" not in about,
            total_bytes=about.get("total"),
            used_bytes=about.get("used"),
            free_bytes=about.get("free"),
            error=about.get("error"),
        )

    return RemotesStatusResponse(
        gdrive_configured=gdrive_exists,
        icloud_configured=icloud_exists,
        all_remotes=clean_remotes,
        gdrive=gdrive_detail,
        icloud=icloud_detail,
    )


# ---------------------------------------------------------------------------
# File Explorer Endpoints
# ---------------------------------------------------------------------------


@app.post("/api/fs/list", response_model=FSListResponse)
async def list_files(req: FSListRequest):
    """List directory contents for a specific remote and path."""
    raw_items = await rclone_client.list_directory(req.remote, req.path)

    items: list[FSItem] = []
    for item in raw_items:
        mime = item.get("MimeType", "")
        is_dir = item.get("IsDir", False)
        # Identify Google Docs formats that will be exported automatically
        is_gdoc = bool(mime and "application/vnd.google-apps" in mime)

        items.append(
            FSItem(
                name=item.get("Name", ""),
                path=item.get("Path", ""),
                size=item.get("Size", 0) if not is_dir else 0,
                is_dir=is_dir,
                mime_type=mime,
                mod_time=item.get("ModTime"),
                is_gdoc=is_gdoc,
            )
        )

    # Sort directories first, then alphabetically by name
    items.sort(key=lambda x: (not x.is_dir, x.name.lower()))

    return FSListResponse(
        remote=req.remote,
        path=req.path,
        items=items,
        total_items=len(items),
    )


@app.post("/api/fs/mkdir")
async def create_directory(req: MkdirRequest):
    """Create a new folder in the specified remote."""
    await rclone_client.mkdir(req.remote, req.path)
    return {
        "status": "success",
        "message": f"Directory '{req.path}' created on '{req.remote}'.",
    }


# ---------------------------------------------------------------------------
# Transfer & Move Operations
# ---------------------------------------------------------------------------


@app.post("/api/transfer/move", response_model=TransferMoveResponse)
async def trigger_move(req: TransferMoveRequest):
    """Trigger background asynchronous transfer jobs (Move or Copy) for selected items.

    When delete_source=True:
      Files are moved using operations/movefile, folders via sync/move (source deleted).
    When delete_source=False:
      Files are copied using operations/copyfile, folders via sync/copy (source preserved).
    """
    if not req.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files or folders selected for transfer.",
        )

    batch_id = str(uuid.uuid4())[:8]
    created_jobs: list[JobInfo] = []
    dst_base = req.dst_path.strip("/")
    action_name = "move" if req.delete_source else "copy"

    for item in req.items:
        item_path = item.path.lstrip("/")
        item_name = os.path.basename(item_path)

        # Compute destination path inside the selected destination folder
        if dst_base:
            target_path = f"{dst_base}/{item_name}"
        else:
            target_path = item_name

        try:
            if req.delete_source:
                # Mode MOVE: Delete source upon successful transfer
                if item.is_dir:
                    job_id = await rclone_client.move_directory(
                        src_remote=req.src_remote,
                        src_path=item_path,
                        dst_remote=req.dst_remote,
                        dst_path=target_path,
                        delete_empty_src_dirs=req.delete_empty_src_dirs,
                        export_formats=settings.drive_export_formats
                        if req.export_docs
                        else None,
                        dry_run=req.dry_run,
                    )
                else:
                    job_id = await rclone_client.move_file(
                        src_remote=req.src_remote,
                        src_path=item_path,
                        dst_remote=req.dst_remote,
                        dst_path=target_path,
                        dry_run=req.dry_run,
                    )
            else:
                # Mode COPY: Keep source files intact
                if item.is_dir:
                    job_id = await rclone_client.copy_directory(
                        src_remote=req.src_remote,
                        src_path=item_path,
                        dst_remote=req.dst_remote,
                        dst_path=target_path,
                        export_formats=settings.drive_export_formats
                        if req.export_docs
                        else None,
                        dry_run=req.dry_run,
                    )
                else:
                    job_id = await rclone_client.copy_file(
                        src_remote=req.src_remote,
                        src_path=item_path,
                        dst_remote=req.dst_remote,
                        dst_path=target_path,
                        dry_run=req.dry_run,
                    )

            job_info = JobInfo(
                job_id=job_id,
                item_path=item_path,
                is_dir=item.is_dir,
                dst_path=target_path,
                status="running",
            )
            JOB_REGISTRY[job_id] = job_info
            created_jobs.append(job_info)
            logger.info(
                "Dispatched %s job %d (dry_run=%s, delete_source=%s) for '%s' -> '%s'",
                action_name,
                job_id,
                req.dry_run,
                req.delete_source,
                item_path,
                target_path,
            )

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to dispatch %s for '%s': %s", action_name, item_path, exc
            )
            # Create a failed job representation for visibility
            failed_job = JobInfo(
                job_id=-1,
                item_path=item_path,
                is_dir=item.is_dir,
                dst_path=target_path,
                status="failed",
                error=str(exc),
            )
            created_jobs.append(failed_job)

    # Persist in history
    history_entry = HistoryEntry(
        id=batch_id,
        action=action_name,
        delete_source=req.delete_source,
        src_remote=req.src_remote,
        dst_remote=req.dst_remote,
        dst_path=req.dst_path,
        total_items=len(req.items),
        status="running",
        dry_run=req.dry_run,
        items=[
            HistoryItem(
                path=j.item_path,
                is_dir=j.is_dir,
                dst_path=j.dst_path,
                status="running" if j.job_id != -1 else "failed",
                error=j.error,
            )
            for j in created_jobs
        ],
    )
    history_manager.save_entry(history_entry)

    verb = "spostamento" if req.delete_source else "copia"
    return TransferMoveResponse(
        message=f"Avviati {len(created_jobs)} job di {verb}. Batch ID: {batch_id}",
        batch_id=batch_id,
        jobs=created_jobs,
    )


# ---------------------------------------------------------------------------
# Job Status & Control
# ---------------------------------------------------------------------------


@app.get("/api/jobs", response_model=list[JobInfo])
async def list_jobs():
    """Retrieve list and status of all tracked transfer jobs."""
    await update_active_job_statuses()
    return list(JOB_REGISTRY.values())


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: int):
    """Retrieve execution status of a single job directly from Rclone."""
    try:
        raw_status = await rclone_client.get_job_status(job_id)
        # Update local registry if tracked
        if job_id in JOB_REGISTRY and raw_status.get("finished"):
            JOB_REGISTRY[job_id].status = (
                "completed" if raw_status.get("success") else "failed"
            )
            JOB_REGISTRY[job_id].error = raw_status.get("error")
            JOB_REGISTRY[job_id].duration = raw_status.get("duration")

        return JobStatusResponse(
            jobid=job_id,
            finished=raw_status.get("finished", False),
            success=raw_status.get("success", False),
            error=raw_status.get("error"),
            duration=raw_status.get("duration"),
            startTime=raw_status.get("startTime"),
            endTime=raw_status.get("endTime"),
            output=raw_status.get("output"),
        )
    except RcloneAPIError as e:
        raise HTTPException(
            status_code=404, detail=f"Job {job_id} not found: {e.message}"
        ) from e


@app.post("/api/jobs/{job_id}/stop")
async def stop_job(job_id: int):
    """Cancel or stop an ongoing Rclone transfer job."""
    res = await rclone_client.stop_job(job_id)
    if job_id in JOB_REGISTRY:
        JOB_REGISTRY[job_id].status = "stopped"
    return {
        "status": "success",
        "message": f"Job {job_id} stop requested.",
        "details": res,
    }


async def update_active_job_statuses() -> None:
    """Helper to refresh statuses of active jobs in registry."""
    for job_id, job in list(JOB_REGISTRY.items()):
        if job.status == "running":
            try:
                res = await rclone_client.get_job_status(job_id)
                if res.get("finished"):
                    job.status = "completed" if res.get("success") else "failed"
                    job.error = res.get("error")
                    job.duration = res.get("duration")
            except (RcloneAPIError, httpx.HTTPError) as exc:
                logger.debug("Error checking status for job %d: %s", job_id, exc)


async def build_stats_payload() -> dict[str, Any]:
    """Helper to query Rclone core/stats and combine with active jobs."""
    try:
        raw_stats = await rclone_client.get_stats()
    except (RcloneAPIError, httpx.HTTPError) as exc:
        logger.debug("Error fetching Rclone stats: %s", exc)
        raw_stats = {}

    await update_active_job_statuses()

    transferring_list = []
    for t in raw_stats.get("transferring", []):
        transferring_list.append(
            TransferringItem(
                name=t.get("name", ""),
                size=t.get("size", 0),
                bytes=t.get("bytes", 0),
                percentage=t.get("percentage", 0),
                speed=t.get("speed", 0.0),
                speed_avg=t.get("speedAvg", 0.0),
                eta=t.get("eta"),
            ).model_dump()
        )

    stats = {
        "bytes": raw_stats.get("bytes", 0),
        "total_bytes": raw_stats.get("totalBytes", 0),
        "speed": raw_stats.get("speed", 0.0),
        "transfers": raw_stats.get("transfers", 0),
        "total_transfers": raw_stats.get("totalTransfers", 0),
        "checks": raw_stats.get("checks", 0),
        "deletes": raw_stats.get("deletes", 0),
        "errors": raw_stats.get("errors", 0),
        "fatal_error": raw_stats.get("fatalError", False),
        "elapsed_time": raw_stats.get("elapsedTime", 0.0),
        "eta": raw_stats.get("eta"),
        "transferring": transferring_list,
        "active_jobs": [
            j.model_dump() for j in JOB_REGISTRY.values() if j.status == "running"
        ],
        "all_jobs": [j.model_dump() for j in JOB_REGISTRY.values()],
    }
    return stats


# ---------------------------------------------------------------------------
# Real-Time Monitoring: Server-Sent Events (SSE) & WebSocket
# ---------------------------------------------------------------------------


@app.get("/api/stream/stats")
async def stream_stats():
    """Server-Sent Events (SSE) endpoint emitting real-time transfer stats every second."""

    async def event_generator():
        while True:
            try:
                stats = await build_stats_payload()
                yield f"data: {json.dumps(stats)}\n\n"
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error("Error generating SSE stats: %s", e)
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(settings.stats_poll_interval)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.websocket("/ws/stats")
async def websocket_stats(websocket: WebSocket):
    """WebSocket endpoint streaming transfer speed, progress, and active jobs every second."""
    await websocket.accept()
    logger.info("WebSocket client connected for stats.")
    try:
        while True:
            stats = await build_stats_payload()
            await websocket.send_json(stats)
            await asyncio.sleep(settings.stats_poll_interval)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected.")
    except Exception as e:  # noqa: BLE001
        logger.error("WebSocket error: %s", e)
        try:
            await websocket.close()
        except Exception as close_err:  # noqa: BLE001
            logger.debug("Error while closing websocket: %s", close_err)


# ---------------------------------------------------------------------------
# Cloudflare Tunnel & System Settings
# ---------------------------------------------------------------------------


@app.get("/api/system/tunnel", response_model=TunnelStatusResponse)
async def get_tunnel_status():
    """Check if Cloudflare Quick Tunnel is active and return the public URL."""
    candidates = [
        Path("/app/data/tunnel_url.txt"),
        Path(settings.rclone_config_path).parent.parent / "data" / "tunnel_url.txt",
        Path("data/tunnel_url.txt"),
        Path("/tmp/tunnel_url.txt"),
    ]
    for p in candidates:
        if p.exists():
            try:
                content = p.read_text(encoding="utf-8").strip()
                if content.startswith("http"):
                    return TunnelStatusResponse(active=True, url=content)
            except Exception as e:  # noqa: BLE001
                logger.debug("Could not read tunnel file %s: %s", p, e)
    return TunnelStatusResponse(active=False, url=None)


@app.post("/api/system/bwlimit")
async def set_bandwidth_limit(req: BandwidthLimitRequest):
    """Dynamically set transfer speed limit in Rclone (e.g. '10M', 'off')."""
    res = await rclone_client.set_bwlimit(req.rate)
    return {"status": "success", "rate": req.rate, "details": res}


# ---------------------------------------------------------------------------
# In-App Remote Diagnostics & Configuration
# ---------------------------------------------------------------------------


@app.post("/api/remotes/test", response_model=RemoteTestResponse)
async def test_remote_connectivity(req: RemoteTestRequest):
    """Test connection to a configured remote."""
    about = await rclone_client.get_about(req.remote)
    if "error" in about:
        return RemoteTestResponse(
            remote=req.remote,
            success=False,
            message=f"Connection failed: {about['error']}",
            about=None,
        )
    return RemoteTestResponse(
        remote=req.remote,
        success=True,
        message=f"Remote '{req.remote}' is reachable and active.",
        about=about,
    )


@app.post("/api/remotes/configure")
async def configure_remote(req: RemoteConfigRequest):
    """Configure or update a remote in Rclone."""
    try:
        res = await rclone_client.config_create(
            name=req.name,
            remote_type=req.type,
            parameters=req.parameters,
            obscure=req.obscure,
        )
        return {
            "status": "success",
            "message": f"Remote '{req.name}' configured.",
            "details": res,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/remotes/disconnect")
async def disconnect_remote(req: DisconnectRemoteRequest):
    """Disconnect and remove remote configuration."""
    try:
        res = await rclone_client.config_delete(req.remote)
        return {
            "status": "success",
            "message": f"Remote '{req.remote}' rimosso con successo.",
            "details": res,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ---------------------------------------------------------------------------
# Alternative Google Drive Authentication Methods
# ---------------------------------------------------------------------------


@app.post("/api/auth/google/service-account", response_model=GoogleAuthResponse)
async def auth_google_service_account(req: GoogleServiceAccountAuthRequest):
    """Authenticate Google Drive using a Service Account JSON."""
    raw_json = req.service_account_json.strip()
    try:
        sa_data = json.loads(raw_json)
    except json.JSONDecodeError as err:
        raise HTTPException(
            status_code=400,
            detail=f"Formato JSON della Service Account non valido: {err}",
        ) from err

    if sa_data.get("type") != "service_account" or "client_email" not in sa_data:
        raise HTTPException(
            status_code=400,
            detail="Il JSON fornito non è una chiave Service Account Google valida (manca 'type': 'service_account' o 'client_email').",
        )

    client_email = sa_data.get("client_email")
    params: dict[str, str] = {
        "scope": "drive",
        "service_account_credentials": json.dumps(sa_data),
    }

    if req.folder_id and req.folder_id.strip():
        clean_id = req.folder_id.strip()
        if "folders/" in clean_id:
            clean_id = clean_id.split("folders/")[1].split("?")[0].strip("/")
        params["root_folder_id"] = clean_id

    try:
        await rclone_client.config_create(
            name="gdrive",
            remote_type="drive",
            parameters=params,
        )
        return GoogleAuthResponse(
            success=True,
            message=f"Google Drive collegato con successo tramite Service Account ({client_email}).",
            remote="gdrive",
            email=client_email,
        )
    except Exception as exc:
        logger.error("Errore configurazione Service Account Google: %s", exc)
        raise HTTPException(
            status_code=400,
            detail=f"Impossibile configurare Google Drive con la Service Account: {exc}",
        ) from exc


@app.post("/api/auth/google/token", response_model=GoogleAuthResponse)
async def auth_google_token(req: GoogleOAuthTokenAuthRequest):
    """Authenticate Google Drive using an OAuth token JSON blob."""
    token_str = req.token_json.strip()
    try:
        token_obj = json.loads(token_str)
        if "access_token" not in token_obj:
            raise ValueError("Manca il campo 'access_token' nel token JSON.")
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Token JSON non valido: {exc}",
        ) from exc

    params: dict[str, str] = {
        "scope": "drive",
        "token": json.dumps(token_obj),
    }
    if req.client_id and req.client_id.strip():
        params["client_id"] = req.client_id.strip()
    if req.client_secret and req.client_secret.strip():
        params["client_secret"] = req.client_secret.strip()

    try:
        await rclone_client.config_create(
            name="gdrive",
            remote_type="drive",
            parameters=params,
        )
        return GoogleAuthResponse(
            success=True,
            message="Google Drive collegato con successo tramite OAuth Token.",
            remote="gdrive",
        )
    except Exception as exc:
        logger.error("Errore configurazione OAuth Token Google: %s", exc)
        raise HTTPException(
            status_code=400,
            detail=f"Errore configurazione OAuth Token: {exc}",
        ) from exc


@app.post("/api/auth/google/exchange-code", response_model=GoogleAuthResponse)
async def auth_google_exchange_code(req: GoogleOAuthExchangeRequest):
    """Exchange a Google OAuth authorization code for tokens and configure gdrive."""
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "code": req.code.strip(),
        "client_id": req.client_id.strip(),
        "client_secret": req.client_secret.strip(),
        "redirect_uri": req.redirect_uri.strip(),
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(token_url, data=payload)
            if resp.status_code != 200:
                err_detail = resp.text
                try:
                    err_json = resp.json()
                    err_detail = err_json.get("error_description", err_detail)
                except (json.JSONDecodeError, ValueError) as parse_err:
                    logger.debug("Response was not JSON: %s", parse_err)
                raise HTTPException(
                    status_code=400,
                    detail=f"Scambio codice fallito da Google: {err_detail}",
                )
            token_data = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Impossibile contattare i server Google per lo scambio codice: {exc}",
        ) from exc

    rclone_token = {
        "access_token": token_data.get("access_token"),
        "token_type": token_data.get("token_type", "Bearer"),
        "refresh_token": token_data.get("refresh_token"),
        "expiry": token_data.get("expiry"),
    }
    params: dict[str, str] = {
        "scope": "drive",
        "client_id": req.client_id.strip(),
        "client_secret": req.client_secret.strip(),
        "token": json.dumps(rclone_token),
    }

    try:
        await rclone_client.config_create(
            name="gdrive",
            remote_type="drive",
            parameters=params,
        )
        return GoogleAuthResponse(
            success=True,
            message="Autenticazione Google completata con successo! Account collegato.",
            remote="gdrive",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Errore nella registrazione in Rclone: {exc}",
        ) from exc


@app.post("/api/remotes/obscure", response_model=ObscurePasswordResponse)
async def obscure_password(req: ObscurePasswordRequest):
    """Obscure password using Rclone native obscuring algorithm."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "rclone",
            "obscure",
            req.password,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=stderr.decode().strip())
        return ObscurePasswordResponse(obscured=stdout.decode().strip())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ---------------------------------------------------------------------------
# Transfer History Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/history")
async def get_transfer_history(limit: int = 50):
    """Retrieve persistent transfer history log."""
    return history_manager.get_entries(limit=limit)


@app.delete("/api/history")
async def clear_transfer_history():
    """Clear all transfer history."""
    history_manager.clear()
    return {"status": "success", "message": "History cleared."}


# ---------------------------------------------------------------------------
# Zero-Setup Demo / Simulation Environment
# ---------------------------------------------------------------------------


@app.post("/api/demo/activate")
async def activate_demo_environment():
    """Set up simulated Google Drive and iCloud Drive directories for instant testing."""
    paths = setup_demo_environment()
    conf_content = generate_demo_rclone_conf(paths["gdrive_path"], paths["icloud_path"])
    conf_file = Path(settings.rclone_config_path)
    conf_file.parent.mkdir(parents=True, exist_ok=True)
    conf_file.write_text(conf_content, encoding="utf-8")

    # Re-create remotes in running rclone daemon via config_create
    await rclone_client.config_create(
        "gdrive", "alias", {"remote": paths["gdrive_path"]}
    )
    await rclone_client.config_create(
        "icloud", "alias", {"remote": paths["icloud_path"]}
    )

    return {
        "status": "success",
        "message": "Modalità dimostrativa attivata. File di prova pronti in Google Drive e iCloud.",
        "paths": paths,
    }


# ---------------------------------------------------------------------------
# Static Files & SPA Frontend Serving
# ---------------------------------------------------------------------------

frontend_path = Path(settings.frontend_dir)
if frontend_path.exists() and frontend_path.is_dir():
    app.mount(
        "/", StaticFiles(directory=str(frontend_path), html=True), name="frontend"
    )
else:
    logger.warning(
        "Frontend directory '%s' not found. SPA will not be served.", frontend_path
    )
