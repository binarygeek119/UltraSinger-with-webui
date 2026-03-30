"""Extract and patch plain words from UltraStar `.txt` note lines (exported songs / notes.txt)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

_TAG_SIDECAR = "ultrasinger-tag.txt"

_NOTE_TYPES = frozenset({":", "*", "F", "R", "G"})
# Trailing ~ / fullwidth tilde / wave dash: UltraStar melisma / syllable hold (strip for lyric compare UI)
_COMPARE_TILDE_SUFFIX = re.compile(r"(?:[~～〜]|\u301C)+\s*$")


def strip_ultrastar_compare_tilde(token: str) -> str:
    """Remove UltraStar-style trailing tilde markers from a note syllable for plain-text comparison."""
    if not token:
        return ""
    s = token.strip()
    s = _COMPARE_TILDE_SUFFIX.sub("", s)
    return s.rstrip()


def trailing_compare_tilde_as_space_for_view(s: str) -> str:
    """
    Lyrics-compare view only: replace trailing melisma ``~`` with a single ASCII space so tokens
    do not run together when comparing to reference words. File data and split-replacement logic
    still use ``strip_ultrastar_compare_tilde`` (no added space).
    """
    t = (s or "").strip()
    if not t:
        return ""
    m = _COMPARE_TILDE_SUFFIX.search(t)
    if not m:
        return t
    core = t[: m.start()].rstrip()
    return (core + " ") if core else ""


def note_word_has_trailing_tilde(token: str) -> bool:
    """True if this note word ends with UltraStar melisma / syllable-end ``~`` (or fullwidth variants)."""
    if not (token or "").strip():
        return False
    return bool(_COMPARE_TILDE_SUFFIX.search((token or "").rstrip()))


def trailing_tilde_suffix(token: str) -> str:
    """Return only the trailing tilde characters (no spaces), e.g. ``~`` or ``~~``."""
    if not token:
        return ""
    m = _COMPARE_TILDE_SUFFIX.search(token.rstrip())
    if not m:
        return ""
    return re.sub(r"\s+", "", m.group(0))


def syllable_run_length_at(words: list[str], start: int) -> int:
    """
    How many consecutive note words form one logical syllable starting at *start*.

    Convention: a trailing ``~`` ends the syllable (single-note or last fragment). If the word at
    *start* already ends with ``~``, the run length is 1. Otherwise extend forward until a word ends
    with ``~``. If none do, treat as a single note (run length 1).
    """
    n = len(words)
    if start < 0 or start >= n:
        return 0
    if note_word_has_trailing_tilde(words[start]):
        return 1
    j = start
    while j < n:
        if note_word_has_trailing_tilde(words[j]):
            return j - start + 1
        j += 1
    return 1


def merge_syllable_fragments_for_display(parts: list[str]) -> str:
    """
    Join note fragments that belong to one logical syllable for the lyrics-compare UltraSinger column.

    Multiple notes in one run are separated by spaces so the line stays readable (e.g. ``And I'm``).
    Trailing UltraStar ``~`` on the last fragment becomes an extra trailing space. The file and
    correction box are unchanged (still one string per note, tildes preserved).
    """
    if not parts:
        return ""
    if len(parts) == 1:
        return trailing_compare_tilde_as_space_for_view(parts[0])
    bits = [strip_ultrastar_compare_tilde(p) for p in parts]
    bits = [b for b in bits if b != ""]
    inner = " ".join(bits)
    if note_word_has_trailing_tilde(parts[-1]):
        return inner + (" " if inner else "")
    return inner


def syllable_runs_for_compare_view(words: list[str]) -> list[dict[str, Any]]:
    """One entry per logical syllable: start note index, fragment count, merged display line."""
    runs: list[dict[str, Any]] = []
    i = 0
    n = len(words)
    while i < n:
        L = syllable_run_length_at(words, i)
        frag = words[i : i + L]
        runs.append(
            {
                "start": i,
                "length": L,
                "display": merge_syllable_fragments_for_display(frag),
            }
        )
        i += L
    return runs


def merged_ultrasinger_display_plain(words: list[str]) -> str:
    """Newline-separated merged syllables for the lyrics-compare UltraSinger column."""
    runs = syllable_runs_for_compare_view(words)
    return "\n".join(str(r["display"]) for r in runs)


def reference_chips_for_compare(reference_text: str) -> list[str]:
    """
    Split reference lyrics into clickable chips as whitespace words.

    Newlines are treated as whitespace; chips are always word-by-word for consistent UX.
    """
    raw = (reference_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    return [t for t in raw.split() if t]


def _normalize_chip_match_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _is_spelled_single_letter_run(frag: list[str]) -> bool:
    """True if every non-empty fragment is a single letter (e.g. a/n/d spelling ``and``)."""
    cores = [strip_ultrastar_compare_tilde(p) for p in frag]
    cores = [c for c in cores if c]
    if len(cores) < 2:
        return False
    return all(len(c) == 1 for c in cores)


def _run_compact_match_key(frag: list[str]) -> str:
    return _normalize_chip_match_key(
        "".join(strip_ultrastar_compare_tilde(p) for p in frag)
    )


def align_reference_chips_to_run_indices(words: list[str], chips: list[str]) -> list[int | None]:
    """
    Map each reference chip to a melisma run index in *words* (same runs as ``syllable_runs_for_compare_view``).

    Prefers literal match on the **first note** of a run. Falls back to compact
    joined fragments for true multi-note words (e.g. hea+ven), but skips letter-spelling runs so
    ``And`` does not map to ``a``+``n``+``d``.
    """
    runs = syllable_runs_for_compare_view(words)
    n_runs = len(runs)
    out: list[int | None] = []
    cursor = 0
    for chip in chips:
        chip_stripped = (chip or "").strip()
        if not chip_stripped:
            out.append(None)
            continue
        first_tok = chip_stripped.split()[0]
        key = _normalize_chip_match_key(first_tok)
        if not key:
            out.append(None)
            continue
        found: int | None = None
        for r in range(cursor, n_runs):
            st = int(runs[r]["start"])
            if st >= len(words):
                continue
            w0 = strip_ultrastar_compare_tilde(words[st])
            if _normalize_chip_match_key(w0) == key:
                found = r
                break
        if found is None:
            for r in range(cursor, n_runs):
                st = int(runs[r]["start"])
                ln = int(runs[r]["length"])
                frag = words[st : st + ln]
                if _is_spelled_single_letter_run(frag) and len(key) >= 2:
                    continue
                if _run_compact_match_key(frag) == key:
                    found = r
                    break
        if found is None:
            out.append(None)
        else:
            out.append(found)
            cursor = found + 1
    return out


def _split_string_by_weights(s: str, weights: list[int]) -> list[str]:
    """Split *s* into len(weights) substrings with lengths proportional to *weights* (integer partition)."""
    k = len(weights)
    if k == 0:
        return []
    L = len(s)
    if k == 1:
        return [s]
    adj = [max(1, w) for w in weights]
    tw = sum(adj)
    exact = [L * w / tw for w in adj]
    sizes = [int(x) for x in exact]
    for _ in range(L - sum(sizes)):
        j = max(range(k), key=lambda i: exact[i] - sizes[i])
        sizes[j] += 1
    while sum(sizes) > L:
        j = max(range(k), key=lambda i: sizes[i])
        if sizes[j] <= 0:
            break
        sizes[j] -= 1
    out: list[str] = []
    pos = 0
    for sz in sizes:
        out.append(s[pos : pos + sz])
        pos += sz
    return out


def split_replacement_across_syllable_run(new_text: str, old_parts: list[str]) -> list[str]:
    """
    Map one reference/corrected word onto multiple note fragments, matching the old fragment lengths.

    Trailing ``~`` on the *last* old fragment is copied onto the last new fragment only.
    """
    k = len(old_parts)
    nt = (new_text or "").strip()
    if k <= 0:
        return []
    if k == 1:
        if note_word_has_trailing_tilde(old_parts[0]):
            suf = trailing_tilde_suffix(old_parts[0]) or "~"
            core = strip_ultrastar_compare_tilde(nt)
            return [core + suf]
        return [nt]
    last_suf = trailing_tilde_suffix(old_parts[-1]) if note_word_has_trailing_tilde(old_parts[-1]) else ""
    nt_core = strip_ultrastar_compare_tilde(nt)
    weights = [max(1, len(strip_ultrastar_compare_tilde(p))) for p in old_parts]
    chunks = _split_string_by_weights(nt_core, weights)
    if last_suf:
        chunks[-1] = chunks[-1] + last_suf
    return chunks


def strip_tilde_for_reference_display(text: str) -> str:
    """Replace trailing ``~`` / melisma markers per line with a space for the compare UI view."""
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(trailing_compare_tilde_as_space_for_view(line) for line in lines)


def note_words_as_lines(words: list[str]) -> str:
    """One UltraStar syllable per line (file order), including ``~`` as stored."""
    return "\n".join(words)


def correction_text_to_words(text: str) -> list[str]:
    """
    Parse correction box text: if it contains newlines, each line is one syllable (after strip);
    otherwise tokens are split on whitespace (legacy one-line paste).
    """
    raw = str(text).replace("\r\n", "\n").replace("\r", "\n")
    if "\n" in raw:
        parts = [ln.strip() for ln in raw.split("\n")]
        while parts and parts[-1] == "":
            parts.pop()
        return parts
    return raw.split()


@dataclass
class NoteLineRef:
    line_index: int  # 0-based in lines list
    prefix: str
    start: str
    duration: str
    pitch: str
    word: str


def pick_ultrastar_txt(song_dir: Path) -> Optional[Path]:
    """Prefer YARG-style ``notes.txt``, else first UltraStar txt (excluding sidecars)."""
    skip = {_TAG_SIDECAR.lower(), "readme.txt"}
    n = song_dir / "notes.txt"
    if n.is_file():
        return n
    cands = sorted(
        p
        for p in song_dir.glob("*.txt")
        if p.is_file() and p.name.lower() not in skip
    )
    if not cands:
        return None
    match = next((p for p in cands if p.stem == song_dir.name), None)
    return match or cands[0]


def parse_note_lines(lines: list[str]) -> list[NoteLineRef]:
    refs: list[NoteLineRef] = []
    for i, raw in enumerate(lines):
        line = raw.rstrip("\n\r")
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        if parts[0] not in _NOTE_TYPES:
            continue
        try:
            float(parts[1])
            float(parts[2])
            int(parts[3])
        except (ValueError, IndexError):
            continue
        word = " ".join(parts[4:]) if len(parts) > 4 else ""
        refs.append(NoteLineRef(i, parts[0], parts[1], parts[2], parts[3], word))
    return refs


def plain_lyrics_from_txt_path(txt_path: Path) -> tuple[str, list[str]]:
    """Return (one_syllable_per_line_text, word_list) from file — words match note lines, ``~`` preserved."""
    text = txt_path.read_text(encoding="utf-8", errors="replace")
    note_refs = parse_note_lines(text.splitlines())
    words = [r.word for r in note_refs]
    return note_words_as_lines(words), words


def apply_words_to_txt_file(txt_path: Path, new_words: list[str]) -> dict[str, int | str]:
    """
    Replace syllable/word tokens on note lines in order.
    If *new_words* is shorter, later notes keep old words; if longer, extra tokens are ignored.
    """
    raw = txt_path.read_text(encoding="utf-8", errors="replace")
    raw_lines = raw.splitlines()
    note_refs = parse_note_lines(raw_lines)
    if not note_refs:
        return {"ok": 0, "error": "No note lines found in file"}
    n_new = len(new_words)
    n_notes = len(note_refs)
    for i, ref in enumerate(note_refs):
        if i >= n_new:
            break
        old_line = raw_lines[ref.line_index]
        parts = old_line.split()
        if len(parts) < 4 or parts[0] not in _NOTE_TYPES:
            continue
        replacement = new_words[i]
        tail = [replacement] if replacement != "" else []
        raw_lines[ref.line_index] = " ".join(parts[:4] + tail)
    out = "\n".join(raw_lines) + "\n"
    txt_path.write_text(out, encoding="utf-8", newline="\n")
    warn = ""
    if n_new != n_notes:
        warn = f"Word count ({n_new}) differs from note lines ({n_notes}); only the first min(N,M) notes were updated."
    return {
        "ok": 1,
        "notes_total": n_notes,
        "words_supplied": n_new,
        "notes_updated": min(n_new, n_notes),
        "warning": warn,
    }
