"""Optional desktop tray integration (via pystray)."""

from __future__ import annotations

import os
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from PIL import Image, ImageDraw, ImageFont

from webui.config import WebUIConfig, load_config
from webui.job_manager import job_manager

try:
    import pystray
    from pystray import MenuItem as item
except ImportError:
    pystray = None  # type: ignore[assignment]


def _static_icon_ico() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / "assets" / "icon.ico"


def _icon_image() -> "Image.Image":
    ico = _static_icon_ico()
    if ico.is_file():
        try:
            return Image.open(ico).convert("RGBA")
        except Exception:
            # Fallback: PIL may not be able to open .ico on some platforms/builds.
            pass
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((4, 4, size - 4, size - 4), radius=14, fill=(110, 181, 255, 255))
    font = ImageFont.load_default()
    draw.text((20, 14), "U", fill=(12, 14, 20, 255), font=font)
    return img


def run_tray_app(cfg: WebUIConfig | None = None) -> None:
    if pystray is None:
        raise ImportError("pystray is not installed")

    cfg = cfg or load_config()
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    os.chdir(root)
    if root not in sys.path:
        sys.path.insert(0, root)

    host = (cfg.host or "").strip()
    if host in ("0.0.0.0", ""):
        host = "127.0.0.1"
    elif host == "::":
        host = "[::1]"
    base_url = f"http://{host}:{int(cfg.port)}"
    jobs_url = f"{base_url}/jobs"

    def serve() -> None:
        uvicorn.run(
            "webui.app:app",
            host=cfg.host,
            port=cfg.port,
            log_level="warning",
        )

    server_thread = threading.Thread(target=serve, daemon=True, name="UvicornTray")
    server_thread.start()

    def on_open() -> None:
        webbrowser.open(jobs_url)

    def on_stop() -> None:
        job_manager.stop_all()

    def on_resume() -> None:
        job_manager.resume_processing()

    icon = pystray.Icon(
        "ultrasinger_webui",
        _icon_image(),
        "UltraSinger WebUI",
        menu=None,
    )

    def quit_app() -> None:
        icon.stop()
        os._exit(0)  # noqa: S404 — terminate daemon uvicorn thread

    icon.menu = pystray.Menu(
        item("Open WebUI", lambda: webbrowser.open(base_url)),
        item("Jobs", on_open),
        item("Stop All", on_stop),
        item("Resume", on_resume),
        pystray.Menu.SEPARATOR,
        item("Exit", quit_app),
    )
    icon.run()
