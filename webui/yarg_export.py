"""Map UltraSinger job outputs to YARG-style flat file names for folder export."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from webui.output_bundle import iter_job_output_files

VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v"})
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"})
AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".wma"})


def _basename_only(rel: str) -> str:
    return Path(str(rel).replace("\\", "/")).name


def _stem(name: str) -> str:
    return Path(name).stem


def _suffix(name: str) -> str:
    return Path(name).suffix.lower()


def _is_bg_image_name(name: str) -> bool:
    stem = _stem(name)
    return stem.endswith(" [BG]") or "[BG]" in stem


def plan_yarg_flat_copies(
    items: list[tuple[Path, str]],
    song_folder_name: str,
) -> list[tuple[Path, str]]:
    """Choose files from a song bundle and return (src, dest_filename) for a flat YARG layout.

    - One audio → ``guitar.<ext>`` (prefers ``[Instrumental]`` stem, else main mix, else first non-vocal audio).
    - One video → ``background.<ext>``
    - One ``*.txt`` → ``notes.txt``
    - One image → ``album.<ext>``
    """
    if not items:
        return []

    by_txt: list[tuple[Path, str]] = []
    by_vid: list[tuple[Path, str]] = []
    by_img: list[tuple[Path, str]] = []
    by_aud: list[tuple[Path, str]] = []

    for src, rel in items:
        name = _basename_only(rel)
        suf = _suffix(name)
        if suf == ".txt":
            by_txt.append((src, name))
        elif suf in VIDEO_SUFFIXES:
            by_vid.append((src, name))
        elif suf in IMAGE_SUFFIXES:
            by_img.append((src, name))
        elif suf in AUDIO_SUFFIXES:
            by_aud.append((src, name))

    def prefer_stem_match(cands: list[tuple[Path, str]], folder: str) -> tuple[Path, str] | None:
        for src, name in cands:
            if _stem(name) == folder:
                return (src, name)
        return None

    out: list[tuple[Path, str]] = []

    # notes.txt
    if by_txt:
        pick = prefer_stem_match(by_txt, song_folder_name)
        if not pick:
            pick = sorted(by_txt, key=lambda x: x[1].lower())[0]
        out.append((pick[0], "notes.txt"))

    # background.<ext>
    # Include both media types when present:
    # - video as background.<video_ext>
    # - generated BG image (* [BG].jpg / [BG].*) as background.<image_ext>
    if by_vid:
        pick_vid = prefer_stem_match(by_vid, song_folder_name)
        if not pick_vid:
            pick_vid = sorted(by_vid, key=lambda x: x[1].lower())[0]
        out.append((pick_vid[0], f"background{_suffix(pick_vid[1])}"))

    bg_img = next((x for x in by_img if _is_bg_image_name(x[1])), None)
    if bg_img:
        out.append((bg_img[0], f"background{_suffix(bg_img[1])}"))

    # album.<ext>
    if by_img:
        pick = prefer_stem_match(by_img, song_folder_name)
        if not pick:
            pick = sorted(by_img, key=lambda x: x[1].lower())[0]
        out.append((pick[0], f"album{_suffix(pick[1])}"))

    # guitar.<ext>
    if by_aud:
        instrumental = next((x for x in by_aud if "[Instrumental]" in x[1]), None)
        if instrumental:
            pick = instrumental
        else:
            main = prefer_stem_match(by_aud, song_folder_name)
            if main:
                pick = main
            else:
                non_vocals = [x for x in by_aud if "[Vocals]" not in x[1]]
                pick = non_vocals[0] if non_vocals else by_aud[0]
        out.append((pick[0], f"guitar{_suffix(pick[1])}"))

    # Deduplicate by src (same file must not map to two roles)
    seen_src: set[Path] = set()
    deduped: list[tuple[Path, str]] = []
    for src, dest in out:
        key = src.resolve()
        if key in seen_src:
            continue
        seen_src.add(key)
        deduped.append((src, dest))
    return deduped


def group_output_by_song_folder(
    output_root: Path,
    job_id: str,
    *,
    exclude_stem_tracks: bool,
    exclude_midi: bool,
) -> dict[str, list[tuple[Path, str]]]:
    """Group ``iter_job_output_files`` by first path segment (song folder) or *job_id* if files are flat."""
    grouped: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    for src, rel in iter_job_output_files(
        output_root,
        exclude_stem_tracks=exclude_stem_tracks,
        exclude_midi=exclude_midi,
    ):
        rel_norm = str(rel).replace("\\", "/")
        if "/" in rel_norm:
            top, _ = rel_norm.split("/", 1)
        else:
            top = job_id
        grouped[top].append((src, rel_norm))
    return grouped


def iter_yarg_export_style_zip_entries(
    output_root: Path,
    job_id: str,
    *,
    exclude_stem_tracks: bool,
    zip_arc_root: str | None = None,
) -> list[tuple[Path, str]]:
    """``(path, arcname)`` matching YARG folder export: ``<folder>/guitar.*``, ``notes.txt``, etc.

    Uses ``exclude_midi=True`` like the on-disk YARG export. *zip_arc_root* overrides the folder name
    inside the archive (e.g. bulk download collision suffix); file picking still uses the real *top* key.
    """
    grouped = group_output_by_song_folder(
        output_root,
        job_id,
        exclude_stem_tracks=exclude_stem_tracks,
        exclude_midi=True,
    )
    out: list[tuple[Path, str]] = []
    for top, items in grouped.items():
        if not top:
            continue
        folder = zip_arc_root if zip_arc_root is not None else top
        for src, dest_name in plan_yarg_flat_copies(items, top):
            out.append((src, f"{folder}/{dest_name}"))
    return out


def build_yarg_zip_album_arc_overrides(
    output_root: Path,
    *,
    exclude_stem_tracks: bool,
    exclude_midi: bool,
    job_id_fallback_top: str = "",
) -> dict[Path, str]:
    """When YARG-style ZIP is used (stem tracks omitted), map cover file path → zip entry ``<song>/album.<ext>``.

    Same cover choice as :func:`plan_yarg_flat_copies`. Other files keep their relative paths.
    """
    if not exclude_stem_tracks:
        return {}
    grouped = group_output_by_song_folder(
        output_root,
        job_id_fallback_top,
        exclude_stem_tracks=exclude_stem_tracks,
        exclude_midi=exclude_midi,
    )

    overrides: dict[Path, str] = {}
    for top, items in grouped.items():
        if not top:
            continue
        for src, dest in plan_yarg_flat_copies(items, top):
            if dest.startswith("album"):
                overrides[src.resolve()] = f"{top}/{dest}"
                break
    return overrides
