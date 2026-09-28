"""Demo and Simulation Environment for CloudSync.

Enables instant out-of-the-box testing without requiring Google Cloud OAuth
or Apple App-Specific Password credentials. Creates realistic mock file structures
and configures virtual local remotes in Rclone.
"""

import logging
from pathlib import Path

from backend.config import settings

logger = logging.getLogger("cloudsync.demo")


def setup_demo_environment() -> dict[str, str]:
    """Create simulated Google Drive and iCloud Drive directories with realistic sample files.

    Returns the paths to the demo directories.
    """
    base_dir = Path(settings.rclone_config_path).parent.parent / "data" / "demo"
    gdrive_dir = base_dir / "gdrive_mock"
    icloud_dir = base_dir / "icloud_mock"

    # Create directories
    gdrive_dir.mkdir(parents=True, exist_ok=True)
    icloud_dir.mkdir(parents=True, exist_ok=True)

    # Subfolders inside Google Drive
    work_docs = gdrive_dir / "Documenti_Aziendali"
    work_docs.mkdir(parents=True, exist_ok=True)

    photos = gdrive_dir / "Fotografie"
    photos.mkdir(parents=True, exist_ok=True)

    # Populate sample files if empty
    if not any(work_docs.iterdir()):
        # 1. Standard Office & PDF
        (work_docs / "Bilancio_Preventivo_2026.xlsx").write_bytes(
            b"Simulated Excel Spreadsheet Content " * 1024
        )
        (work_docs / "Relazione_Tecnica_Architettura.pdf").write_bytes(
            b"%PDF-1.4 Simulated PDF Document Content " * 2048
        )

        # 2. Simulated Google Doc (with Google Doc mime header)
        (work_docs / "Progetto_CloudSync.gdoc").write_text(
            '{"doc_id": "12345", "title": "Progetto CloudSync", "mimeType": "application/vnd.google-apps.document"}',
            encoding="utf-8",
        )

        # 3. Media files
        (photos / "Paesaggio_Dolomiti.jpg").write_bytes(
            b"\xff\xd8\xff\xe0\x00\x10JFIF Simulated Image Data " * 4096
        )
        (photos / "Screenshot_Architettura.png").write_bytes(
            b"\x89PNG\r\n\x1a\n Simulated PNG Data " * 2048
        )

        # 4. Root archive
        (gdrive_dir / "Backup_Progetti_Precedenti.zip").write_bytes(
            b"PK\x03\x04 Simulated ZIP Archive " * 10240
        )

        logger.info("Demo sample files created successfully in %s", gdrive_dir)

    # Populate iCloud Drive sample structure
    backups_folder = icloud_dir / "Archivi"
    backups_folder.mkdir(parents=True, exist_ok=True)

    return {
        "gdrive_path": str(gdrive_dir),
        "icloud_path": str(icloud_dir),
    }


def generate_demo_rclone_conf(gdrive_dir: str, icloud_dir: str) -> str:
    """Generate an rclone.conf content using local filesystem remotes for zero-setup demo."""
    return f"""# ==============================================================================
# CloudSync - Configurazione Modalità Demo (Simulazione Locale)
# ==============================================================================

[gdrive]
type = alias
remote = {gdrive_dir}

[icloud]
type = alias
remote = {icloud_dir}
"""
