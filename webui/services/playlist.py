"""Expand playlist URLs into individual video URLs using yt-dlp."""

from __future__ import annotations

from typing import Optional

import yt_dlp


def expand_playlist(url: str, cookiefile: Optional[str] = None) -> list[tuple[str, str]]:
    """Return list of (watch_url, title_hint) for each entry."""

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

    out: list[tuple[str, str]] = []
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
            u = entry_url(e)
            if u:
                title = (e.get("title") or e.get("id") or u)[:200]
                out.append((u, str(title)))
    else:
        u = info.get("webpage_url") or url
        title = info.get("title") or u
        out.append((u, str(title)[:200]))

    return out
