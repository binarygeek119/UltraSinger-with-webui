"""Unit tests for lyrics_remote helpers (no network)."""

from __future__ import annotations

import unittest

from webui.services import lyrics_remote as lr


class TestShazamJsonLd(unittest.TestCase):
    def test_lyrics_from_shazam_track_html(self) -> None:
        html = """<!doctype html><html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"MusicRecording","name":"X",
 "recordingOf":{"@type":"MusicComposition",
   "lyrics":{"@type":"CreativeWork","text":"Line one\\nLine two\\n"}}
}
</script></head><body></body></html>"""
        got = lr._lyrics_from_shazam_track_html(html)
        self.assertEqual(got, "Line one\nLine two\n")

    def test_lyrics_from_json_ld_graph(self) -> None:
        data = {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "Thing"},
                {
                    "@type": "MusicComposition",
                    "lyrics": {"@type": "CreativeWork", "text": "Hello world here"},
                },
            ],
        }
        self.assertEqual(lr._lyrics_from_json_ld_node(data), "Hello world here")


if __name__ == "__main__":
    unittest.main()
