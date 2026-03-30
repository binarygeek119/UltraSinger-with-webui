"""JSON API for jobs, settings, downloads, and controls."""

from __future__ import annotations

import json
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
from webui.ultrasinger_tag import file_sha256, read_prior_song_version_from_song_dir
from webui.zip_naming import (
    logical_root_for_job_output,
    per_job_zip_download_filename,
    remap_inner_arc_to_root,
)

log = logging.getLogger("ultrasinger.webui.api")

router = APIRouter(prefix="/api", tags=["api"])

_JOB_ID_RE = re.compile(r"^job_[a-zA-Z0-9]+$")
AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".wma"})


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


def _truthy(v: Any) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def _pick_song_roots_for_scan(cfg) -> list[tuple[str, Path]]:
    roots: list[tuple[str, Path]] = []
    yarg = (cfg.yarg_export_path or "").strip()
    ultra = (cfg.ultrastar_export_path or "").strip()
    if yarg:
        roots.append(("YARG", Path(yarg).expanduser()))
    if ultra:
        roots.append(("UltraStar", Path(ultra).expanduser()))
    if not roots:
        # Fallback: common local "Songs" folder name in repo root.
        roots.append(("Songs", Path("songs")))
    return roots


def _is_under_any_root(path: Path, roots: list[tuple[str, Path]]) -> bool:
    for _label, root in roots:
        try:
            if path.is_relative_to(root.resolve()):
                return True
        except (OSError, ValueError):
            continue
    return False


def _extract_video_url_from_txt(song_dir: Path) -> str:
    txt_files = sorted(song_dir.glob("*.txt"))
    for txt in txt_files:
        try:
            for line in txt.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("#VIDEOURL:"):
                    val = line.split(":", 1)[1].strip()
                    if val.startswith("http://") or val.startswith("https://"):
                        return val
        except OSError:
            continue
    return ""


def _pick_audio_file(song_dir: Path) -> str:
    audio_files = [
        p for p in song_dir.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
    ]
    if not audio_files:
        return ""
    base = song_dir.name
    exact = next((p for p in audio_files if p.stem == base), None)
    if exact:
        return str(exact)
    non_stems = [p for p in audio_files if "[Vocals]" not in p.name and "[Instrumental]" not in p.name]
    if non_stems:
        return str(sorted(non_stems, key=lambda x: x.name.lower())[0])
    return str(sorted(audio_files, key=lambda x: x.name.lower())[0])


def _build_exported_song_row(export_type: str, song_dir: Path) -> dict[str, Any]:
    name = song_dir.name
    artist = ""
    title = name
    if " - " in name:
        artist, title = [x.strip() for x in name.split(" - ", 1)]
    try:
        file_count = sum(1 for f in song_dir.rglob("*") if f.is_file())
    except OSError:
        file_count = 0

    video_url = _extract_video_url_from_txt(song_dir)
    audio_path = _pick_audio_file(song_dir)
    mode = "youtube" if video_url else ("audio" if audio_path else "")
    return {
        "name": name,
        "artist": artist,
        "title": title,
        "type": export_type,
        "path": str(song_dir),
        "file_count": file_count,
        "reprocess_mode": mode,
        "video_url": video_url,
        "audio_path": audio_path,
    }


def _scan_exported_songs(cfg) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for export_type, root in _pick_song_roots_for_scan(cfg):
        try:
            root_resolved = root.resolve()
        except OSError:
            continue
        if not root_resolved.is_dir():
            continue
        for p in root_resolved.iterdir():
            if not p.is_dir():
                continue
            key = (export_type, str(p).lower())
            if key in seen:
                continue
            seen.add(key)
            rows.append(_build_exported_song_row(export_type, p))
    rows.sort(key=lambda x: (str(x.get("artist") or "").lower(), str(x.get("title") or "").lower()))
    return rows


@router.get("/jobs")
def api_list_jobs() -> dict[str, Any]:
    return {"jobs": [_job_public(j) for j in job_manager.list_jobs()]}


@router.get("/exported-songs")
def api_exported_songs() -> dict[str, Any]:
    cfg = load_config()
    songs = _scan_exported_songs(cfg)
    return {"songs": songs, "count": len(songs)}


@router.post("/exported-songs/reprocess")
async def api_exported_songs_reprocess(request: Request) -> dict[str, Any]:
    body = await request.json()
    song_path_raw = str(body.get("path") or "").strip()
    if not song_path_raw:
        raise HTTPException(400, "Missing song folder path")
    cfg = load_config()
    roots = _pick_song_roots_for_scan(cfg)
    try:
        song_dir = Path(song_path_raw).expanduser().resolve()
    except OSError as e:
        raise HTTPException(400, f"Invalid path: {e}") from e
    if not song_dir.is_dir():
        raise HTTPException(404, "Song folder not found")
    if not _is_under_any_root(song_dir, roots):
        raise HTTPException(400, "Path is not inside configured export folders")

    row = _build_exported_song_row("Detected", song_dir)
    title = str(row.get("title") or song_dir.name)
    artist = str(row.get("artist") or "")
    video_url = str(row.get("video_url") or "")
    audio_path = str(row.get("audio_path") or "")
    prior_ver = read_prior_song_version_from_song_dir(song_dir)

    if video_url:
        job = job_manager.create_job(
            title,
            artist,
            video_url,
            "url",
            video_url,
            cookiefile=cfg.cookiefile.strip() or None,
            youtube_metadata=True,
            tag_prior_song_version=prior_ver,
        )
        return {"ok": True, "mode": "youtube", "job": job}
    if audio_path:
        ap = Path(audio_path)
        up_hash: str | None = None
        try:
            if ap.is_file():
                up_hash = file_sha256(ap)
        except OSError:
            up_hash = None
        job = job_manager.create_job(
            title,
            artist,
            audio_path,
            "upload",
            audio_path,
            cookiefile=None,
            youtube_metadata=False,
            tag_prior_song_version=prior_ver,
            tag_upload_file_hash=up_hash,
        )
        return {"ok": True, "mode": "audio", "job": job}

    raise HTTPException(400, "No reprocessable source found (VIDEOURL or audio file)")


@router.post("/exported-songs/reprocess-all")
def api_exported_songs_reprocess_all() -> dict[str, Any]:
    cfg = load_config()
    songs = _scan_exported_songs(cfg)
    queued = 0
    failed = 0
    skipped = 0

    for row in songs:
        title = str(row.get("title") or row.get("name") or "Unknown").strip()
        artist = str(row.get("artist") or "").strip()
        video_url = str(row.get("video_url") or "").strip()
        audio_path = str(row.get("audio_path") or "").strip()
        song_dir: Path | None = None
        try:
            raw_path = str(row.get("path") or "").strip()
            if raw_path:
                song_dir = Path(raw_path).expanduser().resolve()
        except OSError:
            song_dir = None
        prior_ver = read_prior_song_version_from_song_dir(song_dir) if song_dir and song_dir.is_dir() else None

        try:
            if video_url:
                job_manager.create_job(
                    title,
                    artist,
                    video_url,
                    "url",
                    video_url,
                    cookiefile=cfg.cookiefile.strip() or None,
                    youtube_metadata=True,
                    tag_prior_song_version=prior_ver,
                )
                queued += 1
                continue
            if audio_path:
                ap = Path(audio_path)
                up_hash: str | None = None
                try:
                    if ap.is_file():
                        up_hash = file_sha256(ap)
                except OSError:
                    up_hash = None
                job_manager.create_job(
                    title,
                    artist,
                    audio_path,
                    "upload",
                    audio_path,
                    cookiefile=None,
                    youtube_metadata=False,
                    tag_prior_song_version=prior_ver,
                    tag_upload_file_hash=up_hash,
                )
                queued += 1
                continue
            skipped += 1
        except Exception:
            failed += 1
            log.exception("bulk reprocess enqueue failed for %s", row.get("path"))

    return {
        "ok": True,
        "queued": queued,
        "failed": failed,
        "skipped": skipped,
        "total": len(songs),
    }


@router.get("/jobs/export")
def api_export_jobs() -> FileResponse:
    cfg = load_config()
    jobs = job_manager.list_jobs()
    payload = {
        "format": "ultrasinger-webui-jobs",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "jobs": jobs,
    }
    temp = cfg.effective_data_dir() / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    out = temp / "jobs_export.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return FileResponse(out, filename="ultrasinger_jobs_export.json", media_type="application/json")


@router.post("/jobs/import")
async def api_import_jobs(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(400, f"Invalid JSON file: {e}") from e

    if isinstance(data, dict):
        jobs_raw = data.get("jobs", [])
    elif isinstance(data, list):
        jobs_raw = data
    else:
        raise HTTPException(400, "JSON must be a list of jobs or an object with a 'jobs' array")

    if not isinstance(jobs_raw, list):
        raise HTTPException(400, "'jobs' must be an array")

    imported = 0
    skipped = 0
    for row in jobs_raw:
        if not isinstance(row, dict):
            skipped += 1
            continue
        title = str(row.get("title") or row.get("source") or "Imported job").strip()
        artist = str(row.get("artist") or "").strip()
        source = str(row.get("source") or "").strip()
        source_type = str(row.get("source_type") or "url").strip() or "url"
        input_path = str(row.get("input_path") or source).strip()
        if not input_path:
            skipped += 1
            continue
        cookiefile_raw = row.get("cookiefile")
        cookiefile = str(cookiefile_raw).strip() if cookiefile_raw is not None else ""
        cookiefile = cookiefile or None
        youtube_metadata = _truthy(row.get("youtube_metadata"))
        job_manager.create_job(
            title,
            artist,
            source or input_path,
            source_type,
            input_path,
            cookiefile=cookiefile,
            youtube_metadata=youtube_metadata,
        )
        imported += 1

    return {"ok": True, "imported": imported, "skipped": skipped, "total": len(jobs_raw)}


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
    job = job_manager.create_job(t, "", u, "url", u, cookiefile=cfg.cookiefile or None)
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
    for watch_url, hint, _artist in items:
        jobs.append(
            job_manager.create_job(hint, "", watch_url, "youtube", watch_url, cookiefile=cf)
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
    items: list[tuple[str, str, str]] = []
    try:
        items = expand_playlist(u, cf)
    except Exception:
        log.debug("expand_playlist failed; using direct URL", exc_info=True)
    if not items:
        items = [(u, title.strip() or u, "")]
    custom = title.strip()
    jobs = []
    for watch_url, hint, artist_hint in items:
        job_title = custom if len(items) == 1 and custom else hint
        job_artist = artist_hint.strip() if use_yt_meta else ""
        jobs.append(
            job_manager.create_job(
                job_title,
                job_artist,
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
        "",
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
    try:
        job["tag_upload_file_hash"] = file_sha256(dest)
    except OSError:
        pass
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


@router.post("/jobs/{job_id}/prioritize")
def api_prioritize(job_id: str) -> dict[str, Any]:
    _require_job_id(job_id)
    ok = job_manager.move_queued_job_to_front(job_id)
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


@router.post("/jobs/control/retry-failed")
def api_retry_failed() -> dict[str, Any]:
    n = job_manager.retry_all_failed()
    return {"retried": n}


@router.post("/jobs/control/resume")
def api_resume() -> dict[str, str]:
    job_manager.resume_processing()
    return {"status": "resumed"}


@router.post("/jobs/control/clear-all")
def api_clear_all() -> dict[str, Any]:
    n = job_manager.clear_all_finished()
    return {"cleared": n}


@router.post("/jobs/control/clear-failed")
def api_clear_failed() -> dict[str, Any]:
    n = job_manager.clear_all_failed()
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
