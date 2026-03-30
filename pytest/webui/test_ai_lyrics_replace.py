"""Tests for AI lyric replace length normalization."""

from __future__ import annotations

import unittest

from webui.services.ai_lyrics_replace import _normalize_ai_output_to_slot_count


class TestAiLyricsReplace(unittest.TestCase):
    def test_normalize_same_length(self) -> None:
        a = ["x", "y"]
        b, w = _normalize_ai_output_to_slot_count(["1", "2"], a)
        self.assertEqual(b, ["1", "2"])
        self.assertIsNone(w)

    def test_normalize_pad_from_original(self) -> None:
        orig = ["a", "b", "c", "d"]
        b, w = _normalize_ai_output_to_slot_count(["x", "y"], orig)
        self.assertEqual(b, ["x", "y", "c", "d"])
        self.assertIsNotNone(w)

    def test_normalize_truncate(self) -> None:
        orig = ["a", "b"]
        b, w = _normalize_ai_output_to_slot_count(["1", "2", "3", "4"], orig)
        self.assertEqual(b, ["1", "2"])
        self.assertIsNotNone(w)


if __name__ == "__main__":
    unittest.main()
