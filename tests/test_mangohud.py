import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from voxcomm.mangohud import current_status, publish_status


class MangoHudStatusTests(unittest.TestCase):
    def test_published_status_preserves_line_break_and_sanitizes_whitespace(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("voxcomm.mangohud.status_path", return_value=Path(directory) / "status.json"):
                publish_status("Flight ready\n  Systems   online\tnow ")

                self.assertEqual(current_status(), "Flight ready\nSystems online now")

    def test_expired_status_is_hidden(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text(json.dumps({"text": "Old message", "expires": time.time() - 1}), encoding="utf-8")
            with patch("voxcomm.mangohud.status_path", return_value=path):
                self.assertEqual(current_status(), "")

    def test_missing_status_returns_empty_text(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("voxcomm.mangohud.status_path", return_value=Path(directory) / "missing.json"):
                self.assertEqual(current_status(), "")

    def test_invalid_or_unexpected_status_data_returns_empty_text(self):
        invalid_values = (
            "not json",
            "[]",
            json.dumps({"text": "No expiry"}),
            json.dumps({"text": "Bad expiry", "expires": "later"}),
            json.dumps({"text": 42, "expires": time.time() + 30}),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            with patch("voxcomm.mangohud.status_path", return_value=path):
                for value in invalid_values:
                    with self.subTest(value=value):
                        path.write_text(value, encoding="utf-8")
                        self.assertEqual(current_status(), "")

    def test_status_is_transliterated_for_mangohud_fonts(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("voxcomm.mangohud.status_path", return_value=Path(directory) / "status.json"):
                publish_status("Flugbereitschaft bestätigt · Systeme online")

                self.assertEqual(current_status(), "Flugbereitschaft bestaetigt | Systeme online")


if __name__ == "__main__":
    unittest.main()