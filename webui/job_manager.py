"""Job queue, persistence, and global controls."""

from __future__ import annotations

import json
import logging
import shutil
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from webui.config import WebUIConfig, ensure_data_layout, load_config
from webui.ultrasinger_tag import read_prior_song_version_from_job_output

log = logging.getLogger("ultrasinger.webui.jobs")

_QUEUE_STATE_FILENAME = "queue.json"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_ts(s: Optional[str]) -> float:
    """Parse job ISO timestamps for sorting (0 if missing/invalid)."""
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _job_dir(cfg: WebUIConfig, job_id: str) -> Path:
    return cfg.jobs_dir() / job_id


def _clear_job_output_dir(cfg: WebUIConfig, job_id: str) -> None:
    """Remove prior UltraSinger output so a retry does not reuse or duplicate folders."""
    out = _job_dir(cfg, job_id) / "output"
    if out.is_dir():
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)


def _wipe_jobs_root(cfg: WebUIConfig) -> None:
    root = cfg.jobs_dir()
    if root.is_dir():
        for child in list(root.iterdir()):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass
    root.mkdir(parents=True, exist_ok=True)


class JobManager:
    def __init__(self, config_loader: Callable[[], WebUIConfig] = load_config):
        self._config_loader = config_loader
        self._lock = threading.RLock()
        self._queue: deque[str] = deque()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._paused = False
        self._current_job_id: Optional[str] = None
        self._running_proc = None
        self._load_from_disk()

    def _cfg(self) -> WebUIConfig:
        return self._config_loader()

    def _load_from_disk(self) -> None:
        cfg = self._cfg()
        ensure_data_layout(cfg)
        root = cfg.jobs_dir()
        if not root.is_dir():
            return
        queued_ids: list[str] = []
        for p in root.iterdir():
            if not p.is_dir():
                continue
            jf = p / "job.json"
            if not jf.is_file():
                continue
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                jid = data.get("job_id") or p.name
                data["job_id"] = jid
                if data.get("status") == JobStatus.QUEUED.value:
                    self._jobs[jid] = data
                    queued_ids.append(jid)
                elif data.get("status") == JobStatus.RUNNING.value:
                    data["status"] = JobStatus.FAILED.value
                    data["stage"] = "interrupted"
                    data["error"] = "Server restarted while job was running"
                    data["completed_at"] = _now_iso()
                    self._save_job(data)
                    self._jobs[jid] = data
                else:
                    self._jobs[jid] = data
            except (OSError, json.JSONDecodeError) as e:
                log.warning("Skip bad job folder %s: %s", p, e)
        self._restore_queue_and_paused_from_disk(cfg)

    def _persist_queue_state(self) -> None:
        """Write queue order and pause flag so restarts keep the same queue."""
        with self._lock:
            cfg = self._cfg()
            root = cfg.jobs_dir()
            root.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "paused": self._paused,
                "queue": list(self._queue),
            }
            (root / _QUEUE_STATE_FILENAME).write_text(
                json.dumps(payload, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )

    def _restore_queue_and_paused_from_disk(self, cfg: WebUIConfig) -> None:
        """Rebuild deque from queue.json; fall back to created_at order for any missing ids."""
        queued_set = {
            jid
            for jid, j in self._jobs.items()
            if j.get("status") == JobStatus.QUEUED.value
        }
        saved_queue: list[str] = []
        saved_paused = False
        qfile = cfg.jobs_dir() / _QUEUE_STATE_FILENAME
        if qfile.is_file():
            try:
                data = json.loads(qfile.read_text(encoding="utf-8"))
                saved_paused = bool(data.get("paused"))
                raw = data.get("queue")
                if isinstance(raw, list):
                    saved_queue = [str(x) for x in raw if isinstance(x, str)]
            except (OSError, json.JSONDecodeError) as e:
                log.warning("Could not read %s: %s", qfile, e)
        ordered: list[str] = []
        seen: set[str] = set()
        for jid in saved_queue:
            if jid in queued_set:
                ordered.append(jid)
                seen.add(jid)
        remainder = sorted(queued_set - seen, key=lambda x: self._jobs[x].get("created_at") or "")
        ordered.extend(remainder)
        self._queue.clear()
        self._queue.extend(ordered)
        self._paused = saved_paused

    def wipe_jobs_dir_and_reset(self) -> None:
        """Delete every job folder under data/jobs and clear queue state (startup reset)."""
        cfg = self._cfg()
        with self._lock:
            self._queue.clear()
            self._jobs.clear()
            self._paused = False
            self._current_job_id = None
            self._running_proc = None
        _wipe_jobs_root(cfg)
        log.info("Jobs directory cleared and queue reset")
        try:
            self._persist_queue_state()
        except OSError:
            log.warning("Could not write empty queue state after wipe", exc_info=True)

    def _save_job(self, job: dict[str, Any]) -> None:
        cfg = self._cfg()
        d = _job_dir(cfg, job["job_id"])
        d.mkdir(parents=True, exist_ok=True)
        (d / "job.json").write_text(json.dumps(job, indent=2), encoding="utf-8")

    def save_jobs_backup_snapshot(self, keep_latest: int = 5) -> Path:
        cfg = self._cfg()
        backup_dir = cfg.history_dir() / "job_backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        with self._lock:
            jobs = [dict(j) for j in self._jobs.values()]
            queue_snapshot = list(self._queue)
            paused_snapshot = self._paused
        jobs.sort(key=lambda j: str(j.get("created_at") or ""))

        payload = {
            "format": "ultrasinger-webui-jobs-backup",
            "version": 2,
            "exported_at": _now_iso(),
            "jobs": jobs,
            "count": len(jobs),
            "queue": queue_snapshot,
            "paused": paused_snapshot,
        }
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        out = backup_dir / f"jobs_backup_{stamp}.json"
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")

        try:
            keep_n = max(1, int(keep_latest))
        except (TypeError, ValueError):
            keep_n = 5
        files = sorted(
            backup_dir.glob("jobs_backup_*.json"),
            key=lambda p: p.stat().st_mtime,
        )
        while len(files) > keep_n:
            old = files.pop(0)
            try:
                old.unlink()
            except OSError:
                log.warning("Could not remove old jobs backup: %s", old)
        return out

    def append_history(self, job_id: str, event: str) -> None:
        cfg = self._cfg()
        cfg.history_dir().mkdir(parents=True, exist_ok=True)
        line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')} | {job_id} | {event}\n"
        try:
            with open(cfg.history_log_path(), "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            log.error("history write failed: %s", e)

    def create_job(
        self,
        title: str,
        artist: str,
        source: str,
        source_type: str,
        input_path: str,
        cookiefile: Optional[str] = None,
        youtube_metadata: bool = False,
        tag_prior_song_version: Optional[str] = None,
        tag_upload_file_hash: Optional[str] = None,
    ) -> dict[str, Any]:
        cfg = self._cfg()
        ensure_data_layout(cfg)
        job_id = f"job_{uuid.uuid4().hex[:10]}"
        job = {
            "job_id": job_id,
            "title": title,
            "artist": artist,
            "source": source,
            "source_type": source_type,
            "input_path": input_path,
            "cookiefile": cookiefile,
            "youtube_metadata": bool(youtube_metadata),
            "tag_prior_song_version": tag_prior_song_version,
            "tag_upload_file_hash": tag_upload_file_hash,
            "status": JobStatus.QUEUED.value,
            "stage": "Queued",
            "created_at": _now_iso(),
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._queue.append(job_id)
            self._save_job(job)
            self._persist_queue_state()
        self.append_history(job_id, "queued")
        return job

    def list_jobs(self) -> list[dict[str, Any]]:
        """Order: running first, then queue (head = next to run at top), then finished newest-first."""
        with self._lock:
            jobs = [dict(j) for j in self._jobs.values()]
            queue_pos = {jid: i for i, jid in enumerate(self._queue)}

            def sort_key(j: dict[str, Any]) -> tuple:
                st = j.get("status")
                jid = j.get("job_id", "")
                if st == JobStatus.RUNNING.value:
                    return (0, 0, 0.0, jid)
                if st == JobStatus.QUEUED.value:
                    pos = queue_pos.get(jid)
                    if pos is None:
                        pos = 10**9
                    return (1, pos, 0.0, jid)
                fin = j.get("completed_at") or j.get("created_at") or ""
                return (2, 0, -_iso_ts(fin), jid)

            return sorted(jobs, key=sort_key)

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            j = self._jobs.get(job_id)
            return dict(j) if j else None

    def persist_job(self, job: dict[str, Any]) -> None:
        with self._lock:
            self._jobs[job["job_id"]] = job
            self._save_job(job)

    def dequeue(self) -> Optional[str]:
        with self._lock:
            if self._paused:
                return None
            while self._queue:
                jid = self._queue.popleft()
                j = self._jobs.get(jid)
                if j and j.get("status") == JobStatus.QUEUED.value:
                    self._persist_queue_state()
                    return jid
            self._persist_queue_state()
            return None

    def mark_running(self, job_id: str) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return
            j["status"] = JobStatus.RUNNING.value
            j["stage"] = "Starting"
            j["started_at"] = _now_iso()
            self._current_job_id = job_id
            self._save_job(j)
        self.append_history(job_id, "running")

    def update_job(
        self,
        job_id: str,
        stage: Optional[str] = None,
        status: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return
            if stage is not None:
                j["stage"] = stage
            if status is not None:
                j["status"] = status
            if error is not None:
                j["error"] = error
            self._save_job(j)

    def complete_job(self, job_id: str, success: bool, error: Optional[str] = None) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return
            j["completed_at"] = _now_iso()
            if j.get("started_at"):
                try:
                    t0 = datetime.fromisoformat(j["started_at"].replace("Z", "+00:00"))
                    t1 = datetime.fromisoformat(j["completed_at"].replace("Z", "+00:00"))
                    j["duration_seconds"] = max(0, (t1 - t0).total_seconds())
                except ValueError:
                    j["duration_seconds"] = None
            j["status"] = JobStatus.COMPLETED.value if success else JobStatus.FAILED.value
            j["stage"] = "Complete" if success else "Failed"
            if error:
                j["error"] = error
            if self._current_job_id == job_id:
                self._current_job_id = None
            self._save_job(j)
        self.append_history(job_id, "completed" if success else "failed")

    def skip_job(self, job_id: str, stage_message: str) -> None:
        """Mark job completed without running UltraSinger (e.g. already present in export folder)."""
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return
            now = _now_iso()
            j["status"] = JobStatus.SKIPPED.value
            j["stage"] = stage_message
            j["error"] = None
            j["started_at"] = now
            j["completed_at"] = now
            j["duration_seconds"] = 0.0
            self._save_job(j)
        self.append_history(job_id, "skipped")

    def set_running_process(self, proc) -> None:
        self._running_proc = proc

    def clear_running_process(self) -> None:
        self._running_proc = None

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def stop_all(self) -> None:
        with self._lock:
            self._paused = True
            proc = self._running_proc
        if proc and proc.poll() is None:
            self._terminate_tree(proc)
        jid = self._current_job_id
        if jid:
            with self._lock:
                j = self._jobs.get(jid)
                if j and j.get("status") == JobStatus.RUNNING.value:
                    j["status"] = JobStatus.CANCELLED.value
                    j["stage"] = "Cancelled"
                    j["completed_at"] = _now_iso()
                    j["error"] = "Stopped by user"
                    self._save_job(j)
                    self._current_job_id = None
            self.append_history(jid, "cancelled")
        self._persist_queue_state()

    def resume_processing(self) -> None:
        with self._lock:
            self._paused = False
            self._persist_queue_state()

    def cancel_all_active(self) -> int:
        """Cancel every queued and running job. Does not change pause state (unlike stop_all)."""
        n = 0
        for _ in range(64):
            with self._lock:
                running = self._current_job_id
                queued = [
                    jid
                    for jid, j in self._jobs.items()
                    if j.get("status") == JobStatus.QUEUED.value
                ]
            if running:
                if self.cancel_job(running):
                    n += 1
                continue
            if queued:
                for jid in queued:
                    if self.cancel_job(jid):
                        n += 1
                continue
            break
        return n

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            if job_id == self._current_job_id:
                proc = self._running_proc
            else:
                proc = None
            j = self._jobs.get(job_id)
            if not j:
                return False
            if j.get("status") == JobStatus.QUEUED.value:
                j["status"] = JobStatus.CANCELLED.value
                j["stage"] = "Cancelled"
                j["completed_at"] = _now_iso()
                self._save_job(j)
                try:
                    while job_id in self._queue:
                        self._queue.remove(job_id)
                except ValueError:
                    pass
                self._persist_queue_state()
                self.append_history(job_id, "cancelled")
                return True
            if j.get("status") == JobStatus.RUNNING.value and proc and proc.poll() is None:
                self._terminate_tree(proc)
                j["status"] = JobStatus.CANCELLED.value
                j["stage"] = "Cancelled"
                j["completed_at"] = _now_iso()
                j["error"] = "Cancelled by user"
                self._save_job(j)
                self._current_job_id = None
                self.append_history(job_id, "cancelled")
                return True
        return False

    def retry_job(self, job_id: str) -> bool:
        with self._lock:
            j = self._jobs.get(job_id)
            if not j or j.get("status") not in (
                JobStatus.FAILED.value,
                JobStatus.COMPLETED.value,
                JobStatus.SKIPPED.value,
            ):
                return False
            cfg = self._cfg()
            prior_tag_ver = read_prior_song_version_from_job_output(cfg.jobs_dir(), job_id)
            _clear_job_output_dir(cfg, job_id)
            j["status"] = JobStatus.QUEUED.value
            j["stage"] = "Queued"
            j["started_at"] = None
            j["completed_at"] = None
            j["duration_seconds"] = None
            j["error"] = None
            j["tag_prior_song_version"] = prior_tag_ver
            if j.get("source_type") == "upload" and (j.get("input_path") or "").strip():
                try:
                    from webui.ultrasinger_tag import file_sha256

                    j["tag_upload_file_hash"] = file_sha256(Path(str(j["input_path"]).strip()))
                except OSError:
                    pass
            self._queue.append(job_id)
            self._save_job(j)
            self._persist_queue_state()
        self.append_history(job_id, "retry")
        return True

    def move_queued_job_to_front(self, job_id: str) -> bool:
        """Move a queued job to the front so it runs next."""
        with self._lock:
            j = self._jobs.get(job_id)
            if not j or j.get("status") != JobStatus.QUEUED.value:
                return False
            if job_id not in self._queue:
                return False
            try:
                self._queue.remove(job_id)
            except ValueError:
                return False
            self._queue.appendleft(job_id)
            self._persist_queue_state()
        self.append_history(job_id, "prioritized")
        return True

    def clear_job(self, job_id: str) -> bool:
        with self._lock:
            j = self._jobs.get(job_id)
            if not j:
                return False
            st = j.get("status")
            if st in (JobStatus.RUNNING.value, JobStatus.QUEUED.value):
                return False
            del self._jobs[job_id]
        cfg = self._cfg()
        d = _job_dir(cfg, job_id)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
        self.append_history(job_id, "cleared")
        return True

    def clear_all_finished(self) -> int:
        cleared = 0
        with self._lock:
            ids = [
                jid
                for jid, j in self._jobs.items()
                if j.get("status")
                in (
                    JobStatus.COMPLETED.value,
                    JobStatus.CANCELLED.value,
                    JobStatus.SKIPPED.value,
                )
            ]
        for jid in ids:
            with self._lock:
                cur = self._jobs.get(jid)
                if not cur or cur.get("status") not in (
                    JobStatus.COMPLETED.value,
                    JobStatus.CANCELLED.value,
                    JobStatus.SKIPPED.value,
                ):
                    continue
            if self.clear_job(jid):
                cleared += 1
        return cleared

    def clear_all_completed(self) -> int:
        """Remove every completed job (same set as the Downloads list)."""
        cleared = 0
        with self._lock:
            ids = [
                jid
                for jid, j in self._jobs.items()
                if j.get("status") == JobStatus.COMPLETED.value
            ]
        for jid in ids:
            if self.clear_job(jid):
                cleared += 1
        return cleared

    def clear_all_failed(self) -> int:
        """Remove every failed job."""
        cleared = 0
        with self._lock:
            ids = [
                jid
                for jid, j in self._jobs.items()
                if j.get("status") == JobStatus.FAILED.value
            ]
        for jid in ids:
            if self.clear_job(jid):
                cleared += 1
        return cleared

    def retry_all_failed(self) -> int:
        """Retry every failed job by re-queuing it and clearing old output."""
        with self._lock:
            ids = [
                jid
                for jid, j in self._jobs.items()
                if j.get("status") == JobStatus.FAILED.value
            ]
        n = 0
        for jid in ids:
            if self.retry_job(jid):
                n += 1
        return n

    def _terminate_tree(self, proc) -> None:
        import signal
        import subprocess
        import sys

        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        else:
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=15)
            except Exception:
                try:
                    proc.kill()
                except OSError:
                    pass


job_manager = JobManager()
