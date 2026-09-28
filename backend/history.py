"""Persistent Transfer History Management.

Tracks past transfer operations and saves their audit logs to disk (JSON)
so they survive container restarts.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.config import settings

logger = logging.getLogger("cloudsync.history")


class HistoryItem(BaseModel):
    """Record of a single transferred item within a batch."""

    path: str
    is_dir: bool
    dst_path: str
    status: str = "completed"  # completed, failed, skipped
    error: str | None = None


class HistoryEntry(BaseModel):
    """Record of an executed transfer batch."""

    id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    src_remote: str = "gdrive"
    dst_remote: str = "icloud"
    dst_path: str = ""
    total_items: int = 0
    status: str = "running"  # running, completed, failed, partial
    dry_run: bool = False
    items: list[HistoryItem] = Field(default_factory=list)
    duration_seconds: float = 0.0
    error: str | None = None


class HistoryManager:
    """Manages reading and persisting transfer history."""

    def __init__(self, storage_path: str | None = None):
        self.storage_path = Path(
            storage_path
            or (
                Path(settings.rclone_config_path).parent.parent
                / "data"
                / "history.json"
            )
        )
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        """Create parent directory and empty history file if not existing."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.storage_path.exists():
                self.storage_path.write_text("[]", encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Could not initialize history storage at %s: %s", self.storage_path, e
            )

    def get_entries(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve recent transfer history entries."""
        try:
            if not self.storage_path.exists():
                return []
            content = self.storage_path.read_text(encoding="utf-8")
            data = json.loads(content)
            # Sort newest first
            return sorted(data, key=lambda x: x.get("timestamp", ""), reverse=True)[
                :limit
            ]
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to load history entries: %s", e)
            return []

    def save_entry(self, entry: HistoryEntry) -> None:
        """Save or update an entry in the history store."""
        try:
            entries = self.get_entries(limit=500)
            existing_idx = next(
                (i for i, e in enumerate(entries) if e.get("id") == entry.id), None
            )
            entry_dict = entry.model_dump()

            if existing_idx is not None:
                entries[existing_idx] = entry_dict
            else:
                entries.insert(0, entry_dict)

            # Cap history at 500 items to avoid unbounded disk growth
            entries = entries[:500]
            self.storage_path.write_text(
                json.dumps(entries, indent=2), encoding="utf-8"
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to save history entry: %s", e)

    def clear(self) -> None:
        """Clear all stored history."""
        try:
            self.storage_path.write_text("[]", encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to clear history: %s", e)


history_manager = HistoryManager()
