"""FastAPI application: WebUI + API + static files."""

from __future__ import annotations

import logging
import os
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from webui.config import ensure_data_layout, load_config, save_config
from webui.job_manager import job_manager
from webui.routes import api, pages
from webui.services.cleanup import run_cleanup
from webui.single_instance import acquire_or_exit, release
from webui.worker import worker_service

log = logging.getLogger("ultrasinger.webui")


def _browser_base_url(host: str, port: int) -> str:
    """Use a loopback URL in the browser when the server binds all interfaces."""
    h = host.strip()
    if h in ("0.0.0.0", ""):
        h = "127.0.0.1"
    elif h == "::":
        h = "[::1]"
    return f"http://{h}:{int(port)}/"


def _schedule_open_browser() -> None:
    if os.environ.get("WEBUI_NO_BROWSER", "").strip().lower() in ("1", "true", "yes"):
        return
    cfg = load_config()
    if not cfg.open_browser_on_start:
        return
    url = _browser_base_url(cfg.host, cfg.port)

    def open_it() -> None:
        try:
            webbrowser.open(url)
        except Exception:
            log.debug("webbrowser.open failed", exc_info=True)

    threading.Timer(0.4, open_it).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = load_config()
    acquire_or_exit()
    try:
        if cfg.debug_logging:
            logging.basicConfig(
                level=logging.DEBUG,
                format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                force=True,
            )
        else:
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                force=True,
            )

        ensure_data_layout(cfg)
        if not cfg.config_path().is_file():
            save_config(cfg)

        _keep_jobs = os.environ.get("ULTRASINGER_WEBUI_KEEP_JOBS", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if not _keep_jobs:
            job_manager.wipe_jobs_dir_and_reset()

        if cfg.cleanup_on_startup:
            run_cleanup(cfg)

        stop_cleanup = threading.Event()

        def cleanup_loop() -> None:
            while not stop_cleanup.wait(max(1, load_config().cleanup_interval_hours) * 3600):
                try:
                    run_cleanup(load_config())
                except Exception:
                    log.exception("scheduled cleanup failed")

        cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True, name="WebUICleanup")
        cleanup_thread.start()

        worker_service.start()
        log.info("UltraSinger WebUI worker started")
        _schedule_open_browser()
        yield
        stop_cleanup.set()
        worker_service.stop()
        log.info("UltraSinger WebUI shutdown complete")
    finally:
        release()


app = FastAPI(title="UltraSinger WebUI", lifespan=lifespan)

_static = Path(__file__).resolve().parent / "static"
_static.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static)), name="static")

app.include_router(api.router)
app.include_router(pages.router)
