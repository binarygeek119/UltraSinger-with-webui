"""API: compare exported UltraStar lyrics with public lyric databases."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from webui.config import load_config
from webui.services.lyrics_remote import LyricsFetchResult, fetch_all_sources
from webui.ultrastar_plain_lyrics import (
    align_reference_chips_to_run_indices,
    apply_words_to_txt_file,
    correction_text_to_words,
    merged_ultrasinger_display_plain,
    pick_ultrastar_txt,
    plain_lyrics_from_txt_path,
    reference_chips_for_compare,
    split_replacement_across_syllable_run,
    strip_tilde_for_reference_display,
    syllable_run_length_at,
    syllable_runs_for_compare_view,
)

router = APIRouter(prefix="/api/lyrics-compare", tags=["lyrics-compare"])


@router.post("/split-replacement")
async def api_split_replacement(request: Request) -> dict[str, Any]:
    """Split one replacement string across consecutive note fragments (multi-line syllable with ``~``)."""
    body = await request.json()
    words = body.get("words")
    try:
        start = int(body.get("start", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "start must be an integer") from None
    replacement = str(body.get("replacement") or "")
    if not isinstance(words, list):
        raise HTTPException(400, "words must be an array of strings")
    str_words = [str(w) for w in words]
    if start < 0 or (str_words and start >= len(str_words)):
        raise HTTPException(400, "start out of range")
    if not str_words:
        return {"start": start, "run_length": 0, "parts": []}
    run = syllable_run_length_at(str_words, start)
    run = min(run, len(str_words) - start)
    old_parts = str_words[start : start + run]
    parts = split_replacement_across_syllable_run(replacement, old_parts)
    return {"start": start, "run_length": run, "parts": parts}


def _serialize_lyrics_source(r: LyricsFetchResult) -> dict[str, Any]:
    raw = r.lyrics or ""
    disp = strip_tilde_for_reference_display(raw) if r.ok else ""
    return {
        "source": r.source,
        "ok": r.ok,
        "lyrics": r.lyrics,
        "lyrics_display": disp,
        "error": r.error,
        "meta": r.raw_meta,
    }


def _optional_song_folder(body: dict[str, Any]) -> Path | None:
    raw = str(body.get("path") or "").strip()
    if not raw:
        return None
    try:
        p = Path(raw).expanduser().resolve()
    except OSError as e:
        raise HTTPException(400, f"Invalid path: {e}") from e
    if not p.is_dir():
        raise HTTPException(404, "Song folder not found")
    from webui.routes.api import _is_under_any_root, _pick_song_roots_for_scan

    cfg = load_config()
    roots = _pick_song_roots_for_scan(cfg)
    if not _is_under_any_root(p, roots):
        raise HTTPException(400, "Path is not inside configured export folders")
    return p


def _artist_title_for_lookup(body: dict[str, Any], folder: Path | None) -> tuple[str, str]:
    artist = str(body.get("artist") or "").strip()
    title = str(body.get("title") or "").strip()
    if artist and title:
        return artist, title
    if folder is not None:
        name = folder.name
        if " - " in name:
            a, t = name.split(" - ", 1)
            return a.strip(), t.strip()
        return "", name.strip()
    raise HTTPException(
        400,
        "Enter artist and song title for lookup, or select an exported song folder (name used as fallback).",
    )


def _song_folder_from_payload(body: dict[str, Any]) -> Path:
    raw = str(body.get("path") or "").strip()
    if not raw:
        raise HTTPException(400, "Missing song folder path")
    try:
        p = Path(raw).expanduser().resolve()
    except OSError as e:
        raise HTTPException(400, f"Invalid path: {e}") from e
    if not p.is_dir():
        raise HTTPException(404, "Song folder not found")
    from webui.routes.api import _is_under_any_root, _pick_song_roots_for_scan

    cfg = load_config()
    roots = _pick_song_roots_for_scan(cfg)
    if not _is_under_any_root(p, roots):
        raise HTTPException(400, "Path is not inside configured export folders")
    return p


@router.post("/generated")
async def api_lyrics_compare_generated(request: Request) -> dict[str, Any]:
    body = await request.json()
    folder = _song_folder_from_payload(body)
    txt = pick_ultrastar_txt(folder)
    if not txt or not txt.is_file():
        raise HTTPException(404, "No UltraStar txt (e.g. notes.txt) in this folder")
    plain, words = plain_lyrics_from_txt_path(txt)
    return {
        "txt_path": str(txt),
        "plain": plain,
        "words": words,
        "word_count": len(words),
        "syllable_runs": syllable_runs_for_compare_view(words),
        "merged_ultrasinger_display": merged_ultrasinger_display_plain(words),
    }


@router.post("/session")
async def api_lyrics_compare_session(request: Request) -> dict[str, Any]:
    """Return generated lyrics from disk plus remote database hits (one round trip)."""
    body = await request.json()
    folder = _song_folder_from_payload(body)
    txt = pick_ultrastar_txt(folder)
    if not txt or not txt.is_file():
        raise HTTPException(404, "No UltraStar txt (e.g. notes.txt) in this folder")
    plain, words = plain_lyrics_from_txt_path(txt)
    artist, title = _artist_title_for_lookup(body, folder)
    results = fetch_all_sources(artist, title)
    return {
        "path": str(folder),
        "artist": artist,
        "title": title,
        "txt_path": str(txt),
        "generated_plain": plain,
        "generated_words": words,
        "syllable_runs": syllable_runs_for_compare_view(words),
        "merged_ultrasinger_display": merged_ultrasinger_display_plain(words),
        "sources": [_serialize_lyrics_source(r) for r in results],
    }


@router.post("/lookup")
async def api_lyrics_compare_lookup(request: Request) -> dict[str, Any]:
    """Fetch reference lyrics using artist + title in the body and/or an optional export folder path."""
    body = await request.json()
    folder = _optional_song_folder(body)
    artist, title = _artist_title_for_lookup(body, folder)
    results = fetch_all_sources(artist, title)
    return {
        "artist": artist,
        "title": title,
        "path": str(folder) if folder else None,
        "sources": [_serialize_lyrics_source(r) for r in results],
    }


@router.post("/apply-words")
async def api_lyrics_compare_apply_words(request: Request) -> dict[str, Any]:
    body = await request.json()
    folder = _song_folder_from_payload(body)
    txt_path_raw = str(body.get("txt_path") or "").strip()
    plain = body.get("plain_text")
    words_in = body.get("words")
    if txt_path_raw:
        try:
            txt = Path(txt_path_raw).expanduser().resolve()
        except OSError as e:
            raise HTTPException(400, f"Invalid txt path: {e}") from e
    else:
        picked = pick_ultrastar_txt(folder)
        if not picked:
            raise HTTPException(404, "No UltraStar txt in folder")
        txt = picked
    if not txt.is_file():
        raise HTTPException(404, "Txt file not found")
    from webui.routes.api import _is_under_any_root, _pick_song_roots_for_scan

    cfg = load_config()
    roots = _pick_song_roots_for_scan(cfg)
    if not _is_under_any_root(txt.parent, roots):
        raise HTTPException(400, "Txt file must live under a configured export folder")
    if isinstance(words_in, list):
        words = [str(w) for w in words_in]
    elif plain is not None:
        words = correction_text_to_words(str(plain))
    else:
        raise HTTPException(400, "Provide words array or plain_text")
    res = apply_words_to_txt_file(txt, words)
    if res.get("ok") != 1:
        raise HTTPException(400, str(res.get("error") or "Could not apply words"))
    anew, words_after = plain_lyrics_from_txt_path(txt)
    runs = syllable_runs_for_compare_view(words_after)
    return {
        **res,
        "txt_path": str(txt),
        "plain_after": anew,
        "generated_words": words_after,
        "syllable_runs": runs,
        "merged_ultrasinger_display": merged_ultrasinger_display_plain(words_after),
    }


@router.post("/syllable-view")
async def api_syllable_view(request: Request) -> dict[str, Any]:
    """Merged UltraSinger column lines from per-note words (view only; file stays one line per note)."""
    body = await request.json()
    words = body.get("words")
    if not isinstance(words, list):
        raise HTTPException(400, "words must be an array of strings")
    str_words = [str(w) for w in words]
    runs = syllable_runs_for_compare_view(str_words)
    ref_text = str(body.get("reference_text") or "")
    chips = reference_chips_for_compare(ref_text)
    chip_run_indices = (
        align_reference_chips_to_run_indices(str_words, chips)
        if chips
        else []
    )
    return {
        "syllable_runs": runs,
        "merged_ultrasinger_display": "\n".join(str(r["display"]) for r in runs),
        "reference_chips": chips,
        "chip_run_indices": chip_run_indices,
    }
