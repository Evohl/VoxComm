import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from voxcomm.theme import make_icon, read_kde_color
from PySide6.QtGui import QColor


class ThemeTests(unittest.TestCase):
    def test_reads_kde_accent_and_section_colors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kdeglobals"
            path.write_text("[General]\nAccentColor=41,197,254\n[Colors:Window]\nBackgroundNormal=32,35,38\n")

            self.assertEqual(read_kde_color("General", "AccentColor", path).name(), "#29c5fe")
            self.assertEqual(read_kde_color("Colors:Window", "BackgroundNormal", path).name(), "#202326")

    def test_missing_or_invalid_kde_color_returns_none(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kdeglobals"
            self.assertIsNone(read_kde_color("General", "AccentColor", path))
            path.write_text("[General]\nAccentColor=not,a,color\n")
            self.assertIsNone(read_kde_color("General", "AccentColor", path))

    def test_icons_are_drawn_in_requested_color(self):
        QApplication.instance() or QApplication([])
        for kind in ("mic", "play", "gear", "refresh"):
            image = make_icon(kind, QColor("#29c5fe")).pixmap(96, 96).toImage()
            opaque = [image.pixelColor(x, y) for x in range(96) for y in range(96) if image.pixelColor(x, y).alpha() > 250]
            self.assertTrue(opaque, kind)
            for color in opaque:
                self.assertLessEqual(max(abs(color.red() - 41), abs(color.green() - 197), abs(color.blue() - 254)), 2, kind)


if __name__ == "__main__":
    unittest.main()
