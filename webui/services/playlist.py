"""Expand playlist URLs into individual video URLs using yt-dlp."""

from __future__ import annotations

from typing import Optional

import yt_dlp


def _is_blocked_playlist_entry_title(title: str) -> bool:
    t = (title or "").strip().lower()
    return t in {
        "[private video]",
        "[deleted video]",
        "private video",
        "deleted video",
    }


def expand_playlist(url: str, cookiefile: Optional[str] = None) -> list[tuple[str, str, str]]:
    """Return list of (watch_url, title_hint, artist_hint) for each entry."""

    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        # Match UltraSinger youtube.py: allow fetching current EJS solvers from GitHub
        "remote_components": ["ejs:github"],
    }
    if cookiefile:
        opts["cookiefile"] = cookiefile

    out: list[tuple[str, str, str]] = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        return out

    def entry_url(e: dict) -> Optional[str]:
        if not e:
            return None
        if e.get("url") and str(e["url"]).startswith("http"):
            return e["url"]
        vid = e.get("id")
        if vid and (e.get("ie_key") == "Youtube" or "youtube" in (e.get("webpage_url") or "")):
            return f"https://www.youtube.com/watch?v={vid}"
        return e.get("webpage_url")

    entries = info.get("entries")
    if entries:
        for e in entries:
            if e is None:
                continue
            title_raw = str(e.get("title") or "")
            if _is_blocked_playlist_entry_title(title_raw):
                continue
            u = entry_url(e)
            if u:
                title = (title_raw or e.get("id") or u)[:200]
                artist = str(e.get("channel") or e.get("uploader") or e.get("creator") or "").strip()
                out.append((u, str(title), artist))
    else:
        u = info.get("webpage_url") or url
        title = info.get("title") or u
        artist = str(info.get("channel") or info.get("uploader") or info.get("creator") or "").strip()
        out.append((u, str(title)[:200], artist))

    return out
