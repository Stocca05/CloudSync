"""Stateless trusted worker. One isolated temporary rclone config per execution.
Fail closed when control-plane heartbeats cannot renew ownership.
"""

import concurrent.futures
import configparser
import json
import logging
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import httpx

from .providers import public_host

log = logging.getLogger("cloudsync.worker")
STOP = threading.Event()


def obscure(value):
    return subprocess.run(
        ["rclone", "obscure", "-"], input=value, text=True, capture_output=True, check=True, timeout=10
    ).stdout.strip()


def write_config(job, path):
    config = configparser.ConfigParser(interpolation=None)
    for remote_id, data in job["remotes"].items():
        values = dict(data["config"])
        if values["type"] == "sftp":
            public_host(values["host"])
            values["known_hosts_file"] = os.environ.get("SFTP_KNOWN_HOSTS", "/etc/cloudsync/known_hosts")
        for field in ["url", "endpoint"]:
            if values.get(field):
                from urllib.parse import urlsplit

                public_host(urlsplit(values[field]).hostname)
        if "pass" in values:
            values["pass"] = obscure(values["pass"])
        config["r" + remote_id] = values
    with path.open("w") as output:
        config.write(output)
    path.chmod(0o600)


def command(job, config, port, log_path):
    source = "r" + job["source_id"] + ":" + job["source_path"]
    common = [
        "--config",
        str(config),
        "--use-json-log",
        "--log-file",
        str(log_path),
        "--stats",
        "1s",
        "--stats-log-level",
        "NOTICE",
        "--retries",
        "1",
        "--low-level-retries",
        "3",
        "--contimeout",
        "15s",
        "--timeout",
        "60s",
        "--transfers",
        "2",
        "--checkers",
        "4",
        "--rc",
        "--rc-addr",
        f"127.0.0.1:{port}",
        "--bwlimit",
        str(job["bandwidth_bps"]) + "B",
    ]
    if job["operation"] == "list":
        return ["rclone", "lsjson", source, "--no-mimetype", "--no-modtime", *common]
    operation = job["operation"]
    if job["is_file"]:
        operation = "copyto" if operation == "copy" else "moveto"
    destination = "r" + job["destination_id"] + ":" + job["destination_path"]
    return ["rclone", operation, source, destination, *common]


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def read_progress(path):
    """Read a bounded tail; never send raw rclone errors/credentials to the server."""
    stats = {}
    if not path.exists():
        return stats
    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 32768))
        for line in stream:
            try:
                record = json.loads(line)
                if isinstance(record.get("stats"), dict):
                    stats = record["stats"]
            except (ValueError, UnicodeError):
                pass
    return stats


def result_listing(path):
    if path.stat().st_size > 8_000_000:
        raise ValueError("Cartella troppo grande: scegli un percorso più specifico")
    data = json.loads(path.read_text() or "[]")
    return {
        "items": [{k: item.get(k) for k in ["Path", "Name", "Size", "IsDir"]} for item in data[:1000]],
        "truncated": len(data) > 1000,
    }


def run_job(api_url, token, job):
    headers = {"Authorization": "Bearer " + token}
    auth = ("worker", os.urandom(24).hex())
    # Environment contains only runtime requirements, never the worker enrollment token.
    env = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "HOME", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "LANG"}
    }
    env.update(RCLONE_RC_USER=auth[0], RCLONE_RC_PASS=auth[1])
    with (
        tempfile.TemporaryDirectory(prefix="cloudsync-") as directory,
        httpx.Client(base_url=api_url, headers=headers, timeout=8) as api,
    ):
        root = Path(directory)
        config, log_path, output_path = root / "rclone.conf", root / "rclone.log", root / "output.json"
        job_id = job["id"]
        base = f"/internal/jobs/{job_id}"
        payload = {"lease_token": job["lease_token"], "stats": {}}
        process = None
        cancelled = False
        ownership_lost = False
        last_renewed = time.monotonic()
        try:
            write_config(job, config)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            with output_path.open("wb") as output, (root / "stderr").open("wb") as errors:
                process = subprocess.Popen(
                    command(job, config, port, log_path), stdout=output, stderr=errors, env=env
                )
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", auth=auth, timeout=2) as rc:
                    while True:
                        payload["stats"] = read_progress(log_path)
                        try:
                            response = api.post(base + "/heartbeat", json=payload)
                            response.raise_for_status()
                            control = response.json()
                            last_renewed = time.monotonic()
                            if control["cancel"] or STOP.is_set():
                                cancelled = True
                                stop_process(process)
                            if process.poll() is None:
                                try:
                                    rate_response = rc.post(
                                        "/core/bwlimit", json={"rate": str(control["bandwidth_bps"]) + "B"}
                                    )
                                    rate_response.raise_for_status()
                                except httpx.HTTPError:
                                    # RC startup is asynchronous. A later heartbeat retries.
                                    pass
                        except httpx.HTTPStatusError as exc:
                            if exc.response.status_code in {401, 403, 409}:
                                ownership_lost = True
                                stop_process(process)
                                break
                        except httpx.HTTPError:
                            pass
                        if time.monotonic() - last_renewed > 25:
                            ownership_lost = True
                            stop_process(process)
                            break
                        if process.poll() is not None:
                            break
                        if output_path.stat().st_size > 8_000_000 or (
                            log_path.exists() and log_path.stat().st_size > 32_000_000
                        ):
                            stop_process(process)
                            raise ValueError(
                                "Limite output raggiunto: suddividi il trasferimento o la cartella"
                            )
                        STOP.wait(3)
            if ownership_lost:
                log.warning("job %s stopped: lease unavailable", job_id)
                return
            payload["stats"] = read_progress(log_path)
            success = process.returncode == 0 and not cancelled
            result = result_listing(output_path) if success and job["operation"] == "list" else {}
            refreshed = {}
            saved = configparser.ConfigParser(interpolation=None)
            saved.read(config)
            for remote_id, data in job["remotes"].items():
                if data["config"]["type"] == "drive" and saved.has_option("r" + remote_id, "token"):
                    refreshed[remote_id] = {
                        "revision": data["revision"],
                        "token": saved["r" + remote_id]["token"],
                    }
            error = (
                ""
                if success or cancelled
                else f"Rclone terminato con codice {process.returncode}. Controlla credenziali, permessi e percorso; per SFTP verifica la chiave host sul nodo."
            )
            completion = {
                **payload,
                "success": success,
                "cancelled": cancelled,
                "error": error,
                "result": result,
                "refreshed": refreshed,
            }
        except Exception as exc:
            if process:
                stop_process(process)
            log.warning("job %s failed (%s)", job_id, type(exc).__name__)
            completion = {
                **payload,
                "success": False,
                "error": "Impossibile eseguire il lavoro. Verifica il collegamento e la configurazione del nodo.",
            }
        if not ownership_lost:
            # Retry completion within the valid lease; duplicate/fenced completion is rejected.
            for attempt in range(3):
                try:
                    response = api.post(base + "/complete", json=completion)
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt < 2:
                        STOP.wait(2)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    api_url = os.environ.get("CONTROL_URL", "http://api:8000")
    token = os.environ["WORKER_TOKEN"]
    slots = max(1, min(32, int(os.environ.get("WORKER_SLOTS", "2"))))
    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, lambda *_: STOP.set())
    subprocess.run(["rclone", "version"], check=True, capture_output=True)
    futures = set()
    with (
        concurrent.futures.ThreadPoolExecutor(max_workers=slots) as pool,
        httpx.Client(base_url=api_url, headers={"Authorization": "Bearer " + token}, timeout=10) as api,
    ):
        while not STOP.is_set():
            futures = {f for f in futures if not f.done()}
            if len(futures) < slots:
                try:
                    response = api.post("/internal/claim")
                    response.raise_for_status()
                    job = response.json()["job"]
                    if job:
                        futures.add(pool.submit(run_job, api_url, token, job))
                        continue
                except (httpx.HTTPError, ValueError):
                    log.warning("Control plane unavailable; retrying")
            STOP.wait(2)


if __name__ == "__main__":
    main()
