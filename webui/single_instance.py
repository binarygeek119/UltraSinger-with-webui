"""Single-process lock so only one WebUI server can run at a time."""

from __future__ import annotations

import os
import socket
import sys
from typing import Optional

_lock_sock: Optional[socket.socket] = None

# Loopback port used only for mutual exclusion (not HTTP). Fixed so two WebUIs cannot run on different --port.
_LOCK_PORT = 50987


def acquire_or_exit() -> None:
    """Bind a localhost TCP port exclusively; exit if another instance already holds it."""
    global _lock_sock
    if os.environ.get("WEBUI_ALLOW_MULTI_INSTANCE", "").strip() in ("1", "true", "yes"):
        return
    if _lock_sock is not None:
        return
    port = _LOCK_PORT
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        s.close()
        print(
            "UltraSinger WebUI is already running. Only one instance is allowed at a time.",
            file=sys.stderr,
        )
        sys.exit(1)
    _lock_sock = s


def release() -> None:
    global _lock_sock
    if _lock_sock is not None:
        try:
            _lock_sock.close()
        except OSError:
            pass
        _lock_sock = None
