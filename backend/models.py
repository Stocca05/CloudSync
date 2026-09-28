"""Data models and schemas for CloudSync API requests and responses."""

from typing import Any

from pydantic import BaseModel, Field


class RemoteDetail(BaseModel):
    """Details and storage quota of a configured remote."""

    name: str
    connected: bool
    type: str | None = None
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    error: str | None = None


class RemotesStatusResponse(BaseModel):
    """Overall status of configured remotes in Rclone."""

    gdrive_configured: bool
    icloud_configured: bool
    all_remotes: list[str] = Field(default_factory=list)
    gdrive: RemoteDetail | None = None
    icloud: RemoteDetail | None = None
    error: str | None = None


class FSItem(BaseModel):
    """File or folder item representation."""

    name: str
    path: str
    size: int = 0
    is_dir: bool = False
    mime_type: str | None = None
    mod_time: str | None = None
    is_gdoc: bool = False


class FSListRequest(BaseModel):
    """Request payload to browse a remote directory."""

    remote: str = Field(description="Remote name (e.g. gdrive or icloud)")
    path: str = Field(default="", description="Subpath inside remote")


class FSListResponse(BaseModel):
    """Directory listing response."""

    remote: str
    path: str
    items: list[FSItem] = Field(default_factory=list)
    total_items: int = 0


class MkdirRequest(BaseModel):
    """Request payload to create a new directory."""

    remote: str
    path: str


class TransferItem(BaseModel):
    """Single item selected for moving."""

    path: str
    is_dir: bool = False


class TransferMoveRequest(BaseModel):
    """Request payload to trigger move transfer from source to destination."""

    src_remote: str = Field(default="gdrive", description="Source remote name")
    dst_remote: str = Field(default="icloud", description="Destination remote name")
    dst_path: str = Field(default="", description="Destination directory path")
    items: list[TransferItem] = Field(description="List of files and folders to move")
    export_docs: bool = Field(
        default=True, description="Export Google Docs to Office formats"
    )
    delete_empty_src_dirs: bool = Field(
        default=True, description="Delete empty source folders"
    )
    dry_run: bool = Field(
        default=False, description="Simulate transfer without deleting or copying"
    )


class JobInfo(BaseModel):
    """Information on an active or completed transfer job."""

    job_id: int
    item_path: str
    is_dir: bool
    dst_path: str
    status: str = "queued"  # queued, running, completed, failed
    error: str | None = None
    duration: float | None = None


class TransferMoveResponse(BaseModel):
    """Response returned upon dispatching move jobs."""

    message: str
    batch_id: str
    jobs: list[JobInfo] = Field(default_factory=list)


class JobStatusResponse(BaseModel):
    """Status details of a specific Rclone job."""

    jobid: int
    finished: bool = False
    success: bool = False
    error: str | None = None
    duration: float | None = None
    startTime: str | None = None
    endTime: str | None = None
    output: dict[str, Any] | None = None


class TransferringItem(BaseModel):
    """Details of a single file currently being transferred."""

    name: str
    size: int = 0
    bytes: int = 0
    percentage: int = 0
    speed: float = 0.0
    speed_avg: float = 0.0
    eta: int | None = None


class CoreStats(BaseModel):
    """Aggregated real-time metrics from Rclone core/stats."""

    bytes: int = 0
    total_bytes: int = 0
    speed: float = 0.0
    transfers: int = 0
    total_transfers: int = 0
    checks: int = 0
    deletes: int = 0
    errors: int = 0
    fatal_error: bool = False
    elapsed_time: float = 0.0
    eta: int | None = None
    transferring: list[TransferringItem] = Field(default_factory=list)
    active_jobs: list[JobInfo] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """Health check status."""

    status: str
    rclone_connected: bool
    rclone_version: str | None = None
    config_file_found: bool = False


class BandwidthLimitRequest(BaseModel):
    """Request payload to set global transfer bandwidth throttle."""

    rate: str = Field(
        default="off", description="Bandwidth limit (e.g. 10M, 5M, 1M, off)"
    )


class RemoteConfigRequest(BaseModel):
    """Request payload to configure or update a remote."""

    name: str = Field(description="Remote name (gdrive or icloud)")
    type: str = Field(description="Remote type (drive, webdav, alias, local)")
    parameters: dict[str, str] = Field(default_factory=dict)
    obscure: bool = Field(
        default=False, description="Automatically obscure password fields"
    )


class RemoteTestRequest(BaseModel):
    """Request to test remote connectivity."""

    remote: str


class RemoteTestResponse(BaseModel):
    """Result of remote connectivity test."""

    remote: str
    success: bool
    message: str
    about: dict[str, Any] | None = None


class ObscurePasswordRequest(BaseModel):
    """Request payload to obscure a plaintext password."""

    password: str


class ObscurePasswordResponse(BaseModel):
    """Obscured password string."""

    obscured: str


class TunnelStatusResponse(BaseModel):
    """Status of Cloudflare quick tunnel."""

    active: bool
    url: str | None = None
