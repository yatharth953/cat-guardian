"""Checks for the music controls in the settings dialog."""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from cat_guardian.__main__ import SettingsDialog  # noqa: E402
from cat_guardian.settings import DEFAULT_SETTINGS  # noqa: E402


class MusicSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_dialog(self, **overrides):
        settings = dict(DEFAULT_SETTINGS)
        settings.update(overrides)
        return SettingsDialog(settings, "Listening")

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()

    def test_music_controls_reflect_settings(self):
        dialog = self.make_dialog(music_style="groovy", music_intensity=70,
                                  music_sensitivity=40, music_delay=9.0)
        self.assertEqual(dialog.music_style.currentData(), "groovy")
        self.assertEqual(dialog.music_intensity.value(), 70)
        self.assertEqual(dialog.music_sensitivity.value(), 40)
        self.assertEqual(dialog.music_delay.value(), 9)
        self.assertIn("Listening", dialog.music_status.text())

    def test_preview_emits_selected_style_without_saving(self):
        dialog = self.make_dialog(music_style="hype", music_intensity=80)
        seen = []
        dialog.preview_requested.connect(lambda style, value: seen.append((style, value)))
        dialog._preview_music()
        self.assertEqual(seen, [("hype", 80)])

    def test_off_style_disables_preview(self):
        dialog = self.make_dialog(music_style="off")
        self.assertFalse(dialog.preview_button.isEnabled())
        dialog.music_style.setCurrentIndex(dialog.music_style.findData("chill"))
        self.assertTrue(dialog.preview_button.isEnabled())

    def test_dialog_resizes_from_all_edges(self):
        dialog = self.make_dialog()
        self.assertTrue(dialog.isSizeGripEnabled())
        self.assertIsNotNone(dialog.scroll)
        self.assertLessEqual(dialog.minimumHeight(), 320)
        dialog.resize(520, 380)
        self.assertLessEqual(dialog.height(), 420)
        self.assertGreater(dialog.app_list.maximumHeight(), dialog.app_list.minimumHeight())

    def test_disabling_detection_disables_tuning_controls(self):
        dialog = self.make_dialog()
        dialog.music_enabled.setChecked(False)
        self.assertFalse(dialog.music_intensity.isEnabled())
        self.assertFalse(dialog.music_delay.isEnabled())
        self.assertTrue(dialog.music_style.isEnabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
