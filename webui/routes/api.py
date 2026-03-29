"""JSON API for jobs, settings, downloads, and controls."""

from __future__ import annotations

import logging
import re
import shutil
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from webui.config import WHISPER_COMPUTE_TYPE_VALUES, default_config, load_config, save_config
from webui.job_manager import JobStatus, job_manager
from webui.output_bundle import iter_job_output_files
from webui.yarg_export import build_yarg_zip_album_arc_overrides, iter_yarg_export_style_zip_entries
from webui.services.playlist import expand_playlist
from webui.zip_naming import (
    logical_root_for_job_output,
    per_job_zip_download_filename,
    remap_inner_arc_to_root,
)

log = logging.getLogger("ultrasinger.webui.api")

router = APIRouter(prefix="/api", tags=["api"])

_JOB_ID_RE = re.compile(r"^job_[a-zA-Z0-9]+$")


def _require_job_id(job_id: str) -> None:
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(400, "Invalid job id")


def _live_duration_seconds(job: dict[str, Any]) -> Optional[float]:
    if job.get("status") != JobStatus.RUNNING.value or not job.get("started_at"):
        return job.get("duration_seconds")
    try:
        t0 = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
        return max(0, (datetime.now(timezone.utc) - t0).total_seconds())
    except ValueError:
        return job.get("duration_seconds")


def _job_public(job: dict[str, Any]) -> dict[str, Any]:
    out = dict(job)
    out["live_duration_seconds"] = _live_duration_seconds(job)
    return out


@router.get("/jobs")
def api_list_jobs() -> dict[str, Any]:
    return {"jobs": [_job_public(j) for j in job_manager.list_jobs()]}


@router.get("/jobs/{job_id}")
def api_get_job(job_id: str) -> dict[str, Any]:
    _require_job_id(job_id)
    j = job_manager.get_job(job_id)
    if not j:
        raise HTTPException(404)
    return _job_public(j)


@router.get("/jobs/{job_id}/log")
def api_job_log(job_id: str, tail: int = 2000) -> dict[str, Any]:
    _require_job_id(job_id)
    cfg = load_config()
    log_path = cfg.jobs_dir() / job_id / "logs" / "job.log"
    if not log_path.is_file():
        return {"lines": ""}
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if tail > 0 and len(text) > tail * 80:
        text = text[-tail * 80 :]
    return {"lines": text}


@router.post("/jobs/url")
async def api_submit_url(
    url: str = Form(...),
    title: str = Form(""),
) -> dict[str, Any]:
    u = url.strip()
    if not u.startswith("http://") and not u.startswith("https://"):
        raise HTTPException(400, "URL must start with http:// or https://")
    cfg = load_config()
    t = title.strip() or u
    job = job_manager.create_job(t, u, "url", u, cookiefile=cfg.cookiefile or None)
    return {"job": job}


@router.post("/jobs/playlist")
async def api_submit_playlist(url: str = Form(...)) -> dict[str, Any]:
    u = url.strip()
    if not u.startswith("http://") and not u.startswith("https://"):
        raise HTTPException(400, "Invalid playlist URL")
    cfg = load_config()
    cf = cfg.cookiefile.strip() or None
    try:
        items = expand_playlist(u, cf)
    except Exception as e:
        log.exception("playlist expand failed")
        raise HTTPException(400, f"Could not read playlist: {e}") from e
    if not items:
        raise HTTPException(400, "No entries found in playlist")
    jobs = []
    for watch_url, hint in items:
        jobs.append(
            job_manager.create_job(hint, watch_url, "youtube", watch_url, cookiefile=cf)
        )
    return {"count": len(jobs), "jobs": jobs}


def _form_truthy(v: str | None) -> bool:
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on")


@router.post("/jobs/from-url")
async def api_submit_from_url(
    url: str = Form(...),
    title: str = Form(""),
    youtube_metadata: str = Form(""),
) -> dict[str, Any]:
    """Single video or playlist: expand with yt-dlp when possible, else one job for the raw URL."""
    u = url.strip()
    if not u.startswith("http://") and not u.startswith("https://"):
        raise HTTPException(400, "URL must start with http:// or https://")
    cfg = load_config()
    cf = cfg.cookiefile.strip() or None
    use_yt_meta = _form_truthy(youtube_metadata)
    items: list[tuple[str, str]] = []
    try:
        items = expand_playlist(u, cf)
    except Exception:
        log.debug("expand_playlist failed; using direct URL", exc_info=True)
    if not items:
        items = [(u, title.strip() or u)]
    custom = title.strip()
    jobs = []
    for watch_url, hint in items:
        job_title = custom if len(items) == 1 and custom else hint
        jobs.append(
            job_manager.create_job(
                job_title,
                watch_url,
                "url",
                watch_url,
                cookiefile=cf,
                youtube_metadata=use_yt_meta,
            )
        )
    return {"count": len(jobs), "jobs": jobs}


@router.post("/jobs/upload")
async def api_upload(
    file: UploadFile = File(...),
) -> dict[str, Any]:
    if not file.filename:
        raise HTTPException(400, "No filename")
    cfg = load_config()
    job = job_manager.create_job(
        file.filename,
        file.filename,
        "upload",
        "",
        cookiefile=None,
    )
    jid = job["job_id"]
    input_dir = cfg.jobs_dir() / jid / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name
    dest = input_dir / safe_name
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    job["input_path"] = str(dest.resolve())
    job_manager.persist_job(job)
    return {"job": job}


@router.post("/jobs/{job_id}/cancel")
def api_cancel(job_id: str) -> dict[str, Any]:
    _require_job_id(job_id)
    ok = job_manager.cancel_job(job_id)
    return {"ok": ok}


@router.post("/jobs/{job_id}/retry")
def api_retry(job_id: str) -> dict[str, Any]:
    _require_job_id(job_id)
    ok = job_manager.retry_job(job_id)
    return {"ok": ok}


@router.delete("/jobs/{job_id}")
def api_clear(job_id: str) -> dict[str, Any]:
    _require_job_id(job_id)
    ok = job_manager.clear_job(job_id)
    return {"ok": ok}


@router.post("/jobs/control/stop-all")
def api_stop_all() -> dict[str, str]:
    job_manager.stop_all()
    return {"status": "stopped"}


@router.post("/jobs/control/cancel-all")
def api_cancel_all() -> dict[str, Any]:
    n = job_manager.cancel_all_active()
    return {"cancelled": n}


@router.post("/jobs/control/resume")
def api_resume() -> dict[str, str]:
    job_manager.resume_processing()
    return {"status": "resumed"}


@router.post("/jobs/control/clear-all")
def api_clear_all() -> dict[str, Any]:
    n = job_manager.clear_all_finished()
    return {"cleared": n}


@router.get("/settings")
def api_get_settings() -> dict[str, Any]:
    from webui.config import config_to_api_dict

    return {"settings": config_to_api_dict(load_config())}


@router.post("/settings")
async def api_post_settings(request: Request) -> dict[str, Any]:
    body = await request.json()
    cfg = load_config()
    ref = default_config()
    for k, v in body.items():
        if not hasattr(cfg, k):
            continue
        if k == "whisper_compute_type":
            s = (str(v) if v is not None else "").strip()
            if s in WHISPER_COMPUTE_TYPE_VALUES:
                cfg.whisper_compute_type = s
            continue
        sample = getattr(ref, k)
        if isinstance(sample, bool):
            setattr(cfg, k, v is True or v == "true" or v == "1")
        elif type(sample) is int:
            try:
                setattr(cfg, k, int(v))
            except (TypeError, ValueError):
                pass
        else:
            setattr(cfg, k, v)
    save_config(cfg)
    return {"ok": True}


@router.post("/cookies/upload")
async def api_cookies_upload(file: UploadFile = File(...)) -> dict[str, Any]:
    cfg = load_config()
    dest = cfg.effective_data_dir() / "cookies.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    cfg.cookiefile = str(dest.resolve())
    save_config(cfg)
    return {"ok": True, "cookiefile": cfg.cookiefile}


@router.post("/cookies/paste")
async def api_cookies_paste(request: Request) -> dict[str, Any]:
    body = await request.json()
    content = body.get("content")
    if not isinstance(content, str):
        raise HTTPException(400, "JSON body must include string field 'content'")
    text = content.strip()
    if not text:
        raise HTTPException(400, "Paste your cookies (Netscape cookies.txt format)")
    cfg = load_config()
    dest = cfg.effective_data_dir() / "cookies.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text + ("\n" if not text.endswith("\n") else ""), encoding="utf-8", newline="\n")
    cfg.cookiefile = str(dest.resolve())
    save_config(cfg)
    return {"ok": True, "cookiefile": cfg.cookiefile}


def _zip_output_dir(
    output_root: Path,
    zip_path: Path,
    exclude_stem_tracks: bool = False,
    job_id: str = "",
    *,
    yarg_flat_zip_layout: bool = False,
) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if yarg_flat_zip_layout:
            for path, arcname in iter_yarg_export_style_zip_entries(
                output_root,
                job_id,
                exclude_stem_tracks=exclude_stem_tracks,
            ):
                zf.write(path, arcname=str(arcname).replace("\\", "/"))
            return
        overrides = build_yarg_zip_album_arc_overrides(
            output_root,
            exclude_stem_tracks=exclude_stem_tracks,
            exclude_midi=False,
            job_id_fallback_top=job_id,
        )
        for path, rel in iter_job_output_files(
            output_root,
            exclude_stem_tracks=exclude_stem_tracks,
            exclude_midi=False,
        ):
            rel_norm = str(rel).replace("\\", "/")
            arcname = overrides.get(path.resolve(), rel_norm)
            zf.write(path, arcname=arcname)


@router.get("/jobs/{job_id}/download")
def api_download_job(job_id: str) -> FileResponse:
    _require_job_id(job_id)
    j = job_manager.get_job(job_id)
    if not j or j.get("status") != JobStatus.COMPLETED.value:
        raise HTTPException(400, "Job is not completed")
    cfg = load_config()
    out = cfg.jobs_dir() / job_id / "output"
    if not out.is_dir() or not any(out.iterdir()):
        raise HTTPException(404, "No output files")
    temp = cfg.effective_data_dir() / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    zip_path = temp / f"{job_id}_download.zip"
    _zip_output_dir(
        out,
        zip_path,
        cfg.zip_exclude_stem_tracks,
        job_id=job_id,
        yarg_flat_zip_layout=cfg.yarg_mode,
    )
    return FileResponse(
        zip_path,
        filename=per_job_zip_download_filename(j, out),
        media_type="application/zip",
    )


def _get_bulk_state(request: Request) -> dict:
    if not hasattr(request.app.state, "bulk_downloads"):
        request.app.state.bulk_downloads = {}
    return request.app.state.bulk_downloads  # type: ignore[no-any-return]


@router.post("/downloads/prepare-all")
def api_prepare_all_download(request: Request) -> dict[str, Any]:
    cfg = load_config()
    completed = [
        j
        for j in job_manager.list_jobs()
        if j.get("status") == JobStatus.COMPLETED.value
    ]
    if not completed:
        raise HTTPException(400, "No completed jobs to download")

    task_id = uuid.uuid4().hex
    state = _get_bulk_state(request)
    state[task_id] = {"ready": False, "path": None, "error": None}

    exclude_stems = cfg.zip_exclude_stem_tracks
    yarg_flat = cfg.yarg_mode

    def build() -> None:
        try:
            temp = cfg.effective_data_dir() / "temp"
            temp.mkdir(parents=True, exist_ok=True)
            zip_path = temp / f"bulk_{task_id}.zip"
            used_arc_roots: set[str] = set()
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for job in completed:
                    jid = job["job_id"]
                    out = cfg.jobs_dir() / jid / "output"
                    if not out.is_dir():
                        continue
                    logical = logical_root_for_job_output(out, job, jid)
                    arc_root = logical
                    if arc_root in used_arc_roots:
                        arc_root = f"{logical} [{jid}]"
                    used_arc_roots.add(arc_root)
                    if yarg_flat:
                        for path, arcname in iter_yarg_export_style_zip_entries(
                            out,
                            jid,
                            exclude_stem_tracks=exclude_stems,
                            zip_arc_root=arc_root,
                        ):
                            zf.write(path, arcname=str(arcname).replace("\\", "/"))
                        continue
                    overrides = build_yarg_zip_album_arc_overrides(
                        out,
                        exclude_stem_tracks=exclude_stems,
                        exclude_midi=False,
                        job_id_fallback_top=jid,
                    )
                    for path, rel in iter_job_output_files(
                        out,
                        exclude_stem_tracks=exclude_stems,
                        exclude_midi=False,
                    ):
                        rel_norm = str(rel).replace("\\", "/")
                        inner = overrides.get(path.resolve(), rel_norm)
                        new_arc = remap_inner_arc_to_root(inner, logical, arc_root)
                        zf.write(path, arcname=new_arc.replace("\\", "/"))
            state[task_id] = {"ready": True, "path": str(zip_path), "error": None}
        except Exception as e:
            log.exception("bulk zip failed")
            state[task_id] = {"ready": True, "path": None, "error": str(e)}

    threading.Thread(target=build, daemon=True).start()
    return {"task_id": task_id}


@router.get("/downloads/status/{task_id}")
def api_download_status(task_id: str, request: Request) -> dict[str, Any]:
    state = _get_bulk_state(request).get(task_id)
    if not state:
        raise HTTPException(404)
    return state


@router.get("/downloads/file/{task_id}")
def api_download_bulk_file(task_id: str, request: Request) -> FileResponse:
    state = _get_bulk_state(request).get(task_id)
    if not state or not state.get("ready") or not state.get("path"):
        raise HTTPException(404)
    if state.get("error"):
        raise HTTPException(500, state["error"])
    path = Path(state["path"])
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, filename="completed_songs.zip", media_type="application/zip")


@router.post("/downloads/clear-completed")
def api_downloads_clear_completed() -> dict[str, Any]:
    n = job_manager.clear_all_completed()
    return {"cleared": n}
