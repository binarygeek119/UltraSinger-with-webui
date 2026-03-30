"""Fetch plain lyrics from public metadata/Lyrics APIs (best-effort, no API keys)."""

from __future__ import annotations

import gzip
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("ultrasinger.webui.lyrics_remote")

USER_AGENT = "UltraSinger-WebUI/lyrics-compare (+https://github.com/rakuri255/UltraSinger)"

# Shazam serves full lyric text in JSON-LD on track pages when the client looks like mobile Safari.
SHAZAM_PAGE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


@dataclass
class LyricsFetchResult:
    source: str
    ok: bool
    lyrics: str
    error: str
    raw_meta: dict[str, Any] | None = None


def _http_get(url: str, timeout: float = 14.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — curated URLs only
        return int(resp.getcode() or 200), resp.read()


def _http_get_with_headers(
    url: str,
    headers: dict[str, str],
    timeout: float = 14.0,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — curated URLs only
        code = int(resp.getcode() or 200)
        raw = resp.read()
        if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
            try:
                raw = gzip.decompress(raw)
            except OSError:
                pass
        return code, raw


def _http_get_json(url: str, timeout: float = 14.0) -> Any:
    try:
        code, body = _http_get(url, timeout)
    except urllib.error.HTTPError as e:
        raise e
    if code != 200:
        raise urllib.error.URLError(f"HTTP {code}")
    return json.loads(body.decode("utf-8", errors="replace"))


def _normalize_lyrics(text: str) -> str:
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t


def _norm_tokens(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _score_itunes_row(row: dict[str, Any], artist: str, title: str) -> float:
    an = _norm_tokens(str(row.get("artistName") or ""))
    tn = _norm_tokens(str(row.get("trackName") or ""))
    wa = _norm_tokens(artist)
    wt = _norm_tokens(title)
    score = 0.0
    if wa and wa == an:
        score += 4.0
    elif wa and (wa in an or an in wa):
        score += 2.0
    if wt and wt == tn:
        score += 4.0
    elif wt and (wt in tn or tn in wt):
        score += 2.0
    return score


def _pick_itunes_track(
    rows: list[Any],
    artist: str,
    title: str,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_s = -1.0
    for row in rows:
        if not isinstance(row, dict) or row.get("trackId") is None:
            continue
        s = _score_itunes_row(row, artist, title)
        if s > best_s:
            best_s = s
            best = row
    return best


def _lyrics_from_json_ld_node(obj: Any) -> str | None:
    if isinstance(obj, dict):
        if obj.get("@type") == "MusicComposition":
            lyrics = obj.get("lyrics")
            if isinstance(lyrics, dict):
                text = lyrics.get("text")
                if isinstance(text, str) and len(text.strip()) > 12:
                    return text
        graph = obj.get("@graph")
        if graph is not None:
            found = _lyrics_from_json_ld_node(graph)
            if found:
                return found
        for v in obj.values():
            found = _lyrics_from_json_ld_node(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _lyrics_from_json_ld_node(item)
            if found:
                return found
    return None


def _lyrics_from_shazam_track_html(html: str) -> str | None:
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        ly = _lyrics_from_json_ld_node(data)
        if ly:
            return ly
    return None


def fetch_shazam(artist: str, title: str) -> LyricsFetchResult:
    """
    Shazam track page (mobile HTML) exposes lyrics in JSON-LD under ``MusicComposition.lyrics``.

    Resolve the track via the public iTunes Search API, map to Shazam with ``meta2``, then fetch
    the Shazam track URL with a mobile User-Agent so the lyric payload is present.
    """
    artist_t = (artist or "").strip()
    title_t = (title or "").strip()
    if not artist_t or not title_t:
        return LyricsFetchResult("Shazam", False, "", "Artist and title required")

    term = f"{artist_t} {title_t}"
    itunes_q = urllib.parse.urlencode(
        {"term": term, "entity": "song", "limit": "15", "country": "US"}
    )
    itunes_url = f"https://itunes.apple.com/search?{itunes_q}"
    try:
        code, body = _http_get_with_headers(
            itunes_url,
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=16.0,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.debug("shazam itunes search failed: %s", e)
        return LyricsFetchResult("Shazam", False, "", str(e) or type(e).__name__)
    if code != 200:
        return LyricsFetchResult("Shazam", False, "", f"iTunes search HTTP {code}")
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        return LyricsFetchResult("Shazam", False, "", f"iTunes JSON: {e}")
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return LyricsFetchResult("Shazam", False, "", "No iTunes search results")

    pick = _pick_itunes_track(rows, artist_t, title_t)
    if pick is None:
        return LyricsFetchResult("Shazam", False, "", "No usable iTunes song rows")

    adam_id = str(pick.get("trackId"))
    meta2_url = f"https://www.shazam.com/services/meta2/en-US/US/{adam_id}"
    try:
        mcode, mbody = _http_get_with_headers(
            meta2_url,
            {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "en-US",
                "Accept-Encoding": "gzip, deflate",
                "Referer": "https://www.shazam.com/",
            },
            timeout=14.0,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.debug("shazam meta2 failed: %s", e)
        return LyricsFetchResult("Shazam", False, "", str(e) or type(e).__name__)
    if mcode != 200:
        return LyricsFetchResult("Shazam", False, "", f"Shazam meta2 HTTP {mcode}")
    try:
        meta = json.loads(mbody.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        return LyricsFetchResult("Shazam", False, "", f"meta2 JSON: {e}")
    if not isinstance(meta, dict):
        return LyricsFetchResult("Shazam", False, "", "Bad meta2 response")

    web_url = str(meta.get("webUrl") or "").strip()
    shazam_key = str(meta.get("id") or "").strip()
    if not web_url:
        if shazam_key:
            web_url = f"https://www.shazam.com/track/{shazam_key}"
        else:
            return LyricsFetchResult("Shazam", False, "", "No Shazam track URL from meta2")
    page_url = web_url.split("?")[0]

    try:
        pcode, html_bytes = _http_get_with_headers(
            page_url,
            {
                "User-Agent": SHAZAM_PAGE_UA,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Referer": "https://www.shazam.com/",
            },
            timeout=30.0,
        )
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log.debug("shazam track page failed: %s", e)
        return LyricsFetchResult("Shazam", False, "", str(e) or type(e).__name__)
    if pcode != 200:
        return LyricsFetchResult("Shazam", False, "", f"Shazam track page HTTP {pcode}")

    html = html_bytes.decode("utf-8", errors="replace")
    lyrics_raw = _lyrics_from_shazam_track_html(html)
    base_meta: dict[str, Any] = {
        "itunesTrackId": pick.get("trackId"),
        "itunesTrackName": pick.get("trackName"),
        "itunesArtistName": pick.get("artistName"),
        "shazamKey": shazam_key or None,
        "shazamUrl": page_url,
    }
    if not lyrics_raw:
        return LyricsFetchResult(
            "Shazam",
            False,
            "",
            "No lyrics embedded in Shazam page (JSON-LD)",
            raw_meta=base_meta,
        )
    lyrics = _normalize_lyrics(lyrics_raw)
    if not lyrics:
        return LyricsFetchResult(
            "Shazam",
            False,
            "",
            "Empty lyrics after normalize",
            raw_meta=base_meta,
        )
    return LyricsFetchResult("Shazam", True, lyrics, "", raw_meta=base_meta)


def fetch_lrclib(artist: str, title: str) -> LyricsFetchResult:
    """LRCLIB (https://lrclib.net/) — search by artist + track."""
    artist_t = (artist or "").strip()
    title_t = (title or "").strip()
    if not artist_t or not title_t:
        return LyricsFetchResult("LRCLIB", False, "", "Artist and title required")
    q = urllib.parse.urlencode(
        {
            "artist_name": artist_t,
            "track_name": title_t,
        }
    )
    url = f"https://lrclib.net/api/search?{q}"
    try:
        data = _http_get_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        log.debug("lrclib fetch failed: %s", e)
        return LyricsFetchResult("LRCLIB", False, "", str(e) or type(e).__name__)
    if not isinstance(data, list) or not data:
        return LyricsFetchResult("LRCLIB", False, "", "No matches")
    pick: dict[str, Any] | None = None
    for row in data:
        if not isinstance(row, dict):
            continue
        pl = row.get("plainLyrics")
        if isinstance(pl, str) and pl.strip():
            pick = row
            break
    if pick is None:
        return LyricsFetchResult("LRCLIB", False, "", "No plain lyrics in results")
    lyrics = _normalize_lyrics(str(pick.get("plainLyrics") or ""))
    meta = {
        "id": pick.get("id"),
        "trackName": pick.get("trackName"),
        "artistName": pick.get("artistName"),
        "albumName": pick.get("albumName"),
    }
    return LyricsFetchResult("LRCLIB", True, lyrics, "", raw_meta=meta)


def fetch_lyrics_ovh(artist: str, title: str) -> LyricsFetchResult:
    """lyrics.ovh public API."""
    artist_t = (artist or "").strip()
    title_t = (title or "").strip()
    if not artist_t or not title_t:
        return LyricsFetchResult("lyrics.ovh", False, "", "Artist and title required")
    path_a = urllib.parse.quote(artist_t, safe="")
    path_t = urllib.parse.quote(title_t, safe="")
    url = f"https://api.lyrics.ovh/v1/{path_a}/{path_t}"
    try:
        data = _http_get_json(url)
    except urllib.error.HTTPError as e:
        return LyricsFetchResult("lyrics.ovh", False, "", f"HTTP {e.code}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        log.debug("lyrics.ovh fetch failed: %s", e)
        return LyricsFetchResult("lyrics.ovh", False, "", str(e) or type(e).__name__)
    if not isinstance(data, dict):
        return LyricsFetchResult("lyrics.ovh", False, "", "Bad response")
    if data.get("error"):
        return LyricsFetchResult("lyrics.ovh", False, "", str(data.get("error")))
    lyrics = _normalize_lyrics(str(data.get("lyrics") or ""))
    if not lyrics:
        return LyricsFetchResult("lyrics.ovh", False, "", "Empty lyrics")
    return LyricsFetchResult("lyrics.ovh", True, lyrics, "", raw_meta={"source": data.get("url")})


def fetch_all_sources(artist: str, title: str) -> list[LyricsFetchResult]:
    out: list[LyricsFetchResult] = []
    out.append(fetch_lrclib(artist, title))
    out.append(fetch_lyrics_ovh(artist, title))
    out.append(fetch_shazam(artist, title))
    return out

