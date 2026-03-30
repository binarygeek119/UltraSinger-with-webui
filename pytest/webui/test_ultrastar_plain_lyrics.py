"""Tests for UltraStar plain lyric extraction and word patch."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from webui.ultrastar_plain_lyrics import (
    align_reference_chips_to_run_indices,
    apply_words_to_txt_file,
    correction_text_to_words,
    merge_syllable_fragments_for_display,
    merged_ultrasinger_display_plain,
    note_words_as_lines,
    parse_note_lines,
    plain_lyrics_from_txt_path,
    reference_chips_for_compare,
    split_replacement_across_syllable_run,
    strip_tilde_for_reference_display,
    strip_ultrastar_compare_tilde,
    syllable_run_length_at,
    syllable_runs_for_compare_view,
    trailing_compare_tilde_as_space_for_view,
)


class TestPlainLyrics(unittest.TestCase):
    def test_strip_ultrastar_tilde_for_compare(self) -> None:
        self.assertEqual(strip_ultrastar_compare_tilde("hello~"), "hello")
        self.assertEqual(strip_ultrastar_compare_tilde("run~~"), "run")
        self.assertEqual(strip_ultrastar_compare_tilde("no"), "no")

    def test_trailing_tilde_as_space_for_view(self) -> None:
        self.assertEqual(trailing_compare_tilde_as_space_for_view("hello~"), "hello ")
        self.assertEqual(trailing_compare_tilde_as_space_for_view("run~~"), "run ")
        self.assertEqual(trailing_compare_tilde_as_space_for_view("no"), "no")
        self.assertEqual(trailing_compare_tilde_as_space_for_view("~"), "")

    def test_reference_chips_always_words(self) -> None:
        self.assertEqual(
            reference_chips_for_compare("Shady's\nback\nAnd I'm pissed"),
            ["Shady's", "back", "And", "I'm", "pissed"],
        )
        self.assertEqual(reference_chips_for_compare("a b c"), ["a", "b", "c"])

    def test_align_chips_skips_spelled_and_finds_literal_and(self) -> None:
        words = ["Shady's", "~", "back", "~", "a", "n", "d~", "~", "off", "And"]
        chips = ["Shady's", "back", "And"]
        idx = align_reference_chips_to_run_indices(words, chips)
        self.assertEqual(len(idx), 3)
        self.assertIsNotNone(idx[0])
        self.assertIsNotNone(idx[1])
        self.assertIsNotNone(idx[2])
        st_and = syllable_runs_for_compare_view(words)[idx[2]]["start"]
        self.assertEqual(words[st_and], "And")

    def test_syllable_run_length_with_tilde(self) -> None:
        self.assertEqual(syllable_run_length_at(["a~", "b"], 0), 1)
        self.assertEqual(syllable_run_length_at(["hea", "ven~"], 0), 2)
        self.assertEqual(syllable_run_length_at(["a", "b", "c"], 0), 1)

    def test_merge_syllable_fragments_for_display(self) -> None:
        self.assertEqual(merge_syllable_fragments_for_display(["hea", "ven~"]), "hea ven ")
        self.assertEqual(merge_syllable_fragments_for_display(["one~"]), "one ")
        self.assertEqual(merge_syllable_fragments_for_display(["And", "I'm", "pissed~"]), "And I'm pissed ")
        self.assertEqual(merge_syllable_fragments_for_display([]), "")

    def test_syllable_runs_for_compare_view(self) -> None:
        words = ["hea", "ven~", "ok"]
        runs = syllable_runs_for_compare_view(words)
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["start"], 0)
        self.assertEqual(runs[0]["length"], 2)
        self.assertEqual(runs[0]["display"], "hea ven ")
        self.assertEqual(runs[1]["start"], 2)
        self.assertEqual(merged_ultrasinger_display_plain(words), "hea ven \nok")

    def test_split_replacement_multi_fragment(self) -> None:
        parts = split_replacement_across_syllable_run("heaven", ["hea", "ven~"])
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[1].endswith("~"))
        self.assertEqual("".join(strip_ultrastar_compare_tilde(p) for p in parts), "heaven")

    def test_strip_reference_multiline(self) -> None:
        self.assertEqual(
            strip_tilde_for_reference_display("line~\nworld"),
            "line \nworld",
        )

    def test_note_words_as_lines_keeps_tilde(self) -> None:
        words = ["hel~lo", "world~"]
        self.assertEqual(note_words_as_lines(words), "hel~lo\nworld~")

    def test_plain_lyrics_one_line_per_syllable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "song.txt"
            p.write_text("#ARTIST: A\n: 1 2 3 hel~lo~\n: 4 5 6 world\n", encoding="utf-8")
            plain, w = plain_lyrics_from_txt_path(p)
            self.assertEqual(w[0], "hel~lo~")
            self.assertEqual(plain, "hel~lo~\nworld")

    def test_correction_text_to_words_multiline(self) -> None:
        self.assertEqual(correction_text_to_words("a\nb\n"), ["a", "b"])
        self.assertEqual(correction_text_to_words("a\nb"), ["a", "b"])
        self.assertEqual(correction_text_to_words("x y"), ["x", "y"])

    def test_parse_note_lines(self) -> None:
        lines = [
            "#ARTIST: A\n",
            ": 0 1 10 hello\n",
            "* 1 2 11 world\n",
        ]
        refs = parse_note_lines([x.rstrip("\n") for x in lines])
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0].word, "hello")
        self.assertEqual(refs[1].word, "world")

    def test_apply_words(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "song.txt"
            p.write_text(
                "#ARTIST: A\n#TITLE: T\n: 1 2 3 foo\n: 4 5 6 bar\n",
                encoding="utf-8",
            )
            plain, words = plain_lyrics_from_txt_path(p)
            self.assertEqual(words, ["foo", "bar"])
            self.assertEqual(plain, "foo\nbar")
            apply_words_to_txt_file(p, ["one", "two"])
            _, w2 = plain_lyrics_from_txt_path(p)
            self.assertEqual(w2, ["one", "two"])
            self.assertIn("one", p.read_text(encoding="utf-8"))

    def test_apply_words_from_multiline_plain(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "song.txt"
            p.write_text("#ARTIST: A\n: 1 2 3 old~\n: 4 5 6 x\n", encoding="utf-8")
            apply_words_to_txt_file(p, correction_text_to_words("new~\nwhy"))
            plain, w = plain_lyrics_from_txt_path(p)
            self.assertEqual(w, ["new~", "why"])
            self.assertIn("new~", p.read_text(encoding="utf-8"))
