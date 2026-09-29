"""Closed schemas: never accept rclone flags, local paths, aliases or arbitrary config sections."""
import ipaddress
import json
import socket
from pathlib import PurePosixPath
from urllib.parse import urlsplit

PROVIDERS = {
    "s3": {"label": "S3 / MinIO", "fields": ["provider", "access_key_id", "secret_access_key", "region", "endpoint"], "required": ["access_key_id", "secret_access_key"]},
    "sftp": {"label": "SFTP", "fields": ["host", "port", "user", "pass"], "required": ["host", "user", "pass"]},
    "webdav": {"label": "WebDAV", "fields": ["url", "user", "pass", "vendor"], "required": ["url", "user", "pass"]},
    "drive": {"label": "Google Drive · token Rclone", "fields": ["token", "client_id", "client_secret", "root_folder_id"], "required": ["token"]},
}


def safe_path(value: str) -> str:
    if len(value) > 2048 or any(ord(c) < 32 for c in value) or "\\" in value:
        raise ValueError("Percorso non valido")
    if value.startswith("/") or ".." in PurePosixPath(value).parts or ":" in value:
        raise ValueError("Usa un percorso relativo al collegamento cloud")
    return value.strip("/")


def public_host(host: str):
    """Cloud endpoints cannot target loopback, LAN, metadata or the control plane."""
    if not host:
        raise ValueError("Host mancante")
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Host non risolvibile") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%")[0])
        if not ip.is_global:
            raise ValueError("Gli endpoint privati richiedono una rete worker dedicata; usa un host pubblico")


def validate_config(provider: str, values: dict) -> dict:
    if provider not in PROVIDERS:
        raise ValueError("Provider non supportato")
    schema = PROVIDERS[provider]
    if set(values) - set(schema["fields"]):
        raise ValueError("Opzioni non consentite")
    clean = {}
    for key, value in values.items():
        if not isinstance(value, str) or len(value) > 16000 or any(c in value for c in "\r\n\x00"):
            raise ValueError("Valore di configurazione non valido")
        if value:
            clean[key] = value
    if any(not clean.get(key) for key in schema["required"]):
        raise ValueError("Completa tutti i campi obbligatori")
    if provider == "s3":
        clean.setdefault("provider", "AWS")
        if clean["provider"] not in {"AWS", "Minio", "Other", "Cloudflare", "Wasabi", "Backblaze"}:
            raise ValueError("Provider S3 non consentito")
    if provider == "webdav":
        clean.setdefault("vendor", "other")
        if clean["vendor"] not in {"other", "nextcloud", "owncloud", "sharepoint"}:
            raise ValueError("Vendor WebDAV non consentito")
    if provider == "sftp":
        port = int(clean.get("port", "22"))
        if not 1 <= port <= 65535:
            raise ValueError("Porta non valida")
        public_host(clean["host"])
        clean["port"] = str(port)
        # rclone SFTP has no host-key verification by default. Worker adds a managed known_hosts file.
    for field in ["url", "endpoint"]:
        if clean.get(field):
            url = urlsplit(clean[field])
            if url.scheme != "https" or url.username or url.password or url.fragment:
                raise ValueError("L'endpoint deve essere HTTPS senza credenziali nell'URL")
            public_host(url.hostname)
    if provider == "drive":
        token = json.loads(clean["token"])
        if not isinstance(token, dict) or not token.get("access_token") or not token.get("refresh_token"):
            raise ValueError("Inserisci il token JSON completo prodotto da rclone authorize drive")
        clean["scope"] = "drive"
    return {"type": provider, **clean}
