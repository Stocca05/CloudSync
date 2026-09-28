"""Configuration settings for CloudSync application.

Provides typed environment variables using pydantic-settings,
with sensible production defaults for the containerized environment.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment override support."""

    # Rclone Remote Control Daemon settings
    rclone_rc_url: str = "http://127.0.0.1:5572"
    rclone_config_path: str = "/app/config/rclone.conf"

    # Default Remote Names in rclone.conf
    gdrive_remote: str = "gdrive"
    icloud_remote: str = "icloud"

    # Google Drive export formats for Google Docs, Sheets, Slides
    drive_export_formats: str = "docx,xlsx,pptx,pdf"

    # Monitoring and polling
    stats_poll_interval: float = 1.0

    # Server settings
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # Path to frontend assets
    frontend_dir: str = str(Path(__file__).parent.parent / "frontend")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
