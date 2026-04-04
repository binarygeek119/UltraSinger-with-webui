"""Tests for export-folder skip logic."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from webui.export_skip_check import (
    expected_song_folder_from_job,
    export_folder_exists_for_job,
    iter_export_roots,
)


class TestExportSkipCheck(unittest.TestCase):
    def test_expected_folder_artist_title(self) -> None:
        self.assertEqual(
            expected_song_folder_from_job({"artist": "A", "title": "B"}),
            "A - B",
        )

    def test_expected_folder_title_only(self) -> None:
        self.assertEqual(
            expected_song_folder_from_job({"artist": "", "title": "Solo"}),
            "Solo",
        )

    def test_export_folder_exists_direct(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = MagicMock()
            cfg.yarg_export_path = str(root)
            cfg.ultrastar_export_path = ""
            (root / "Queen - Test").mkdir()
            ok, p = export_folder_exists_for_job(
                cfg,
                {"artist": "Queen", "title": "Test"},
            )
            self.assertTrue(ok)
            self.assertIn("Queen - Test", p.replace("\\", "/"))

    def test_export_folder_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = MagicMock()
            cfg.yarg_export_path = str(root)
            cfg.ultrastar_export_path = ""
            (root / "queen - test").mkdir()
            ok, _ = export_folder_exists_for_job(
                cfg,
                {"artist": "Queen", "title": "Test"},
            )
            self.assertTrue(ok)

    def test_iter_export_roots_skips_missing(self) -> None:
        cfg = MagicMock()
        cfg.yarg_export_path = "/nonexistent/path/that/does/not/exist/ever"
        cfg.ultrastar_export_path = ""
        self.assertEqual(iter_export_roots(cfg), [])


if __name__ == "__main__":
    unittest.main()
