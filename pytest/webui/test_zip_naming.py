"""Tests for ZIP download naming."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from webui.zip_naming import (
    per_job_zip_download_filename,
    primary_output_song_folder,
    remap_inner_arc_to_root,
)


class TestZipNaming(unittest.TestCase):
    def test_primary_output_song_folder(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Artist - Title").mkdir()
            self.assertEqual(primary_output_song_folder(root), "Artist - Title")

    def test_per_job_zip_filename_uses_folder(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "My Song").mkdir()
            job = {"job_id": "job_abc", "title": "ignored"}
            self.assertEqual(per_job_zip_download_filename(job, root), "My Song.zip")

    def test_remap_inner_on_collision(self) -> None:
        self.assertEqual(
            remap_inner_arc_to_root("Artist - Title/notes.txt", "Artist - Title", "Artist - Title [job_x]"),
            "Artist - Title [job_x]/notes.txt",
        )


if __name__ == "__main__":
    unittest.main()
