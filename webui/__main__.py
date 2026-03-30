"""Run UltraSinger WebUI: `python -m webui` from the repository root."""

from __future__ import annotations

import os
import sys


def main() -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    os.chdir(root)
    if root not in sys.path:
        sys.path.insert(0, root)

    try:
        import uvicorn
    except ModuleNotFoundError:
        print(
            "UltraSinger WebUI needs the optional Web UI packages (uvicorn, fastapi, …).\n"
            "From the repository root, run:\n"
            "  install_webui.bat              (Windows)\n"
            "  ./install_webui_linux.sh     (Linux)\n"
            "  ./install_webui_macos.sh     (macOS)\n"
            "Or with this same Python/venv active:\n"
            '  pip install -e ".[webui]"',
            file=sys.stderr,
        )
        sys.exit(1)

    from webui.config import load_config

    cfg = load_config()

    if cfg.tray_enabled:
        try:
            from webui.tray import run_tray_app

            run_tray_app(cfg)
            return
        except ImportError as e:
            print("Tray mode requires pystray and Pillow: pip install pystray pillow", file=sys.stderr)
            print(e, file=sys.stderr)

    uvicorn.run(
        "webui.app:app",
        host=cfg.host,
        port=cfg.port,
        log_level="debug" if cfg.debug_logging else "info",
    )


if __name__ == "__main__":
    main()
