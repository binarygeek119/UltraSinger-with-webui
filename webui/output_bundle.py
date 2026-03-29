"""Rules for which job output files go into ZIP downloads and YARG folder exports."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator


def path_is_stem_track(path: Path) -> bool:
    n = path.name
    return "[Instrumental]" in n or "[Vocals]" in n


def iter_job_output_files(
    output_root: Path,
    *,
    exclude_stem_tracks: bool,
    exclude_midi: bool,
) -> Iterator[tuple[Path, str]]:
    """Yield (absolute path, path relative to output_root) for each included file."""
    if not output_root.is_dir():
        return
    for path in output_root.rglob("*"):
        if not path.is_file():
            continue
        if exclude_stem_tracks and path_is_stem_track(path):
            continue
        if exclude_midi and path.suffix.lower() == ".mid":
            continue
        yield path, str(path.relative_to(output_root))
