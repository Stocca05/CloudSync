"""Google web authorization: single-use state bound to the signed-in session."""

import base64
import hashlib
import json
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import GoogleAuthorization, LoginSession, Remote, User
from .security import digest


class RcloneToken(BaseModel):
    token: dict


class GoogleStart(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def install_google_routes(app, settings, vault, get_db, current_user):
    def configured():
        url = urlsplit(settings.public_url)
        return bool(
            settings.google_client_id
            and settings.google_client_secret
            and url.scheme == "https"
            and url.netloc
            and not url.query
            and not url.fragment
            and not url.username
        )

    @app.get("/api/google/status")
    def status(user=Depends(current_user)):
        return {"configured": True, "method": "web" if configured() else "rclone"}

    @app.get("/api/google/helper")
    def helper(user=Depends(current_user)):
        site = settings.public_url.rstrip("/")
        if not site.startswith("https://"):
            raise HTTPException(503, "L’amministratore deve impostare l’indirizzo pubblico del servizio")
        source = (Path(__file__).resolve().parents[1] / "scripts" / "google_helper.py").read_text()
        source = source.replace(
            'parser.add_argument("--site", required=True)',
            'parser.add_argument("--site", default=' + repr(site) + ")",
        )
        return Response(
            source,
            media_type="text/x-python",
            headers={"Content-Disposition": 'attachment; filename="CloudSync-Google.py"'},
        )

    @app.post("/api/google/start")
    def start(data: GoogleStart, request: Request, user=Depends(current_user), db: Session = Depends(get_db)):
        if (
            db.scalar(
                select(func.count())
                .select_from(Remote)
                .where(Remote.user_id == user.id, Remote.enabled.is_(True))
            )
            >= 30
        ):
            raise HTTPException(409, "Limite di collegamenti raggiunto")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        if not configured():
            db.execute(
                delete(GoogleAuthorization).where(
                    (GoogleAuthorization.expires < time.time())
                    | (GoogleAuthorization.session_hash == digest(request.cookies.get("session", "")))
                )
            )
            db.add(
                GoogleAuthorization(
                    state_hash=digest(state),
                    user_id=user.id,
                    session_hash=digest(request.cookies.get("session", "")),
                    name=data.name,
                    encrypted_data=vault.encrypt({"mode": "rclone"}),
                    expires=time.time() + 600,
                )
            )
            db.commit()
            return {
                "url": "http://127.0.0.1:53683/connect?" + urlencode({"ticket": state}),
                "method": "rclone",
            }
        db.execute(
            delete(GoogleAuthorization).where(
                (GoogleAuthorization.expires < time.time())
                | (GoogleAuthorization.session_hash == digest(request.cookies.get("session", "")))
            )
        )
        redirect = settings.public_url.rstrip("/") + "/api/google/callback"
        db.add(
            GoogleAuthorization(
                state_hash=digest(state),
                user_id=user.id,
                session_hash=digest(request.cookies.get("session", "")),
                name=data.name,
                encrypted_data=vault.encrypt({"verifier": verifier, "redirect": redirect}),
                expires=time.time() + 600,
            )
        )
        db.commit()
        return {
            "url": "https://accounts.google.com/o/oauth2/v2/auth?"
            + urlencode(
                {
                    "client_id": settings.google_client_id,
                    "redirect_uri": redirect,
                    "response_type": "code",
                    "scope": "https://www.googleapis.com/auth/drive",
                    "access_type": "offline",
                    "prompt": "consent select_account",
                    "state": state,
                    "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                    .rstrip(b"=")
                    .decode(),
                    "code_challenge_method": "S256",
                }
            )
        }

    @app.get("/api/google/rclone/check")
    def rclone_check(request: Request, db: Session = Depends(get_db)):
        ticket = request.headers.get("authorization", "").removeprefix("Bearer ")
        pending = db.get(GoogleAuthorization, digest(ticket))
        session = db.get(LoginSession, pending.session_hash) if pending else None
        user = db.get(User, pending.user_id) if pending else None
        if (
            not pending
            or pending.expires < time.time()
            or not session
            or session.expires < time.time()
            or not user
            or not user.enabled
            or vault.decrypt(pending.encrypted_data).get("mode") != "rclone"
        ):
            raise HTTPException(401, "Autorizzazione scaduta")
        return {"username": user.username, "name": pending.name}

    @app.post("/api/google/rclone/complete")
    def rclone_complete(data: RcloneToken, request: Request, db: Session = Depends(get_db)):
        ticket = request.headers.get("authorization", "").removeprefix("Bearer ")
        pending = db.get(GoogleAuthorization, digest(ticket))
        session = db.get(LoginSession, pending.session_hash) if pending else None
        user = db.get(User, pending.user_id) if pending else None
        if (
            not pending
            or pending.expires < time.time()
            or not session
            or session.expires < time.time()
            or not user
            or not user.enabled
            or vault.decrypt(pending.encrypted_data).get("mode") != "rclone"
        ):
            raise HTTPException(401, "Autorizzazione scaduta. Riparti dal sito.")
        token = {
            key: data.token[key]
            for key in ["access_token", "refresh_token", "token_type", "expiry", "expires_in"]
            if key in data.token
        }
        if not all(
            isinstance(token.get(key), str) and token[key] for key in ["access_token", "refresh_token"]
        ):
            raise HTTPException(400, "Autorizzazione Google incompleta")
        consumed = db.execute(
            delete(GoogleAuthorization)
            .where(GoogleAuthorization.state_hash == pending.state_hash)
            .returning(GoogleAuthorization.state_hash)
        ).scalar_one_or_none()
        if not consumed:
            raise HTTPException(409, "Autorizzazione già usata")
        db.add(
            Remote(
                user_id=user.id,
                name=pending.name,
                provider="drive",
                encrypted_config=vault.encrypt(
                    {"type": "drive", "scope": "drive", "token": json.dumps(token)}
                ),
            )
        )
        db.commit()
        return {"ok": True}

    @app.get("/api/google/callback")
    def callback(
        request: Request,
        state: str = "",
        code: str = "",
        error: str = "",
        user=Depends(current_user),
        db: Session = Depends(get_db),
    ):
        pending = db.execute(
            delete(GoogleAuthorization)
            .where(
                GoogleAuthorization.state_hash == digest(state),
                GoogleAuthorization.user_id == user.id,
                GoogleAuthorization.session_hash == digest(request.cookies.get("session", "")),
                GoogleAuthorization.expires > time.time(),
            )
            .returning(GoogleAuthorization)
        ).scalar_one_or_none()
        if not pending:
            return RedirectResponse("/?google=expired", status_code=303)
        payload = vault.decrypt(pending.encrypted_data)
        name = pending.name
        db.commit()  # Consume before exchanging; another callback cannot replay this state.
        if error or not code:
            return RedirectResponse("/?google=cancelled", status_code=303)
        try:
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": settings.google_client_id,
                        "client_secret": settings.google_client_secret,
                        "code": code,
                        "grant_type": "authorization_code",
                        "redirect_uri": payload["redirect"],
                        "code_verifier": payload["verifier"],
                    },
                )
                response.raise_for_status()
                token = response.json()
            if not token.get("access_token") or not token.get("refresh_token"):
                raise ValueError("Missing offline authorization")
            token["expiry"] = datetime.fromtimestamp(
                time.time() + int(token.get("expires_in", 3600)), timezone.utc
            ).isoformat()
            config = {
                "type": "drive",
                "scope": "drive",
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "token": json.dumps(token),
            }
            db.add(
                Remote(user_id=user.id, name=name, provider="drive", encrypted_config=vault.encrypt(config))
            )
            db.commit()
        except (httpx.HTTPError, ValueError, TypeError):
            return RedirectResponse("/?google=failed", status_code=303)
        return RedirectResponse("/?google=connected", status_code=303)
