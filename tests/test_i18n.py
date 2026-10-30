import unittest

from voxcomm.i18n import current_language, set_language, tr


class LocalizationTests(unittest.TestCase):
    def tearDown(self):
        set_language("en")

    def test_unknown_language_falls_back_to_english(self):
        set_language("fr")

        self.assertEqual(current_language(), "en")
        self.assertEqual(tr("Settings"), "Settings")

    def test_catalog_supports_formatted_messages(self):
        set_language("de")

        self.assertEqual(tr("Web search: {query}{ellipsis}", query="Wetter", ellipsis="…"), "Websuche: Wetter…")

    def test_english_catalog_falls_back_to_source_messages(self):
        set_language("en")

        self.assertEqual(tr("Thinking …"), "Thinking …")


if __name__ == "__main__":
    unittest.main()