"""ZIP download names and archive layout aligned with song export folders."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_zip_basename(name: str) -> str:
    """Safe single path segment for ZIP entries and download filenames."""
    s = _BAD_FILENAME_CHARS.sub("-", (name or "").strip()).strip(" .")
    if not s:
        return "download"
    if len(s) > 200:
        s = s[:200].rstrip(" .")
    return s


def primary_output_song_folder(output_root: Path) -> str | None:
    """UltraSinger writes under ``output/<Artist - Title>/``. Return that folder name if present."""
    if not output_root.is_dir():
        return None
    subs = sorted(p.name for p in output_root.iterdir() if p.is_dir())
    if not subs:
        return None
    return subs[0]


def per_job_zip_download_filename(job: dict[str, Any], output_root: Path) -> str:
    """Suggested ``Content-Disposition`` filename: ``<song folder>.zip``."""
    folder = primary_output_song_folder(output_root)
    base = folder or (job.get("title") or "").strip() or job.get("job_id", "download")
    return sanitize_zip_basename(base) + ".zip"


def logical_root_for_job_output(output_root: Path, job: dict[str, Any], job_id: str) -> str:
    """Top-level folder name inside this job's output (matches export-folder song name)."""
    folder = primary_output_song_folder(output_root)
    if folder:
        return folder
    return sanitize_zip_basename((job.get("title") or "").strip() or job_id)


def remap_inner_arc_to_root(inner: str, logical_root: str, arc_root: str) -> str:
    """Replace first path segment of *inner* with *arc_root* when it matches *logical_root*."""
    inner = str(inner).replace("\\", "/")
    if inner == logical_root:
        return arc_root
    if inner.startswith(logical_root + "/"):
        return arc_root + inner[len(logical_root) :]
    return f"{arc_root}/{inner}"
