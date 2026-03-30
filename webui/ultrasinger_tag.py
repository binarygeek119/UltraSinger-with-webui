"""Write `ultrasinger-tag.txt` into each song output folder after a successful job."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Optional

from packaging.version import InvalidVersion, Version

log = logging.getLogger("ultrasinger.webui.tag")

TAG_FILENAME = "ultrasinger-tag.txt"
_AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".wma"})
_SONG_VERSION_RE = re.compile(r"(?im)^song_version:\s*(.+)\s*$")


def file_sha256(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def load_ultrasinger_app_version(repo_root: Path) -> str:
    try:
        import importlib.util

        p = (repo_root / "src" / "Settings.py").resolve()
        if not p.is_file():
            return "unknown"
        spec = importlib.util.spec_from_file_location("us_settings_tag", p)
        if spec is None or spec.loader is None:
            return "unknown"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return str(getattr(getattr(mod, "Settings", None), "APP_VERSION", "unknown") or "unknown")
    except Exception:
        log.debug("Could not read UltraSinger APP_VERSION", exc_info=True)
        return "unknown"


def bump_song_version(prior: Optional[str]) -> str:
    if not prior or not str(prior).strip():
        return "1.0.0"
    s = str(prior).strip()
    try:
        v = Version(s)
        rel = list(v.release)
        while len(rel) < 3:
            rel.append(0)
        major, minor, micro = int(rel[0]), int(rel[1]), int(rel[2])
        return f"{major}.{minor}.{micro + 1}"
    except (InvalidVersion, ValueError):
        return "1.0.0"


def read_prior_song_version_from_song_dir(song_dir: Path) -> Optional[str]:
    tag = song_dir / TAG_FILENAME
    if not tag.is_file():
        return None
    m = _SONG_VERSION_RE.search(tag.read_text(encoding="utf-8", errors="replace"))
    if not m:
        return None
    return m.group(1).strip() or None


def read_prior_song_version_from_job_output(jobs_dir: Path, job_id: str) -> Optional[str]:
    out = jobs_dir / job_id / "output"
    if not out.is_dir():
        return None
    for song_dir in sorted(p for p in out.iterdir() if p.is_dir()):
        ver = read_prior_song_version_from_song_dir(song_dir)
        if ver:
            return ver
    return None


def _list_ultrastar_txt_candidates(song_dir: Path) -> list[Path]:
    return sorted(
        p for p in song_dir.glob("*.txt") if p.name.lower() != TAG_FILENAME.lower()
    )


def _preferred_ultrastar_txt(song_dir: Path) -> Optional[Path]:
    cands = _list_ultrastar_txt_candidates(song_dir)
    if not cands:
        return None
    match = next((p for p in cands if p.stem == song_dir.name), None)
    return match or cands[0]


def _parse_txt_lines(txt_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in txt_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("#ARTIST:"):
                out["artist"] = line.split(":", 1)[1].strip()
            elif line.startswith("#TITLE:"):
                out["title"] = line.split(":", 1)[1].strip()
            elif line.startswith("#MP3:"):
                out["mp3"] = line.split(":", 1)[1].strip()
            elif line.startswith("#AUDIO:"):
                out["audio"] = line.split(":", 1)[1].strip()
    except OSError:
        pass
    return out


def _resolve_finished_audio_path(song_dir: Path, txt_meta: dict[str, str]) -> Optional[Path]:
    rel = (txt_meta.get("mp3") or txt_meta.get("audio") or "").strip()
    if rel:
        cand = (song_dir / rel).resolve()
        try:
            if cand.is_file():
                return cand
        except OSError:
            pass
    audio_files = [
        p for p in song_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _AUDIO_SUFFIXES
    ]
    if not audio_files:
        return None
    base = song_dir.name
    exact = next((p for p in audio_files if p.stem == base), None)
    if exact:
        return exact
    non_stems = [p for p in audio_files if "[Vocals]" not in p.name and "[Instrumental]" not in p.name]
    if non_stems:
        return sorted(non_stems, key=lambda x: x.name.lower())[0]
    return sorted(audio_files, key=lambda x: x.name.lower())[0]


def _dash(v: Optional[str]) -> str:
    s = (v or "").strip()
    return s if s else "-"


def _format_tag_file(
    *,
    song_title: str,
    artist_name: str,
    youtube_url: str,
    upload_full_filename: str,
    upload_file_hash: str,
    finished_song_audio_hash: str,
    ultrasinger_version: str,
    ultrasinger_webui_version: str,
    song_version: str,
) -> str:
    lines = [
        "# UltraSinger run metadata (managed by UltraSinger WebUI)",
        f"song_title: {song_title}",
        f"artist_name: {artist_name}",
        f"youtube_url: {_dash(youtube_url)}",
        f"upload_full_filename: {_dash(upload_full_filename)}",
        f"upload_file_hash: {_dash(upload_file_hash)}",
        f"finished_song_audio_hash: {_dash(finished_song_audio_hash)}",
        f"ultrasinger_version: {ultrasinger_version}",
        f"ultrasinger_webui_version: {ultrasinger_webui_version}",
        f"song_version: {song_version}",
        "",
    ]
    return "\n".join(lines)


def write_ultrasinger_tags_after_job(job: dict, output_root: Path, repo_root: Path, webui_version: str) -> None:
    """Create or replace ``ultrasinger-tag.txt`` in each song subfolder under *output_root*."""
    if not output_root.is_dir():
        return
    st = str(job.get("source_type") or "").strip().lower()
    input_path = str(job.get("input_path") or "").strip()
    source = str(job.get("source") or "").strip()
    is_upload = st == "upload"
    youtube_url = ""
    if not is_upload and st in ("url", "youtube"):
        youtube_url = source or input_path
    upload_name = ""
    if is_upload and input_path:
        try:
            upload_name = Path(input_path).name
        except OSError:
            upload_name = input_path
    upload_hash = str(job.get("tag_upload_file_hash") or "").strip()
    prior_ver = job.get("tag_prior_song_version")
    prior_str = str(prior_ver).strip() if prior_ver else ""
    song_ver = bump_song_version(prior_str if prior_str else None)
    us_ver = load_ultrasinger_app_version(repo_root)

    for song_dir in sorted(p for p in output_root.iterdir() if p.is_dir()):
        txt_path = _preferred_ultrastar_txt(song_dir)
        meta = _parse_txt_lines(txt_path) if txt_path else {}
        title = (meta.get("title") or job.get("title") or song_dir.name or "").strip()
        artist = (meta.get("artist") or job.get("artist") or "").strip()
        audio_path = _resolve_finished_audio_path(song_dir, meta)
        audio_hash = ""
        if audio_path and audio_path.is_file():
            try:
                audio_hash = file_sha256(audio_path)
            except OSError as e:
                log.warning("Could not hash finished audio %s: %s", audio_path, e)
        body = _format_tag_file(
            song_title=title,
            artist_name=artist,
            youtube_url=youtube_url,
            upload_full_filename=upload_name,
            upload_file_hash=upload_hash,
            finished_song_audio_hash=audio_hash,
            ultrasinger_version=us_ver,
            ultrasinger_webui_version=webui_version,
            song_version=song_ver,
        )
        dest = song_dir / TAG_FILENAME
        try:
            dest.write_text(body, encoding="utf-8", newline="\n")
        except OSError as e:
            log.warning("Could not write %s: %s", dest, e)
