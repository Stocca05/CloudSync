#!/usr/bin/env python3
"""CloudSync Node Runner & Live Terminal Dashboard.
Monitors bandwidth percentages, active worker slots, real-time jobs, and transfer speeds.
"""

import collections
import concurrent.futures
import configparser
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit

import httpx

# Ensure local cloudsync package is importable
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(BASE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(BASE_DIR.parent))

# Rich UI imports
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Load .env file
def load_env():
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        with env_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    if k not in os.environ:
                        os.environ[k] = v

load_env()

# Configuration
NODE_NAME = os.environ.get("NODE_NAME", "mac-stocca")
CONTROL_URL = os.environ.get("CONTROL_URL", "http://192.168.1.98:8080").rstrip("/")
FALLBACK_URL = os.environ.get("FALLBACK_URL", "").rstrip("/")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "")
WORKER_SLOTS = max(1, min(32, int(os.environ.get("WORKER_SLOTS", "4"))))
BANDWIDTH_LIMIT_MBPS = float(os.environ.get("BANDWIDTH_LIMIT_MBPS", "50.0"))
BANDWIDTH_LIMIT_BPS = int(BANDWIDTH_LIMIT_MBPS * 1024 * 1024)

# Global stop event
STOP = threading.Event()

# Helpers
def format_bytes(n: float) -> str:
    n = float(n or 0)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PiB"

def format_speed(bps: float) -> str:
    return f"{format_bytes(bps)}/s"

def format_duration(seconds: float) -> str:
    s = int(seconds or 0)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def render_progress_bar(percent: float, width: int = 15) -> str:
    percent = max(0.0, min(100.0, percent or 0.0))
    filled = int(round(width * percent / 100))
    bar = "█" * filled + "░" * (width - filled)
    return bar


class NodeState:
    """Thread-safe state container for node statistics and active jobs."""

    def __init__(self):
        self.lock = threading.Lock()
        self.node_name = NODE_NAME
        self.api_url = CONTROL_URL
        self.max_slots = WORKER_SLOTS
        self.bandwidth_limit_bps = BANDWIDTH_LIMIT_BPS
        self.start_time = time.time()
        
        # Real-time metrics
        self.server_status = "Connecting..."
        self.server_online = False
        self.api_latency_ms = 0.0
        self.last_claim_time = 0.0
        
        # Jobs
        self.active_jobs: Dict[str, dict] = {}
        self.history: collections.deque = collections.deque(maxlen=15)
        
        # Counters
        self.total_completed = 0
        self.total_failed = 0
        self.total_bytes_transferred = 0

    def register_job(self, job: dict):
        with self.lock:
            jid = job["id"]
            source_cfg = job.get("remotes", {}).get(job.get("source_id", ""), {}).get("config", {})
            dest_cfg = job.get("remotes", {}).get(job.get("destination_id", ""), {}).get("config", {})
            
            src_type = source_cfg.get("type", "src")
            dst_type = dest_cfg.get("type", "dst") if job.get("destination_id") else "-"

            checkpoint = (job.get("stats") or {}).get("checkpoint_bytes", 0)
            self.active_jobs[jid] = {
                "id": jid,
                "operation": job.get("operation", "copy"),
                "source": f"[{src_type}] {job.get('source_path') or '/'}",
                "destination": f"[{dst_type}] {job.get('destination_path') or '/'}" if job.get("destination_id") else "-",
                "start_time": time.time(),
                "checkpoint": checkpoint,
                "bytes": checkpoint,
                "total_bytes": checkpoint,
                "percentage": 0.0,
                "speed": 0.0,
                "eta": 0,
                "active_file": "",
                "status": "In esecuzione",
                "assigned_bps": job.get("bandwidth_bps", 0)
            }

    def update_job_progress(self, jid: str, stats: dict, assigned_bps: int = 0):
        with self.lock:
            if jid in self.active_jobs:
                j = self.active_jobs[jid]
                cp = j.get("checkpoint", 0)
                b = stats.get("bytes", 0) + cp
                tb = stats.get("totalBytes", 0) + cp
                speed = stats.get("speed", 0)
                eta = stats.get("eta", 0)
                
                pct = (b / tb * 100.0) if tb > 0 else 0.0
                j["bytes"] = b
                j["total_bytes"] = tb
                j["percentage"] = pct
                j["speed"] = speed
                j["eta"] = eta
                if assigned_bps:
                    j["assigned_bps"] = assigned_bps
                
                # Active file name
                transferring = stats.get("transferring", [])
                if transferring and isinstance(transferring, list):
                    first = transferring[0]
                    if isinstance(first, dict):
                        j["active_file"] = first.get("name", "")

    def finish_job(self, jid: str, success: bool, error: str = ""):
        with self.lock:
            job_info = self.active_jobs.pop(jid, None)
            duration = 0.0
            if job_info:
                duration = time.time() - job_info["start_time"]
                bytes_done = job_info["bytes"]
                self.total_bytes_transferred += bytes_done
                
                self.history.appendleft({
                    "id": jid[:8],
                    "operation": job_info["operation"],
                    "source": job_info["source"],
                    "destination": job_info["destination"],
                    "duration": duration,
                    "bytes": bytes_done,
                    "success": success,
                    "error": error,
                    "finished_at": time.strftime("%H:%M:%S")
                })
            
            if success:
                self.total_completed += 1
            else:
                self.total_failed += 1

    def get_summary(self):
        with self.lock:
            active_count = len(self.active_jobs)
            slots_pct = (active_count / self.max_slots * 100.0) if self.max_slots > 0 else 0.0
            
            current_total_speed = sum(j.get("speed", 0.0) for j in self.active_jobs.values())
            bandwidth_pct = (current_total_speed / self.bandwidth_limit_bps * 100.0) if self.bandwidth_limit_bps > 0 else 0.0
            bandwidth_pct = min(100.0, bandwidth_pct)
            
            return {
                "active_count": active_count,
                "max_slots": self.max_slots,
                "slots_pct": slots_pct,
                "current_speed": current_total_speed,
                "bandwidth_limit": self.bandwidth_limit_bps,
                "bandwidth_pct": bandwidth_pct,
                "total_transferred": self.total_bytes_transferred,
                "completed": self.total_completed,
                "failed": self.total_failed,
                "uptime": time.time() - self.start_time,
                "server_status": self.server_status,
                "server_online": self.server_online,
                "api_latency_ms": self.api_latency_ms,
                "active_jobs": list(self.active_jobs.values()),
                "history": list(self.history)
            }


# Instance of state
state = NodeState()


# Rclone Worker Functions
def obscure(value):
    return subprocess.run(
        ["rclone", "obscure", "-"], input=value, text=True, capture_output=True, check=True, timeout=10
    ).stdout.strip()


def write_config(job, path):
    config = configparser.ConfigParser(interpolation=None)
    for remote_id, data in job["remotes"].items():
        values = dict(data["config"])
        if values.get("type") == "sftp":
            values["known_hosts_file"] = os.environ.get("SFTP_KNOWN_HOSTS", str(Path.home() / ".ssh" / "known_hosts"))
        for key in ["pass", "password"]:
            if key in values:
                values[key] = obscure(values[key])
        config["r" + remote_id] = values
    with path.open("w") as output:
        config.write(output)
    path.chmod(0o600)


def build_rclone_command(job, config, port, log_path):
    source = "r" + job["source_id"] + ":" + job["source_path"]
    common = [
        "--config", str(config),
        "--use-json-log",
        "--log-file", str(log_path),
        "--stats", "1s",
        "--stats-log-level", "NOTICE",
        "--retries", "1",
        "--low-level-retries", "3",
        "--contimeout", "15s",
        "--timeout", "60s",
        "--transfers", "2",
        "--checkers", "4",
        "--check-first",
        "--rc",
        "--rc-addr", f"127.0.0.1:{port}",
        "--bwlimit", str(job.get("bandwidth_bps", state.bandwidth_limit_bps)) + "B",
    ]
    op = job.get("operation", "copy")
    if op == "list":
        return ["rclone", "lsjson", source, "--no-mimetype", "--no-modtime", *common]
    if op == "mkdir":
        return ["rclone", "mkdir", source, *common]
    if op == "delete":
        return ["rclone", "deletefile" if job.get("is_file") else "purge", source, *common]
    
    dest_path = (job.get("destination_path") or "").strip()
    if job.get("is_file"):
        source_name = Path(job.get("source_path", "")).name
        if not dest_path:
            dest_path = source_name
        elif dest_path.endswith("/") or (not dest_path.endswith(source_name) and "." not in Path(dest_path).name):
            dest_path = dest_path.rstrip("/") + "/" + source_name
        op = "copyto" if op == "copy" else "moveto"
    destination = "r" + job["destination_id"] + ":" + dest_path
    return ["rclone", op, source, destination, *common]


def stop_process(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def read_rclone_progress(path: Path) -> dict:
    stats = {}
    if not path.exists():
        return stats
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 65536))
            for line in stream:
                try:
                    record = json.loads(line)
                    if isinstance(record.get("stats"), dict):
                        stats = record["stats"]
                except (ValueError, UnicodeError):
                    pass
    except Exception:
        pass
    return stats


def result_listing(path: Path) -> dict:
    if path.stat().st_size > 8_000_000:
        raise ValueError("Cartella troppo grande: scegli un percorso più specifico")
    data = json.loads(path.read_text() or "[]")
    return {
        "items": [{k: item.get(k) for k in ["Path", "Name", "Size", "IsDir"]} for item in data[:1000]],
        "truncated": len(data) > 1000,
    }


def execute_job(api_url: str, token: str, job: dict):
    """Executes a single rclone task with heartbeat and progress reporting."""
    job_id = job["id"]
    state.register_job(job)

    if job.get("operation") == "configure":
        try:
            from cloudsync.icloud_auth import configure_icloud
            configure_icloud(api_url, token, job, STOP)
            state.finish_job(job_id, True)
            return
        except Exception as e:
            state.finish_job(job_id, False, str(e))
            return

    headers = {"Authorization": "Bearer " + token}
    auth = ("worker", os.urandom(24).hex())
    env = {
        k: v for k, v in os.environ.items()
        if k in {"PATH", "HOME", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "LANG"}
    }
    # Ensure standard bin paths on macOS
    env["PATH"] = f"/usr/local/bin:/usr/bin:/bin:{env.get('PATH', '')}"
    env.update(RCLONE_RC_USER=auth[0], RCLONE_RC_PASS=auth[1])

    with tempfile.TemporaryDirectory(prefix="cloudsync-mac-") as directory, \
         httpx.Client(base_url=api_url, headers=headers, timeout=8) as api:
        
        root = Path(directory)
        config, log_path, output_path = root / "rclone.conf", root / "rclone.log", root / "output.json"
        base = f"/internal/jobs/{job_id}"
        payload = {"lease_token": job["lease_token"], "stats": {}}
        process = None
        cancelled = False
        shutdown = False
        ownership_lost = False
        last_rate_applied = time.monotonic()
        last_renewed = time.monotonic()
        error_msg = ""
        success = False

        try:
            write_config(job, config)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]

            with output_path.open("wb") as output, (root / "stderr").open("wb") as errors:
                cmd = build_rclone_command(job, config, port, log_path)
                process = subprocess.Popen(cmd, stdout=output, stderr=errors, env=env)
                
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", auth=auth, timeout=2) as rc:
                    while True:
                        stats = read_rclone_progress(log_path)
                        payload["stats"] = stats
                        state.update_job_progress(job_id, stats)

                        # Heartbeat
                        try:
                            t0 = time.monotonic()
                            response = api.post(base + "/heartbeat", json=payload)
                            state.api_latency_ms = (time.monotonic() - t0) * 1000
                            response.raise_for_status()
                            control = response.json()
                            last_renewed = time.monotonic()
                            
                            if control.get("cancel") or STOP.is_set():
                                cancelled = bool(control.get("cancel"))
                                shutdown = STOP.is_set() and not cancelled
                                stop_process(process)
                            
                            # Adjust rate limit dynamically
                            if process.poll() is None and "bandwidth_bps" in control:
                                new_bps = control["bandwidth_bps"]
                                state.update_job_progress(job_id, stats, assigned_bps=new_bps)
                                try:
                                    rc.post("/core/bwlimit", json={"rate": f"{new_bps}B"})
                                    last_rate_applied = time.monotonic()
                                except httpx.HTTPError:
                                    pass
                        except httpx.HTTPStatusError as exc:
                            if exc.response.status_code in {401, 403, 409}:
                                ownership_lost = True
                                stop_process(process)
                                break
                        except httpx.HTTPError:
                            pass

                        if time.monotonic() - last_rate_applied > 25 and process.poll() is None:
                            stop_process(process)
                            raise RuntimeError("Bandwidth controller unavailable")
                        if time.monotonic() - last_renewed > 30:
                            ownership_lost = True
                            stop_process(process)
                            break
                        if process.poll() is not None:
                            break

                        STOP.wait(1.0)

            if ownership_lost:
                state.finish_job(job_id, False, "Lease scaduta o revocata")
                return

            if shutdown or (process and process.returncode in {143, -15}):
                try:
                    payload["stats"] = read_rclone_progress(log_path)
                    api.post(base + "/release", json={"stats": payload["stats"], "reason": "Node shutdown/signal"})
                except Exception:
                    pass
                state.finish_job(job_id, True, "Lavoro rilasciato al cluster (nodo interrotto)")
                return

            payload["stats"] = read_rclone_progress(log_path)
            state.update_job_progress(job_id, payload["stats"])
            success = (process.returncode == 0) and not cancelled and not shutdown
            result = result_listing(output_path) if success and job.get("operation") == "list" else {}

            refreshed = {}
            saved = configparser.ConfigParser(interpolation=None)
            saved.read(config)
            for remote_id, data in job["remotes"].items():
                provider = data["config"].get("type")
                keys = ["token"] if provider == "drive" else (["cookies", "trust_token", "client_id"] if provider == "iclouddrive" else [])
                values = {key: saved["r" + remote_id][key] for key in keys if saved.has_option("r" + remote_id, key)}
                if values:
                    refreshed[remote_id] = {"revision": data["revision"], **values}

            if not success and not cancelled:
                err_msgs = []
                if log_path.exists():
                    try:
                        with log_path.open("r", encoding="utf-8", errors="ignore") as lf:
                            for line in lf:
                                try:
                                    rec = json.loads(line)
                                    if rec.get("level") == "error" and rec.get("msg"):
                                        err_msgs.append(rec["msg"])
                                except Exception:
                                    pass
                    except Exception:
                        pass
                if err_msgs:
                    detail = "; ".join(err_msgs[-2:])
                    error_msg = f"Errore Rclone (codice {process.returncode}): {detail}"
                elif (root / "stderr").exists() and (root / "stderr").read_text().strip():
                    error_msg = f"Errore Rclone: {(root / 'stderr').read_text()[:200]}"
                else:
                    error_msg = f"Rclone terminato con codice {process.returncode}. Controlla credenziali e percorso."
            
            completion = {
                **payload,
                "success": success,
                "cancelled": cancelled,
                "error": error_msg,
                "result": result,
                "refreshed": refreshed,
            }
        except Exception as exc:
            if process:
                stop_process(process)
            error_msg = str(exc)
            completion = {
                **payload,
                "success": False,
                "error": f"Errore nodo: {exc}",
            }

        # Complete job with API
        if not ownership_lost:
            for attempt in range(3):
                try:
                    response = api.post(base + "/complete", json=completion)
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt < 2:
                        STOP.wait(1)

        state.finish_job(job_id, success, error_msg)


def worker_loop():
    """Background polling loop claiming jobs from the control plane."""
    urls = [u for u in [CONTROL_URL, FALLBACK_URL] if u]
    current_idx = 0
    token = WORKER_TOKEN
    slots = WORKER_SLOTS

    futures = set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=slots) as pool:
        while not STOP.is_set():
            api_url = urls[current_idx]
            state.api_url = api_url
            futures = {f for f in futures if not f.done()}
            
            if len(futures) < slots:
                try:
                    with httpx.Client(base_url=api_url, headers={"Authorization": "Bearer " + token}, timeout=10) as api:
                        t0 = time.monotonic()
                        response = api.post("/internal/claim")
                        latency = (time.monotonic() - t0) * 1000
                        state.api_latency_ms = latency
                        
                        if response.status_code == 200:
                            state.server_online = True
                            state.server_status = "Online"
                            data = response.json()
                            job = data.get("job")
                            if job:
                                futures.add(pool.submit(execute_job, api_url, token, job))
                                continue
                        else:
                            state.server_online = False
                            state.server_status = f"HTTP {response.status_code}"
                            if len(urls) > 1:
                                current_idx = (current_idx + 1) % len(urls)
                except httpx.HTTPError:
                    state.server_online = False
                    state.server_status = "Disconnesso (riprovo...)"
                    if len(urls) > 1:
                        current_idx = (current_idx + 1) % len(urls)
                except Exception as exc:
                    state.server_online = False
                    state.server_status = f"Errore: {type(exc).__name__}"
                    if len(urls) > 1:
                        current_idx = (current_idx + 1) % len(urls)
            
            STOP.wait(1.5)


# Terminal Dashboard UI Generator
def create_dashboard_layout(data: dict) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="metrics", size=6),
        Layout(name="jobs", ratio=1),
        Layout(name="history", size=9),
        Layout(name="footer", size=1)
    )

    # 1. Header
    status_style = "bold green" if data["server_online"] else "bold red"
    uptime_str = format_duration(data["uptime"])
    header_text = Text()
    header_text.append(" ◈ CloudSync Distributed Worker ", style="bold white on #0284c7")
    header_text.append(f"  Nodo: ", style="dim")
    header_text.append(f"{data['active_count']}/{data['max_slots']} ", style="bold cyan")
    header_text.append(f"[{NODE_NAME}]  ", style="bold white")
    
    is_public = "trycloudflare" in state.api_url or not state.api_url.startswith("http://192.168")
    mode_label = "Pubblico 🌍" if is_public else "LAN 🏠"
    header_text.append(f"API ({mode_label}): ", style="dim")
    header_text.append(f"{state.api_url} ", style="underline cyan")
    header_text.append(f"({data['server_status']} · {data['api_latency_ms']:.1f}ms)  ", style=status_style)
    header_text.append("Uptime: ", style="dim")
    header_text.append(uptime_str, style="yellow")
    
    layout["header"].update(Panel(header_text, style="cyan", border_style="bright_blue"))

    # 2. Metrics Cards
    metrics_table = Table.grid(expand=True)
    metrics_table.add_column(ratio=1)
    metrics_table.add_column(ratio=1)
    metrics_table.add_column(ratio=1)
    metrics_table.add_column(ratio=1)

    # Slot metric
    slots_bar = render_progress_bar(data["slots_pct"], 12)
    p_slots = Panel(
        f"[bold white]{data['active_count']} / {data['max_slots']}[/] slots attivi\n"
        f"[{'cyan' if data['slots_pct'] < 80 else 'yellow'}]{slots_bar}[/] [bold]{data['slots_pct']:.0f}%[/]",
        title="[bold cyan]⚡ Worker Slots[/]",
        border_style="cyan"
    )

    # Bandwidth metric
    bw_bar = render_progress_bar(data["bandwidth_pct"], 12)
    cur_speed_str = format_speed(data["current_speed"])
    max_bw_str = format_speed(data["bandwidth_limit"])
    p_bw = Panel(
        f"[bold green]{cur_speed_str}[/] / [dim]{max_bw_str}[/]\n"
        f"[{'green' if data['bandwidth_pct'] < 85 else 'red'}]{bw_bar}[/] [bold]{data['bandwidth_pct']:.1f}%[/]",
        title="[bold green]🌐 Banda Nodo[/]",
        border_style="green"
    )

    # Transferred metric
    total_tf_str = format_bytes(data["total_transferred"])
    p_tf = Panel(
        f"[bold magenta]{total_tf_str}[/] trasferiti\n"
        f"[dim]Completati:[/] [green]{data['completed']}[/] | [dim]Errori:[/] [red]{data['failed']}[/]",
        title="[bold magenta]📦 Dati Sessione[/]",
        border_style="magenta"
    )

    # Health & status metric
    p_status = Panel(
        f"Stato: [{status_style}]{data['server_status']}[/]\n"
        f"Latenza: [yellow]{data['api_latency_ms']:.1f} ms[/] | Max: [dim]{WORKER_SLOTS} thread[/]",
        title="[bold yellow]📡 Connessione Cluster[/]",
        border_style="yellow"
    )

    metrics_table.add_row(p_slots, p_bw, p_tf, p_status)
    layout["metrics"].update(metrics_table)

    # 3. Active Jobs Table
    jobs = data["active_jobs"]
    if jobs:
        table = Table(expand=True, box=None, header_style="bold cyan", border_style="dim")
        table.add_column("ID", width=10, style="dim")
        table.add_column("Tipo", width=8, style="bold")
        table.add_column("Sorgente ➔ Destinazione", ratio=2)
        table.add_column("Progresso", width=22)
        table.add_column("Dati", width=18, justify="right")
        table.add_column("Velocità", width=12, justify="right", style="green")
        table.add_column("ETA", width=10, justify="right", style="yellow")

        for j in jobs:
            op_style = "bold cyan" if j["operation"] == "copy" else "bold yellow"
            p_bar = render_progress_bar(j["percentage"], 10)
            prog_text = f"[{p_bar}] {j['percentage']:.1f}%"
            bytes_text = f"{format_bytes(j['bytes'])} / {format_bytes(j['total_bytes'])}"
            speed_text = format_speed(j["speed"])
            eta_text = format_duration(j["eta"]) if j["eta"] else "--:--"

            route_text = Text()
            route_text.append(j["source"], style="white")
            route_text.append(" ➔ ", style="bold cyan")
            route_text.append(j["destination"], style="white")
            if j.get("active_file"):
                route_text.append(f"\n↳ {j['active_file'][:40]}", style="dim italic")

            table.add_row(
                j["id"][:8],
                f"[{op_style}]{j['operation'].upper()}[/]",
                route_text,
                prog_text,
                bytes_text,
                speed_text,
                eta_text
            )

        layout["jobs"].update(Panel(table, title=f"[bold cyan]🚀 Processi Attivi ({len(jobs)})[/]", border_style="cyan"))
    else:
        empty_text = Text("\n💤 In attesa di nuovi lavori dalla coda di CloudSync...\n", justify="center", style="dim italic")
        empty_text.append(f"Il nodo è online e pronto su {CONTROL_URL}\n", style="dim")
        layout["jobs"].update(Panel(empty_text, title="[bold cyan]🚀 Processi Attivi (0)[/]", border_style="dim"))

    # 4. History Table
    history = data["history"]
    hist_table = Table(expand=True, box=None, header_style="bold dim", border_style="dim")
    hist_table.add_column("Ora", width=10, style="dim")
    hist_table.add_column("ID", width=10, style="dim")
    hist_table.add_column("Tipo", width=8)
    hist_table.add_column("Percorso", ratio=2)
    hist_table.add_column("Dimensione", width=14, justify="right")
    hist_table.add_column("Durata", width=10, justify="right")
    hist_table.add_column("Esito", width=14, justify="center")

    if history:
        for h in history[:6]:
            res_str = "[bold green]✔ COMPLETATO[/]" if h["success"] else f"[bold red]✖ FALLITO[/]"
            hist_table.add_row(
                h["finished_at"],
                h["id"],
                h["operation"].upper(),
                f"{h['source']} ➔ {h['destination']}",
                format_bytes(h["bytes"]),
                format_duration(h["duration"]),
                res_str
            )
    else:
        hist_table.add_row("--:--:--", "-", "-", "Nessun trasferimento completato in questa sessione", "0 B", "00:00", "[dim]In attesa[/]")

    layout["history"].update(Panel(hist_table, title="[bold white]📋 Storico Trasferimenti Recenti[/]", border_style="dim"))

    # 5. Footer
    footer = Text(" Premi Ctrl+C per arrestare il worker in modo sicuro | CloudSync v0.2 Distributed Node", style="dim white on #1e293b", justify="center")
    layout["footer"].update(footer)

    return layout


def main():
    if not WORKER_TOKEN:
        print("ERRORE: WORKER_TOKEN non configurato in .env!")
        sys.exit(1)

    # Check rclone
    try:
        rclone_res = subprocess.run(["rclone", "version"], capture_output=True, text=True, check=True)
        rclone_ver = rclone_res.stdout.splitlines()[0]
    except Exception as e:
        print(f"ERRORE: rclone non trovato nel PATH: {e}")
        sys.exit(1)

    # Handle termination signals
    def handle_sig(sig, frame):
        STOP.set()

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    # Start worker background thread
    t = threading.Thread(target=worker_loop, daemon=True, name="WorkerThread")
    t.start()

    console = Console()
    try:
        with Live(create_dashboard_layout(state.get_summary()), console=console, screen=True, refresh_per_second=2) as live:
            while not STOP.is_set():
                live.update(create_dashboard_layout(state.get_summary()))
                STOP.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        console.print("[bold yellow]\nArresto del nodo in corso... Chiusura processi e rilascio risorse.[/]")
        time.sleep(1.0)
        console.print("[bold green]Nodo arrestato con successo.[/]\n")


if __name__ == "__main__":
    main()
