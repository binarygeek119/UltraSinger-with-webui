"""Remove stale job folders based on retention policy."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from webui.config import WebUIConfig, load_config

log = logging.getLogger("ultrasinger.webui.cleanup")


def run_cleanup(cfg: WebUIConfig | None = None) -> int:
    cfg = cfg or load_config()
    root = cfg.jobs_dir()
    if not root.is_dir():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, cfg.job_retention_hours))
    removed = 0
    for p in root.iterdir():
        if not p.is_dir():
            continue
        jf = p / "job.json"
        if not jf.is_file():
            continue
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            st = data.get("status")
            if st in ("running", "queued"):
                continue
            completed = data.get("completed_at") or data.get("created_at")
            if not completed:
                continue
            ts = datetime.fromisoformat(completed.replace("Z", "+00:00"))
            if ts < cutoff:
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
        except (OSError, json.JSONDecodeError, ValueError) as e:
            log.debug("cleanup skip %s: %s", p, e)
    if removed:
        log.info("cleanup removed %d job folder(s)", removed)
    return removed
