"""Tests for ultrasinger-tag versioning and metadata file."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from webui.ultrasinger_tag import (
    bump_song_version,
    read_prior_song_version_from_song_dir,
    write_ultrasinger_tags_after_job,
)


class TestUltrasingerTag(unittest.TestCase):
    def test_bump_new(self) -> None:
        self.assertEqual(bump_song_version(None), "1.0.0")
        self.assertEqual(bump_song_version(""), "1.0.0")

    def test_bump_increments_patch(self) -> None:
        self.assertEqual(bump_song_version("1.0.0"), "1.0.1")
        self.assertEqual(bump_song_version("1.0.1"), "1.0.2")
        self.assertEqual(bump_song_version("2.3.9"), "2.3.10")

    def test_read_prior_from_song_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "ultrasinger-tag.txt").write_text(
                "song_version: 1.2.3\n",
                encoding="utf-8",
            )
            self.assertEqual(read_prior_song_version_from_song_dir(d), "1.2.3")

    def test_write_tag_after_job(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "out"
            song = out / "Artist - Title"
            song.mkdir(parents=True)
            (song / "Artist - Title.txt").write_text(
                "#ARTIST: Artist\n#TITLE: Title\n#MP3: Artist - Title.mp3\n",
                encoding="utf-8",
            )
            audio = song / "Artist - Title.mp3"
            audio.write_bytes(b"fakeaudio")
            job = {
                "title": "Title",
                "artist": "Artist",
                "source_type": "url",
                "source": "https://example.com/watch?v=1",
                "input_path": "https://example.com/watch?v=1",
                "tag_prior_song_version": "1.0.0",
                "tag_upload_file_hash": None,
            }
            repo = root / "repo"
            (repo / "src").mkdir(parents=True)
            (repo / "src" / "Settings.py").write_text(
                'class Settings:\n    APP_VERSION = "9.9.9"\n',
                encoding="utf-8",
            )
            write_ultrasinger_tags_after_job(job, out, repo, "0.5.0")
            tag = (song / "ultrasinger-tag.txt").read_text(encoding="utf-8")
            self.assertIn("song_version: 1.0.1", tag)
            self.assertIn("youtube_url: https://example.com/watch?v=1", tag)
            self.assertIn("ultrasinger_webui_version: 0.5.0", tag)
            self.assertIn("ultrasinger_version: 9.9.9", tag)
            self.assertIn("finished_song_audio_hash:", tag)
