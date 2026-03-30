"""Background worker: run UltraSinger in a subprocess per job."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
import threading
import time
from pathlib import Path
from typing import Optional

from webui.config import WebUIConfig, load_config, paths_for_worker
from webui.job_manager import JobManager, JobStatus, job_manager
from webui.output_bundle import iter_job_output_files
from webui.yarg_export import group_output_by_song_folder, plan_yarg_flat_copies

log = logging.getLogger("ultrasinger.webui.worker")
VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"})

STAGE_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"Downloading from YouTube|Downloading Video", re.I), "Downloading"),
    (re.compile(r"Downloading thumbnail", re.I), "Downloading"),
    (re.compile(r"Extracting audio from video|Creating video without audio", re.I), "Converting"),
    (re.compile(r"Separating vocals from audio", re.I), "Separating Vocals"),
    (re.compile(r"Transcribing ", re.I), "Transcribing"),
    (re.compile(r"Pitching with", re.I), "Pitching"),
    (re.compile(r"Creating UltraStar file", re.I), "Finalizing"),
    (re.compile(r"Creating midi file", re.I), "Finalizing"),
]


def _match_stage(line: str) -> Optional[str]:
    for pat, name in STAGE_RULES:
        if pat.search(line):
            return name
    return None


def _build_argv(job: dict, cfg: WebUIConfig, job_output: Path) -> list[str]:
    p = paths_for_worker(cfg)
    src = p["src_dir"]
    script = p["ultrasinger_py"]
    if not script.is_file():
        raise FileNotFoundError(f"UltraSinger not found at {script}")

    argv = [
        sys.executable,
        "-u",
        str(script.name),
        "-i",
        job["input_path"],
        "-o",
        str(job_output),
        "--whisper",
        cfg.whisper_model,
    ]
    wct = (cfg.whisper_compute_type or "").strip()
    if wct:
        argv.extend(["--whisper_compute_type", wct])
    argv.extend(["--whisper_batch_size", str(cfg.whisper_batch_size)])
    if cfg.demucs_model:
        argv.extend(["--demucs", cfg.demucs_model])
    if cfg.force_cpu:
        argv.append("--force_cpu")
    if cfg.force_whisper_cpu:
        argv.append("--force_whisper_cpu")
    if cfg.user_ffmpeg_path.strip():
        argv.extend(["--ffmpeg", cfg.user_ffmpeg_path.strip()])
    cf = job.get("cookiefile") or (cfg.cookiefile.strip() or None)
    if cf:
        argv.extend(["--cookiefile", cf])
    if not cfg.delete_workfiles_after_complete:
        argv.append("--keep_cache")
    if job.get("youtube_metadata"):
        argv.append("--youtube_metadata")
    # Ultrastar folder export needs .mid + stem tracks; do not pass --yarg_mode for that run
    ultrastar_on = cfg.ultrastar_export_enabled and (cfg.ultrastar_export_path or "").strip()
    if cfg.yarg_mode and not ultrastar_on:
        argv.append("--yarg_mode")
    return argv, src


def _augment_path(cfg: WebUIConfig) -> dict[str, str]:
    env = os.environ.copy()
    ytd = cfg.ytdlp_binary_path.strip()
    if ytd:
        bin_dir = str(Path(ytd).resolve().parent)
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env


def _resolve_ff_tool(cfg: WebUIConfig, tool_name: str) -> Optional[str]:
    tool_name = tool_name.lower()
    user = (cfg.user_ffmpeg_path or "").strip()
    names = (tool_name, f"{tool_name}.exe")
    if user:
        p = Path(user).expanduser()
        if p.is_dir():
            for n in names:
                cand = p / n
                if cand.is_file():
                    return str(cand)
        elif p.is_file():
            low = p.name.lower()
            if low in names:
                return str(p)
            sibling = p.with_name(tool_name + p.suffix)
            if sibling.is_file():
                return str(sibling)
    from_path = shutil.which(tool_name)
    if from_path:
        return from_path
    return None


def _probe_video_duration_seconds(ffprobe_bin: str, video_path: Path) -> Optional[float]:
    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if res.returncode != 0:
        return None
    try:
        dur = float((res.stdout or "").strip())
    except (TypeError, ValueError):
        return None
    return dur if dur > 0 else None


def _upsert_background_tag(txt_path: Path, bg_filename: str) -> None:
    raw = txt_path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    tag = f"#BACKGROUND:{bg_filename}"

    for i, line in enumerate(lines):
        if line.startswith("#BACKGROUND:"):
            lines[i] = tag
            txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            return

    note_markers = (":", "*", "F", "R", "G", "-", "E")
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if line and line[0] in note_markers:
            insert_at = i
            break

    lines.insert(insert_at, tag)
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


class WorkerService:
    def __init__(self, jm: JobManager):
        self._jm = jm
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="UltraSingerWorker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            job_id = self._jm.dequeue()
            if not job_id:
                time.sleep(0.35)
                continue
            try:
                self._run_one(job_id)
            except Exception as e:
                log.exception("job %s crashed: %s", job_id, e)
                self._jm.complete_job(job_id, False, str(e))

    def _run_one(self, job_id: str) -> None:
        cfg = load_config()
        job = self._jm.get_job(job_id)
        if not job:
            return

        from webui.config import ensure_data_layout

        ensure_data_layout(cfg)
        root = cfg.jobs_dir() / job_id
        out = root / "output"
        logs_dir = root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        out.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / "job.log"

        argv, cwd = _build_argv(job, cfg, out)
        self._jm.mark_running(job_id)
        env = _augment_path(cfg)

        log.info("Starting job %s: %s", job_id, " ".join(argv[:6]) + " ...")

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        self._jm.set_running_process(proc)

        rc = 1
        try:
            assert proc.stdout is not None
            with open(log_path, "a", encoding="utf-8", errors="replace") as lf:
                lf.write(f"--- UltraSinger WebUI job {job_id} ---\n")
                try:
                    for line in proc.stdout:
                        lf.write(line)
                        lf.flush()
                        stage = _match_stage(line)
                        if stage:
                            self._jm.update_job(job_id, stage=stage)
                except (BrokenPipeError, ValueError, OSError):
                    pass
        finally:
            self._jm.clear_running_process()
            if proc.poll() is None:
                rc = proc.wait()
            else:
                rc = proc.returncode or 1

        cur = self._jm.get_job(job_id)
        if cur and cur.get("status") == JobStatus.CANCELLED.value:
            return

        ok = rc == 0
        err = None if ok else f"UltraSinger exited with code {rc}"
        self._jm.complete_job(job_id, ok, err)
        if ok and cfg.delete_workfiles_after_complete:
            self._cleanup_job_cache(out)
        if ok:
            self._capture_youtube_background_if_needed(cfg, job, out)
            self._copy_yarg_export_folder(cfg, job_id, out)
            self._copy_ultrastar_export_folder(cfg, job_id, out)

    def _capture_youtube_background_if_needed(
        self,
        cfg: WebUIConfig,
        job: dict,
        output_root: Path,
    ) -> None:
        if not job.get("youtube_metadata"):
            return

        ffmpeg_bin = _resolve_ff_tool(cfg, "ffmpeg")
        ffprobe_bin = _resolve_ff_tool(cfg, "ffprobe")
        if not ffmpeg_bin or not ffprobe_bin:
            log.info("Skipping YouTube background screenshot: ffmpeg/ffprobe not available")
            return

        try:
            pct_raw = int(getattr(cfg, "youtube_bg_capture_percent", 30) or 30)
        except (TypeError, ValueError):
            pct_raw = 30
        pct = max(0, min(100, pct_raw))
        song_dirs = [p for p in output_root.iterdir() if p.is_dir()]
        for song_dir in song_dirs:
            txt_files = sorted(song_dir.glob("*.txt"))
            if not txt_files:
                continue
            preferred_txt = next((p for p in txt_files if p.stem == song_dir.name), txt_files[0])
            base_name = preferred_txt.stem

            videos = [p for p in song_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES]
            if not videos:
                continue
            video = next((p for p in videos if p.stem == base_name), videos[0])

            dur = _probe_video_duration_seconds(ffprobe_bin, video)
            if not dur:
                continue
            t = max(0.0, min(dur - 0.1, dur * (pct / 100.0)))
            bg_path = song_dir / f"{base_name} [BG].jpg"

            cmd = [
                ffmpeg_bin,
                "-y",
                "-ss",
                f"{t:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(bg_path),
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            except subprocess.TimeoutExpired:
                log.warning("Background screenshot timed out for %s", song_dir)
                continue
            if res.returncode != 0 or not bg_path.is_file():
                log.warning("Background screenshot failed for %s", song_dir)
                continue

            try:
                _upsert_background_tag(preferred_txt, bg_path.name)
            except OSError as e:
                log.warning("Could not update BACKGROUND tag in %s: %s", preferred_txt, e)

    def _copy_song_bundle_export(
        self,
        raw_path: str,
        job_id: str,
        output_root: Path,
        *,
        exclude_stem_tracks: bool,
        exclude_midi: bool,
        log_prefix: str,
    ) -> None:
        raw = (raw_path or "").strip()
        if not raw:
            return
        try:
            dest_root = Path(raw).expanduser().resolve()
        except OSError as e:
            log.warning("%s: invalid path %s: %s", log_prefix, raw, e)
            return
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("%s: cannot create %s: %s", log_prefix, dest_root, e)
            return

        grouped: dict[str, list[tuple[Path, str]]] = defaultdict(list)
        for src, rel in iter_job_output_files(
            output_root,
            exclude_stem_tracks=exclude_stem_tracks,
            exclude_midi=exclude_midi,
        ):
            rel_norm = str(rel).replace("\\", "/")
            if "/" in rel_norm:
                top, rest = rel_norm.split("/", 1)
            else:
                top, rest = job_id, rel_norm
            grouped[top].append((src, rest))

        if not grouped:
            log.warning("%s: no files under %s", log_prefix, output_root)
            return

        total = 0
        n_folders = 0
        for top, items in grouped.items():
            dest_song = dest_root / top
            try:
                if dest_song.exists():
                    shutil.rmtree(dest_song, ignore_errors=True)
                dest_song.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                log.warning("%s: cannot prepare %s: %s", log_prefix, dest_song, e)
                continue
            n_here = 0
            for src, rest in items:
                dst = dest_song / rest
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    n_here += 1
                except OSError as e:
                    log.warning("%s: copy failed %s -> %s: %s", log_prefix, src, dst, e)
            total += n_here
            n_folders += 1
        log.info(
            "%s: %d file(s) into %d folder(s) under %s",
            log_prefix,
            total,
            n_folders,
            dest_root,
        )

    def _copy_yarg_export_folder(self, cfg: WebUIConfig, job_id: str, output_root: Path) -> None:
        if not cfg.yarg_export_enabled:
            return
        raw = (cfg.yarg_export_path or "").strip()
        if not raw:
            return
        try:
            dest_root = Path(raw).expanduser().resolve()
        except OSError as e:
            log.warning("YARG export: invalid path %s: %s", raw, e)
            return
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("YARG export: cannot create %s: %s", dest_root, e)
            return

        grouped = group_output_by_song_folder(
            output_root,
            job_id,
            exclude_stem_tracks=cfg.zip_exclude_stem_tracks,
            exclude_midi=True,
        )

        if not grouped:
            log.warning("YARG export: no files under %s", output_root)
            return

        total = 0
        n_folders = 0
        for top, items in grouped.items():
            dest_song = dest_root / top
            try:
                if dest_song.exists():
                    shutil.rmtree(dest_song, ignore_errors=True)
                dest_song.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                log.warning("YARG export: cannot prepare %s: %s", dest_song, e)
                continue
            planned = plan_yarg_flat_copies(items, top)
            n_here = 0
            for src, dest_name in planned:
                dst = dest_song / dest_name
                try:
                    shutil.copy2(src, dst)
                    n_here += 1
                except OSError as e:
                    log.warning("YARG export: copy failed %s -> %s: %s", src, dst, e)
            total += n_here
            n_folders += 1
        log.info(
            "YARG export: %d file(s) into %d folder(s) under %s (flat: guitar, background, notes, album)",
            total,
            n_folders,
            dest_root,
        )

    def _copy_ultrastar_export_folder(self, cfg: WebUIConfig, job_id: str, output_root: Path) -> None:
        if not cfg.ultrastar_export_enabled:
            return
        self._copy_song_bundle_export(
            cfg.ultrastar_export_path,
            job_id,
            output_root,
            exclude_stem_tracks=False,
            exclude_midi=False,
            log_prefix="Ultrastar export",
        )

    def _cleanup_job_cache(self, output_root: Path) -> None:
        for song_dir in output_root.iterdir():
            if not song_dir.is_dir():
                continue
            cache = song_dir / "cache"
            if cache.is_dir():
                shutil.rmtree(cache, ignore_errors=True)


worker_service = WorkerService(job_manager)
