import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QGuiApplication
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from sc_ai_copilot.app import apply_app_identity
from sc_ai_copilot.branding import APP_ID, APP_NAME, ICON_SIZES, ICON_SVG, install_launcher, make_app_icon


class BrandingTests(unittest.TestCase):
    def test_product_identity_is_voxcomm(self):
        self.assertEqual(APP_NAME, "VoxComm")
        self.assertEqual(APP_ID, "voxcomm")

    def test_icon_svg_is_valid_and_renders_visible_pixels(self):
        app = QApplication.instance() or QApplication([])
        ElementTree.fromstring(ICON_SVG)
        self.assertTrue(QSvgRenderer(QByteArray(ICON_SVG.encode())).isValid())

        icon = make_app_icon()
        self.assertFalse(icon.isNull())
        self.assertTrue(set(ICON_SIZES) <= {size.width() for size in icon.availableSizes()})
        image = icon.pixmap(128, 128).toImage()
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        self.assertGreater(image.pixelColor(64, 64).alpha(), 200)

    def test_install_launcher_writes_desktop_entry_and_icon(self):
        with tempfile.TemporaryDirectory() as directory:
            desktop_path, icon_path = install_launcher(Path(directory), command="/opt/my apps/.venv/bin/voxcomm")

            self.assertEqual(desktop_path, Path(directory) / "applications" / f"{APP_ID}.desktop")
            self.assertEqual(icon_path, Path(directory) / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg")
            self.assertEqual(icon_path.read_text(encoding="utf-8"), ICON_SVG)
            entry = desktop_path.read_text(encoding="utf-8")
            self.assertIn(f"Name={APP_NAME}\n", entry)
            self.assertIn(f"Icon={APP_ID}\n", entry)
            self.assertIn(f"StartupWMClass={APP_ID}\n", entry)
            self.assertIn("Categories=AudioVideo;Audio;\n", entry)
            self.assertIn('Exec="/opt/my apps/.venv/bin/voxcomm"\n', entry)

    def test_install_launcher_quotes_python_module_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            desktop_path, _ = install_launcher(Path(directory), command="/my env/bin/python -m sc_ai_copilot.app")

            self.assertIn('Exec="/my env/bin/python" -m sc_ai_copilot.app\n', desktop_path.read_text(encoding="utf-8"))

    def test_app_identity_sets_name_desktop_file_and_icon(self):
        app = QApplication.instance() or QApplication([])

        apply_app_identity(app)

        self.assertEqual(app.applicationName(), APP_NAME)
        self.assertEqual(QGuiApplication.desktopFileName(), APP_ID)
        self.assertFalse(app.windowIcon().isNull())


if __name__ == "__main__":
    unittest.main()
