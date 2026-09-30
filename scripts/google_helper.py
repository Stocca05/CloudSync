#!/usr/bin/env python3
"""Local Rclone Google bridge. No tokens on disk or in logs. Python standard library only."""

import argparse
import html
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCAL = "http://127.0.0.1:53683"


def extract_token(output):
    # Rclone emits the token between its documented paste markers.
    match = re.search(r"--->\s*(.*?)\s*<---", output, re.S)
    if not match:
        raise ValueError("Rclone non ha restituito l’autorizzazione")
    token = json.loads(match.group(1))
    if not token.get("access_token") or not token.get("refresh_token"):
        raise ValueError("Autorizzazione incompleta")
    return token


def serve(site, rclone):
    active = threading.Lock()
    pending = {}
    pending_lock = threading.Lock()

    def call(path, ticket, body=None):
        headers = {
            "Authorization": "Bearer " + ticket,
            "X-CloudSync-Request": "1",
            "Content-Type": "application/json",
        }
        request = urllib.request.Request(
            site + path, data=json.dumps(body).encode() if body is not None else None, headers=headers
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.load(response)

    def authorize(ticket, ready):
        process = None
        timer = None
        try:
            env = {
                key: value
                for key, value in os.environ.items()
                if key in {"PATH", "HOME", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "LANG"}
            }
            process = subprocess.Popen(
                [rclone, "authorize", "drive", "--auth-no-open-browser", "--config", os.devnull],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            timer = threading.Timer(540, process.terminate)
            timer.start()
            output = []
            announced = False
            for line in process.stdout:
                output.append(line)
                match = re.search(r"http://127\.0\.0\.1:53682/auth\?[^\s]+", line)
                if match and not announced:
                    ready.put(match.group(0))
                    announced = True
                if sum(map(len, output)) > 100000:
                    raise ValueError("Risposta Rclone troppo grande")
            if process.wait() != 0:
                raise ValueError("Rclone non ha completato l’accesso")
            token = extract_token("".join(output))
            call("/api/google/rclone/complete", ticket, {"token": token})
            webbrowser.open(site + "/?google=connected")
        except Exception:
            # Do not log raw OAuth errors: they may contain tokens and codes.
            if ready.empty():
                ready.put(None)
            webbrowser.open(site + "/?google=failed")
        finally:
            if timer:
                timer.cancel()
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            active.release()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def page(self, message, form=""):
            body = (
                '<!doctype html><html lang="it"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Collega Google a CloudSync</title><h1>Collega Google a CloudSync</h1><p>'
                + html.escape(message)
                + "</p>"
                + form
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get("Host") == "127.0.0.1:53683"

        def do_GET(self):
            if not self.valid_host():
                self.send_error(403)
                return
            url = urllib.parse.urlsplit(self.path)
            if url.path != "/connect":
                self.page("Assistente Rclone pronto. Inizia da Collegamenti sul sito CloudSync.")
                return
            ticket = urllib.parse.parse_qs(url.query).get("ticket", [""])[0]
            if not re.fullmatch(r"[A-Za-z0-9_-]{40,100}", ticket):
                self.send_error(400)
                return
            try:
                owner = call("/api/google/rclone/check", ticket)
            except Exception:
                self.page("Richiesta scaduta o servizio non raggiungibile. Riparti dal sito CloudSync.")
                return
            nonce = secrets.token_urlsafe(32)
            with pending_lock:
                for key in list(pending):
                    if pending[key][1] < time.monotonic():
                        del pending[key]
                if len(pending) >= 20:
                    self.send_error(429)
                    return
                pending[nonce] = (ticket, time.monotonic() + 120)
            self.page(
                "Stai collegando Google all’utente "
                + owner["username"]
                + " su "
                + site
                + ". Collegamento: "
                + owner["name"]
                + ". Conferma solo se è il tuo account CloudSync.",
                '<form method="post" action="/authorize"><input type="hidden" name="nonce" value="'
                + nonce
                + '"><button>Continua con Google tramite Rclone</button></form>',
            )

        def do_POST(self):
            if not self.valid_host() or self.path != "/authorize" or self.headers.get("Origin") != LOCAL:
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if not 0 < length < 1000:
                self.send_error(400)
                return
            nonce = urllib.parse.parse_qs(self.rfile.read(length).decode()).get("nonce", [""])[0]
            with pending_lock:
                item = pending.pop(nonce, None)
            if not item or item[1] < time.monotonic():
                self.page("Conferma scaduta. Riparti dal sito.")
                return
            if not active.acquire(blocking=False):
                self.page(
                    "Un accesso Google è già in corso su questo computer. Completalo prima di riprovare."
                )
                return
            ready = queue.Queue()
            threading.Thread(target=authorize, args=(item[0], ready), daemon=True).start()
            try:
                url = ready.get(timeout=25)
            except queue.Empty:
                url = None
            if not url:
                self.page(
                    "Rclone non ha avviato l’accesso. Controlla che la porta 53682 sia libera e riprova."
                )
                return
            self.send_response(303)
            self.send_header("Location", url)
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()

    ThreadingHTTPServer(("127.0.0.1", 53683), Handler).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True)
    args = parser.parse_args()
    parsed = urllib.parse.urlsplit(args.site)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        parser.error("Usa l’indirizzo HTTPS del tuo sito CloudSync")
    rclone = shutil.which("rclone")
    if not rclone:
        parser.error("Installa Rclone da https://rclone.org/install/ e riavvia l’assistente")
    serve(args.site.rstrip("/"), rclone)
