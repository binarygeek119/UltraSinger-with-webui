"""Detect whether a job's song folder already exists under export directories."""

from __future__ import annotations

from pathlib import Path

from webui.config import WebUIConfig


def _normalize_folder_key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def expected_song_folder_from_job(job: dict) -> str:
    """Match typical UltraSinger / export layout: ``Artist - Title`` or title-only."""
    artist = str(job.get("artist") or "").strip()
    title = str(job.get("title") or "").strip()
    if artist and title:
        return f"{artist} - {title}"
    return title or artist


def iter_export_roots(cfg: WebUIConfig) -> list[Path]:
    """YARG and UltraStar export roots (when paths are set and exist)."""
    roots: list[Path] = []
    seen: set[Path] = set()
    for raw in ((cfg.yarg_export_path or "").strip(), (cfg.ultrastar_export_path or "").strip()):
        if not raw:
            continue
        try:
            p = Path(raw).expanduser().resolve()
        except OSError:
            continue
        if p in seen:
            continue
        if p.is_dir():
            seen.add(p)
            roots.append(p)
    return roots


def export_folder_exists_for_job(cfg: WebUIConfig, job: dict) -> tuple[bool, str]:
    """
    True if a directory under an export root matches this job's expected song folder name
    (exact path, then case-insensitive / normalized name match among immediate children).
    """
    expected = expected_song_folder_from_job(job)
    if not expected:
        return False, ""
    want_key = _normalize_folder_key(expected)
    for root in iter_export_roots(cfg):
        direct = root / expected
        if direct.is_dir():
            return True, str(direct)
        try:
            for child in root.iterdir():
                if child.is_dir() and _normalize_folder_key(child.name) == want_key:
                    return True, str(child)
        except OSError:
            continue
    return False, ""


def should_skip_job_already_exported(cfg: WebUIConfig, job: dict) -> tuple[bool, str]:
    """Whether to skip running UltraSinger for this job."""
    exists, path = export_folder_exists_for_job(cfg, job)
    return exists, path
