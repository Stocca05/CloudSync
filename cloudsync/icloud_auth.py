"""Per-job iCloud configuration conversation on a private, authenticated Rclone RC daemon.
Apple credentials stay inside the worker; 2FA answers are encrypted by the API.
"""

import concurrent.futures
import os
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx


class Conversation:
    def __init__(self, name, parameters):
        self.name = name
        self.parameters = parameters
        self.state = ""
        self.challenge = None

    def initial(self):
        return "config/create", {
            "name": self.name,
            "type": "iclouddrive",
            "parameters": self.parameters,
            "opt": {"nonInteractive": True, "obscure": True},
        }

    def receive(self, response):
        self.state = response.get("State", "")
        if not self.state:
            self.challenge = None
            return None
        option = response.get("Option") or {}
        self.challenge = {
            "id": secrets.token_hex(16),
            "name": option.get("Name", "Verifica Apple"),
            "help": option.get("Help", "Inserisci il codice ricevuto sul tuo dispositivo Apple."),
            "secret": True,
            "examples": [
                {"value": str(e.get("Value", "")), "label": str(e.get("Help", ""))}
                for e in option.get("Examples", [])
            ],
        }
        if response.get("Error"):
            self.challenge["help"] = "Apple non ha accettato la risposta. " + self.challenge["help"]
        return self.challenge

    def answer(self, value):
        return "config/update", {
            "name": self.name,
            "parameters": self.parameters,
            "opt": {"nonInteractive": True, "continue": True, "state": self.state, "result": value},
        }


def configure_icloud(api_url, token, job, stop):
    from .worker import stop_process

    remote_id = job["source_id"]
    original = job["remotes"][remote_id]
    params = {key: value for key, value in original["config"].items() if key != "type"}
    conversation = Conversation("r" + remote_id, params)
    base = f"/internal/jobs/{job['id']}"
    payload = {"lease_token": job["lease_token"], "stats": {}, "challenge": {}}
    completion = {
        "lease_token": job["lease_token"],
        "success": False,
        "error": "Connessione iCloud non completata. Verifica Apple ID, password e codice 2FA e riprova.",
    }
    process = None
    ownership_lost = False
    auth = ("worker", secrets.token_hex(24))
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "LANG"}
    }
    env.update(RCLONE_RC_USER=auth[0], RCLONE_RC_PASS=auth[1])
    with tempfile.TemporaryDirectory(prefix="cloudsync-auth-") as directory:
        root = Path(directory)
        config = root / "rclone.conf"
        config.touch(mode=0o600)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with httpx.Client(base_url=api_url, headers={"Authorization": "Bearer " + token}, timeout=8) as api:
            try:
                process = subprocess.Popen(
                    [
                        "rclone",
                        "rcd",
                        "--rc-addr",
                        f"127.0.0.1:{port}",
                        "--config",
                        str(config),
                        "--contimeout",
                        "15s",
                        "--timeout",
                        "60s",
                    ],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", auth=auth, timeout=65) as rc:
                    for _ in range(40):
                        try:
                            rc.post("/core/version", timeout=1).raise_for_status()
                            break
                        except httpx.HTTPError:
                            if stop.wait(0.25):
                                raise RuntimeError("Worker stopped")
                    else:
                        raise RuntimeError("Rclone not ready")

                    def rpc(endpoint, values):
                        response = rc.post("/" + endpoint, json=values)
                        response.raise_for_status()
                        return response.json()

                    last_renewed = time.monotonic()
                    deadline = last_renewed + 600
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(rpc, *conversation.initial())
                        while time.monotonic() < deadline:
                            try:
                                heartbeat = api.post(base + "/heartbeat", json=payload)
                                heartbeat.raise_for_status()
                                control = heartbeat.json()
                                last_renewed = time.monotonic()
                            except httpx.HTTPStatusError as exc:
                                if exc.response.status_code in {401, 403, 409}:
                                    ownership_lost = True
                                    stop_process(process)
                                    break
                                control = {}
                            except httpx.HTTPError:
                                control = {}
                            if time.monotonic() - last_renewed > 25:
                                ownership_lost = True
                                stop_process(process)
                                break
                            if control.get("cancel") or stop.is_set():
                                completion["cancelled"] = bool(control.get("cancel"))
                                stop_process(process)
                                break
                            if future and future.done():
                                question = conversation.receive(future.result())
                                future = None
                                payload["challenge"] = question or {}
                                if question is None:
                                    saved = rpc("config/get", {"name": conversation.name})
                                    if not saved.get("trust_token"):
                                        raise RuntimeError("Apple did not return a trust token")
                                    refreshed = {
                                        key: saved[key]
                                        for key in ["cookies", "trust_token", "client_id"]
                                        if key in saved
                                    }
                                    completion = {
                                        "lease_token": job["lease_token"],
                                        "success": True,
                                        "result": {"connected": True},
                                        "refreshed": {
                                            remote_id: {"revision": original["revision"], **refreshed}
                                        },
                                    }
                                    break
                            answer = control.get("answer")
                            if (
                                answer
                                and future is None
                                and conversation.challenge
                                and answer["challenge_id"] == conversation.challenge["id"]
                            ):
                                payload["ack_answer"] = answer["id"]
                                payload["challenge"] = {}
                                future = pool.submit(rpc, *conversation.answer(answer["answer"]))
                            stop.wait(2)
                        else:
                            completion["error"] = (
                                "Tempo scaduto: riavvia la connessione iCloud e inserisci il codice entro 10 minuti."
                            )
                            stop_process(process)
            except (httpx.HTTPError, RuntimeError, OSError, ValueError):
                # Provider responses can contain secrets: never expose raw exceptions.
                pass
            finally:
                if process:
                    stop_process(process)
            if not ownership_lost:
                for attempt in range(3):
                    try:
                        api.post(base + "/complete", json=completion).raise_for_status()
                        break
                    except httpx.HTTPError:
                        if attempt < 2:
                            stop.wait(2)
