"""Offscreen checks for the optional copy-paste delivery feature.

Run with:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from cat_guardian import settings as settings_module  # noqa: E402
from cat_guardian.native import WindowInfo  # noqa: E402
from cat_guardian.paste import ClipboardPasteWatcher, PasteDelivery, paste_target  # noqa: E402
from cat_guardian.pet import CatGuardian  # noqa: E402
from cat_guardian.settings import DEFAULT_SETTINGS  # noqa: E402


def _info(cls: str, exe: str = "explorer.exe") -> WindowInfo:
    return WindowInfo(
        0, 0, exe, "", 0, 0, 100, 100, cls in {"Progman", "WorkerW"}, cls,
    )


class PasteTargetTests(unittest.TestCase):
    def test_explorer_and_desktop_accept_a_paste(self):
        self.assertEqual(paste_target(_info("CabinetWClass")), "explorer")
        self.assertEqual(paste_target(_info("ExploreWClass")), "explorer")
        self.assertEqual(paste_target(_info("Progman")), "desktop")
        self.assertEqual(paste_target(_info("WorkerW")), "desktop")

    def test_other_windows_and_missing_info_are_ignored(self):
        self.assertIsNone(paste_target(None))
        self.assertIsNone(paste_target(_info("Chrome_WidgetWin_1", "chrome.exe")))
        self.assertIsNone(paste_target(_info("Shell_TrayWnd")))

    def test_window_class_defaults_to_empty_for_older_constructors(self):
        info = WindowInfo(1, 2, "a.exe", "t", 0, 0, 10, 10, False)
        self.assertEqual(info.cls, "")
        self.assertIsNone(paste_target(info))


class PasteSettingsTests(unittest.TestCase):
    def test_paste_mode_is_off_by_default(self):
        self.assertFalse(DEFAULT_SETTINGS["paste_mode"])

    def test_paste_mode_round_trips_and_coerces(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text('{"paste_mode": true, "pet_name": "Miso"}', encoding="utf-8")
            with mock.patch.object(settings_module, "SETTINGS_PATH", path):
                loaded = settings_module.load_settings()
            self.assertTrue(loaded["paste_mode"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            path.write_text('{"paste_mode": 0}', encoding="utf-8")
            with mock.patch.object(settings_module, "SETTINGS_PATH", path):
                loaded = settings_module.load_settings()
            self.assertFalse(loaded["paste_mode"])


class ClipboardClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_local_file_urls_only_are_kept(self):
        class Url:
            def __init__(self, local, path=""):
                self._local = local
                self._path = path

            def isLocalFile(self):
                return self._local

            def toLocalFile(self):
                return self._path

        class Mime:
            def __init__(self, urls):
                self._urls = urls

            def hasUrls(self):
                return bool(self._urls)

            def urls(self):
                return self._urls

        self.assertEqual(
            ClipboardPasteWatcher._local_paths(Mime([
                Url(True, r"C:\Temp\a.txt"),
                Url(False, "https://example.com"),
                Url(True, r"C:\Temp\a.txt"),
            ])),
            [r"C:\Temp\a.txt"],
        )
        self.assertEqual(ClipboardPasteWatcher._local_paths(None), [])
        self.assertEqual(ClipboardPasteWatcher._local_paths(Mime([])), [])


class PasteDeliveryAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_pet(self, paste_mode: bool = True):
        settings = dict(DEFAULT_SETTINGS)
        settings["paste_mode"] = paste_mode
        pet = CatGuardian(settings)
        pet._timer.stop()
        pet.show_cat()
        pet._fade_anim.stop()
        pet.setWindowOpacity(1.0)
        return pet

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()

    def test_delivery_is_ignored_when_the_mode_is_off(self):
        pet = self.make_pet(paste_mode=False)
        accepted = pet.paste_delivery(
            icon=None, count=1, cut=False, point=QPoint(200, 200), kind="file",
        )
        self.assertFalse(accepted)
        self.assertFalse(pet._busy)
        self.assertEqual(pet._mode, "desktop")

    def test_delivery_builds_its_own_sequence(self):
        pet = self.make_pet()
        with mock.patch("cat_guardian.native.send_to_top"):
            accepted = pet.paste_delivery(
                icon=None, count=2, cut=True, point=QPoint(300, 240), kind="folder",
            )
        self.assertTrue(accepted)
        self.assertTrue(pet._busy)
        self.assertEqual(pet._mode, "overlay")
        self.assertEqual(
            [segment.mood for segment in pet._timeline],
            ["pickup", "carry_walk", "drop", "walk"],
        )
        self.assertEqual(pet._paste_count, 2)
        self.assertTrue(pet._paste_cut)
        self.assertEqual(pet._paste_kind, "folder")
        pet.shutdown()

    def test_delivery_never_interrupts_busy_greeting_hidden_or_overlay(self):
        pet = self.make_pet()
        point = QPoint(300, 240)
        pet._busy = True
        self.assertFalse(pet.paste_delivery(point=point))
        pet._busy = False
        pet._greeting = True
        self.assertFalse(pet.paste_delivery(point=point))
        pet._greeting = False
        pet.hide_cat()
        self.assertFalse(pet.paste_delivery(point=point))
        pet.show_cat()
        pet._enter_overlay()
        self.assertFalse(pet.paste_delivery(point=point))
        pet._enter_desktop()

    def test_carry_visual_rises_then_lands(self):
        pet = self.make_pet()
        now = time.monotonic()
        pet._paste_icon = None
        pet._paste_kind = "file"
        pet._paste_busy = True
        pet._set_mood("pickup")
        pet._mood_since = now - 0.225
        rising = pet._visual(now)
        self.assertGreater(rising.carry, 0.1)
        self.assertLessEqual(rising.carry, 1.0)
        pet._set_mood("drop")
        pet._mood_since = now - 0.30
        falling = pet._visual(now)
        self.assertGreater(falling.carry_y, 0.0)
        pet._mood_since = now - 0.42
        landed = pet._visual(now)
        self.assertAlmostEqual(landed.carry, 0.0, places=2)
        self.assertGreater(landed.carry_y, 40.0)

    def test_carried_icon_paints_without_errors(self):
        pet = self.make_pet()
        pet._paste_icon = QPixmap(32, 32)
        pet._paste_icon.fill()
        pet._paste_busy = True
        pet._paste_count = 3
        pet._set_mood("carry_walk")
        image = QPixmap(pet.size())
        pet.render(image)
        self.assertFalse(image.isNull())
        pet._paste_icon = None
        pet._paste_kind = "folder"
        pet.render(image)

    def test_delivery_carries_no_file_paths(self):
        fields = set(PasteDelivery.__dataclass_fields__)
        self.assertFalse({"path", "paths", "name", "title"} & fields)


if __name__ == "__main__":
    unittest.main(verbosity=2)
