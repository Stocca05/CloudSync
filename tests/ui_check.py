"""Browser smoke test on an isolated, disposable app. Run: uv run python tests/ui_check.py."""

import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

from cloudsync.api import create_app
from cloudsync.settings import Settings

with tempfile.TemporaryDirectory() as tmp:
    app = create_app(
        Settings(
            database_url="sqlite:///" + tmp + "/ui.db",
            encryption_key=Fernet.generate_key().decode(),
            admin_password="preview",
            cookie_secure=False,
        )
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    artifacts = Path("artifacts")
    artifacts.mkdir(exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{port}")
            page.locator("#login-form input[name=username]").fill("admin")
            page.locator("#login-form input[name=password]").fill("preview")
            page.get_by_role("button", name="Accedi").click()
            page.locator("#shell").wait_for(state="visible")
            page.screenshot(path=str(artifacts / "dashboard-desktop.png"), full_page=True)
            page.locator("[data-page=remotes]").click()
            page.locator("#add-remote").click()
            page.locator("#remote-form input[name=name]").fill("Drive personale")
            page.locator("#provider").select_option("drive")
            page.locator("#provider-fields textarea").fill(
                '{"access_token":"ui-fixture","refresh_token":"ui-fixture"}'
            )
            page.get_by_role("button", name="Salva collegamento").click()
            page.locator("#remote-dialog").wait_for(state="hidden")
            assert page.locator("#remotes").inner_text().find("Drive personale") >= 0
            page.locator("[data-page=admin]").click()
            page.locator("#global-bandwidth").fill("30")
            page.get_by_role("button", name="Salva limiti").click()
            page.locator("#add-node").click()
            page.locator("#node-form input[name=name]").fill("nodo-02")
            page.get_by_role("button", name="Crea nodo", exact=True).click()
            page.locator("#node-token").wait_for(state="visible")
            assert len(page.locator("#node-token textarea").input_value()) > 30
            page.locator("[data-close=node-dialog]").click()
            page.screenshot(path=str(artifacts / "cluster-desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator("[data-page=overview]").click()
            page.screenshot(path=str(artifacts / "dashboard-mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Horizontal overflow"
            assert not errors, errors
            browser.close()
        print(
            "Browser smoke passed: login, remote creation, cluster limits, node enrollment, mobile layout; no JavaScript errors."
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)
