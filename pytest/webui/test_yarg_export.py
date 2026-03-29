"""Tests for YARG folder export naming."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from webui.yarg_export import (
    build_yarg_zip_album_arc_overrides,
    iter_yarg_export_style_zip_entries,
    plan_yarg_flat_copies,
)


class TestPlanYargFlatCopies(unittest.TestCase):
    def test_maps_roles_with_instrumental_preferred_for_guitar(self) -> None:
        song = "Artist - Title"
        root = Path("/out")
        items = [
            (root / "u.txt", f"{song}/{song}.txt"),
            (root / "u.mp4", f"{song}/{song}.mp4"),
            (root / "u.jpg", f"{song}/{song}.jpg"),
            (root / "u.wav", f"{song}/{song} [Instrumental].wav"),
            (root / "uv.wav", f"{song}/{song} [Vocals].wav"),
        ]
        plan = plan_yarg_flat_copies(items, song)
        dests = {d for _s, d in plan}
        self.assertIn("notes.txt", dests)
        self.assertIn("background.mp4", dests)
        self.assertIn("album.jpg", dests)
        self.assertIn("guitar.wav", dests)
        by_dest = {d: s for s, d in plan}
        self.assertEqual(by_dest["guitar.wav"], root / "u.wav")

    def test_main_mix_when_no_instrumental(self) -> None:
        song = "A - B"
        root = Path("/x")
        items = [
            (root / "t.txt", f"{song}/{song}.txt"),
            (root / "a.wav", f"{song}/{song}.wav"),
        ]
        plan = plan_yarg_flat_copies(items, song)
        self.assertEqual({d for _s, d in plan}, {"notes.txt", "guitar.wav"})

    def test_zip_album_override_when_stems_excluded(self) -> None:
        song = "Artist - Title"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            song_dir = root / song
            song_dir.mkdir(parents=True)
            jpg = song_dir / f"{song}.jpg"
            jpg.write_bytes(b"x")
            txt = song_dir / f"{song}.txt"
            txt.write_bytes(b"#")
            ov = build_yarg_zip_album_arc_overrides(
                root,
                exclude_stem_tracks=True,
                exclude_midi=False,
            )
            self.assertEqual(ov[jpg.resolve()], f"{song}/album.jpg")

    def test_zip_no_album_override_when_stems_included(self) -> None:
        song = "S"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            song_dir = root / song
            song_dir.mkdir(parents=True)
            jpg = song_dir / f"{song}.jpg"
            jpg.write_bytes(b"x")
            ov = build_yarg_zip_album_arc_overrides(
                root,
                exclude_stem_tracks=False,
                exclude_midi=False,
            )
            self.assertEqual(ov, {})

    def test_iter_yarg_zip_entries_flat_names(self) -> None:
        song = "Artist - Title"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            song_dir = root / song
            song_dir.mkdir(parents=True)
            (song_dir / f"{song}.txt").write_bytes(b"#")
            (song_dir / f"{song}.jpg").write_bytes(b"x")
            (song_dir / f"{song}.mp4").write_bytes(b"v")
            (song_dir / f"{song}.wav").write_bytes(b"a")
            entries = iter_yarg_export_style_zip_entries(
                root,
                "job_x",
                exclude_stem_tracks=False,
            )
            arcs = {a for _p, a in entries}
            self.assertIn(f"{song}/notes.txt", arcs)
            self.assertIn(f"{song}/album.jpg", arcs)
            self.assertIn(f"{song}/background.mp4", arcs)
            self.assertIn(f"{song}/guitar.wav", arcs)

    def test_iter_yarg_zip_entries_respects_zip_arc_root(self) -> None:
        song = "S"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / song
            d.mkdir()
            (d / f"{song}.txt").write_bytes(b"#")
            entries = iter_yarg_export_style_zip_entries(
                root,
                "jid",
                exclude_stem_tracks=False,
                zip_arc_root="S [job_1]",
            )
            self.assertTrue(any(a.startswith("S [job_1]/") for _p, a in entries))


if __name__ == "__main__":
    unittest.main()
