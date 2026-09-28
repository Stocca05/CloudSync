"""Async HTTP Client for Rclone Remote Control (rc) REST API.

Manages all interactions with the Rclone daemon running in background.
All communications happen over localhost inside the container for total privacy.
"""

import json
import logging
from typing import Any

import httpx

from backend.config import settings

logger = logging.getLogger("cloudsync.rclone")


class RcloneAPIError(Exception):
    """Exception raised when an Rclone RC API call fails."""

    def __init__(self, message: str, status_code: int = 500, details: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details


class RcloneClient:
    """Async client wrapper for Rclone Remote Control endpoints."""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.rclone_rc_url).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Initialize the shared httpx AsyncClient."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(30.0, connect=5.0),
                headers={"Content-Type": "application/json"},
            )

    async def close(self) -> None:
        """Close the underlying HTTP client session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        """Ensure client is active and return it."""
        if self._client is None or self._client.is_closed:
            raise RcloneAPIError(
                "RcloneClient is not started. Call start() first.", status_code=500
            )
        return self._client

    async def _post(
        self, endpoint: str, json_data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Perform a POST request against the Rclone RC API."""
        endpoint = endpoint.lstrip("/")
        try:
            response = await self.client.post(f"/{endpoint}", json=json_data or {})
            if response.status_code >= 400:
                try:
                    err_json = response.json()
                    err_msg = err_json.get("error", response.text)
                except json.JSONDecodeError:
                    err_msg = response.text
                logger.error(
                    "Rclone RC error on %s: %s (status %d)",
                    endpoint,
                    err_msg,
                    response.status_code,
                )
                raise RcloneAPIError(
                    f"Rclone RC call '{endpoint}' failed: {err_msg}",
                    status_code=response.status_code,
                    details=err_msg,
                )
            return response.json()
        except httpx.ConnectError as exc:
            logger.error(
                "Failed to connect to Rclone daemon at %s: %s", self.base_url, exc
            )
            raise RcloneAPIError(
                f"Rclone daemon unreachable at {self.base_url}. Ensure daemon is running.",
                status_code=502,
            ) from exc
        except httpx.TimeoutException as exc:
            logger.error(
                "Timeout connecting to Rclone daemon at %s: %s", self.base_url, exc
            )
            raise RcloneAPIError(
                f"Rclone request timeout on '{endpoint}'.", status_code=504
            ) from exc

    async def get_version(self) -> dict[str, Any]:
        """Return Rclone version and architecture info."""
        return await self._post("core/version")

    async def is_alive(self) -> bool:
        """Check if Rclone daemon responds to ping."""
        try:
            await self._post("rc/noop")
            return True
        except (RcloneAPIError, httpx.HTTPError) as exc:
            logger.debug("Rclone is_alive check failed: %s", exc)
            return False

    async def list_remotes(self) -> list[str]:
        """Retrieve list of configured remote names."""
        res = await self._post("config/listremotes")
        # Returns {"remotes": ["gdrive", "icloud", ...]}
        return res.get("remotes", [])

    async def get_about(self, remote: str) -> dict[str, Any]:
        """Get storage quota information for a remote."""
        fs = f"{remote.rstrip(':')}:"
        try:
            return await self._post("operations/about", {"fs": fs})
        except RcloneAPIError as e:
            logger.warning(
                "Remote '%s' does not support operations/about: %s", remote, e.message
            )
            return {"error": e.message}

    async def list_directory(self, remote: str, path: str = "") -> list[dict[str, Any]]:
        """List files and subdirectories in a given remote path."""
        fs = f"{remote.rstrip(':')}:"
        clean_path = path.strip("/")
        payload = {
            "fs": fs,
            "remote": clean_path,
            "opt": {
                "recurse": False,
                "noModTime": False,
                "showHidden": False,
            },
        }
        res = await self._post("operations/list", payload)
        return res.get("list", [])

    async def mkdir(self, remote: str, path: str) -> dict[str, Any]:
        """Create a new directory in the given remote."""
        fs = f"{remote.rstrip(':')}:"
        clean_path = path.strip("/")
        return await self._post("operations/mkdir", {"fs": fs, "remote": clean_path})

    async def move_file(
        self,
        src_remote: str,
        src_path: str,
        dst_remote: str,
        dst_path: str,
        dry_run: bool = False,
    ) -> int:
        """Move a single file from source remote to destination remote asynchronously.

        Returns the background Rclone job ID.
        """
        src_fs = f"{src_remote.rstrip(':')}:"
        dst_fs = f"{dst_remote.rstrip(':')}:"
        payload: dict[str, Any] = {
            "srcFs": src_fs,
            "srcRemote": src_path.lstrip("/"),
            "dstFs": dst_fs,
            "dstRemote": dst_path.lstrip("/"),
            "_async": True,
        }
        if dry_run:
            payload["_config"] = {"DryRun": True}

        res = await self._post("operations/movefile", payload)
        job_id = res.get("jobid")
        if job_id is None:
            raise RcloneAPIError(
                "Failed to obtain jobid from operations/movefile", details=res
            )
        return int(job_id)

    async def move_directory(
        self,
        src_remote: str,
        src_path: str,
        dst_remote: str,
        dst_path: str,
        delete_empty_src_dirs: bool = True,
        export_formats: str | None = None,
        dry_run: bool = False,
    ) -> int:
        """Move an entire directory tree from source to destination asynchronously.

        Returns the background Rclone job ID.
        """
        src_clean = src_path.strip("/")
        dst_clean = dst_path.strip("/")
        src_fs = f"{src_remote.rstrip(':')}:{src_clean}"
        dst_fs = f"{dst_remote.rstrip(':')}:{dst_clean}"

        config_dict: dict[str, Any] = {
            "DriveExportFormats": export_formats or settings.drive_export_formats,
        }
        if dry_run:
            config_dict["DryRun"] = True

        payload: dict[str, Any] = {
            "srcFs": src_fs,
            "dstFs": dst_fs,
            "deleteEmptySrcDirs": delete_empty_src_dirs,
            "_async": True,
            "_config": config_dict,
        }
        res = await self._post("sync/move", payload)
        job_id = res.get("jobid")
        if job_id is None:
            raise RcloneAPIError("Failed to obtain jobid from sync/move", details=res)
        return int(job_id)

    async def copy_file(
        self,
        src_remote: str,
        src_path: str,
        dst_remote: str,
        dst_path: str,
        dry_run: bool = False,
    ) -> int:
        """Copy a single file from source remote to destination remote asynchronously.

        Returns the background Rclone job ID.
        """
        src_fs = f"{src_remote.rstrip(':')}:"
        dst_fs = f"{dst_remote.rstrip(':')}:"
        payload: dict[str, Any] = {
            "srcFs": src_fs,
            "srcRemote": src_path.lstrip("/"),
            "dstFs": dst_fs,
            "dstRemote": dst_path.lstrip("/"),
            "_async": True,
        }
        if dry_run:
            payload["_config"] = {"DryRun": True}

        res = await self._post("operations/copyfile", payload)
        job_id = res.get("jobid")
        if job_id is None:
            raise RcloneAPIError(
                "Failed to obtain jobid from operations/copyfile", details=res
            )
        return int(job_id)

    async def copy_directory(
        self,
        src_remote: str,
        src_path: str,
        dst_remote: str,
        dst_path: str,
        export_formats: str | None = None,
        dry_run: bool = False,
    ) -> int:
        """Copy an entire directory tree from source to destination asynchronously without deleting source.

        Returns the background Rclone job ID.
        """
        src_clean = src_path.strip("/")
        dst_clean = dst_path.strip("/")
        src_fs = f"{src_remote.rstrip(':')}:{src_clean}"
        dst_fs = f"{dst_remote.rstrip(':')}:{dst_clean}"

        config_dict: dict[str, Any] = {
            "DriveExportFormats": export_formats or settings.drive_export_formats,
        }
        if dry_run:
            config_dict["DryRun"] = True

        payload: dict[str, Any] = {
            "srcFs": src_fs,
            "dstFs": dst_fs,
            "createEmptySrcDirs": True,
            "_async": True,
            "_config": config_dict,
        }
        res = await self._post("sync/copy", payload)
        job_id = res.get("jobid")
        if job_id is None:
            raise RcloneAPIError("Failed to obtain jobid from sync/copy", details=res)
        return int(job_id)

    async def set_bwlimit(self, rate: str = "off") -> dict[str, Any]:
        """Throttle transfer speed dynamically (e.g. '10M', '2M', 'off')."""
        return await self._post("core/bwlimit", {"rate": rate})

    async def config_create(
        self,
        name: str,
        remote_type: str,
        parameters: dict[str, str],
        obscure: bool = False,
    ) -> dict[str, Any]:
        """Create or update a remote in rclone.conf via Rclone RC."""
        payload: dict[str, Any] = {
            "name": name,
            "type": remote_type,
            "parameters": parameters,
            "opt": {"obscure": obscure},
        }
        return await self._post("config/create", payload)

    async def config_delete(self, name: str) -> dict[str, Any]:
        """Delete a remote from rclone.conf."""
        return await self._post("config/delete", {"name": name})

    async def config_get(self, name: str) -> dict[str, Any]:
        """Retrieve configuration parameters for a remote."""
        return await self._post("config/get", {"name": name})

    async def get_job_status(self, job_id: int) -> dict[str, Any]:
        """Check status of a running or completed asynchronous job."""
        return await self._post("job/status", {"jobid": job_id})

    async def list_jobs(self) -> list[int]:
        """List active background job IDs."""
        res = await self._post("job/list")
        return res.get("jobids", [])

    async def stop_job(self, job_id: int) -> dict[str, Any]:
        """Cancel or stop an asynchronous transfer job."""
        return await self._post("job/stop", {"jobid": job_id})

    async def get_stats(self) -> dict[str, Any]:
        """Fetch current transfer metrics, bandwidth speed, ETA, and transferring files."""
        return await self._post("core/stats")


# Global singleton client instance
rclone_client = RcloneClient()
