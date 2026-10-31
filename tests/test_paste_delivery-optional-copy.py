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

from PySide6.QtCore import QMimeData, QPoint  # noqa: E402
from PySide6.QtGui import QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from cat_guardian import paste as paste_module  # noqa: E402
from cat_guardian import settings as settings_module  # noqa: E402
from cat_guardian.native import WindowInfo  # noqa: E402
from cat_guardian.paste import (  # noqa: E402
    ClipboardPasteWatcher, PasteDelivery, delivery_target, paste_target,
)
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

    def test_delivery_targets_other_apps_but_not_shell_surfaces(self):
        self.assertEqual(delivery_target(_info("CabinetWClass")), "explorer")
        self.assertEqual(delivery_target(_info("Progman")), "desktop")
        self.assertEqual(
            delivery_target(_info("Chrome_WidgetWin_1", "chrome.exe")), "app"
        )
        self.assertEqual(delivery_target(_info("Notepad", "notepad.exe")), "app")
        self.assertIsNone(delivery_target(None))
        self.assertIsNone(delivery_target(_info("Shell_TrayWnd")))
        self.assertIsNone(delivery_target(_info("Shell_SecondaryTrayWnd")))
        self.assertIsNone(
            delivery_target(WindowInfo(1, 2, "", "untitled", 0, 0, 10, 10, False, "Custom"))
        )


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


class ClipboardPickupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_watcher(self):
        watcher = ClipboardPasteWatcher()
        watcher._enabled = True
        return watcher

    def test_bulk_copy_announces_one_pickup_with_the_full_count(self):
        watcher = self.make_watcher()
        pickups = []
        watcher.pickup.connect(pickups.append)
        paths = [rf"C:\Temp\bulk\file{i}.txt" for i in range(6)]
        with mock.patch.object(paste_module, "_preferred_drop_effect", return_value=1):
            watcher._remember(paths, None, announce=True)
            # A copy can raise dataChanged more than once; the second is silent.
            watcher._remember(paths, None, announce=True)
        self.assertEqual(len(pickups), 1)
        self.assertEqual(pickups[0].count, 6)
        self.assertEqual(pickups[0].kind, "file")
        self.assertFalse(pickups[0].cut)

    def test_cut_copies_are_marked_for_the_bubble(self):
        watcher = self.make_watcher()
        pickups = []
        watcher.pickup.connect(pickups.append)
        payload = [r"C:\Temp\one.txt", r"C:\Temp\two.txt"]
        with mock.patch.object(paste_module, "_preferred_drop_effect", return_value=2):
            watcher._remember(payload, None, announce=True)
        self.assertEqual(len(pickups), 1)
        self.assertTrue(pickups[0].cut)
        self.assertEqual(pickups[0].count, 2)

    def test_copied_image_becomes_a_single_image_clip(self):
        watcher = self.make_watcher()
        mime = QMimeData()
        image = QImage(16, 16, QImage.Format.Format_RGB32)
        image.fill(0xFF3366CC)
        mime.setImageData(image)
        watcher._remember([], mime)
        clip = watcher._copied
        self.assertIsNotNone(clip)
        self.assertEqual(clip.kind, "image")
        self.assertEqual(clip.count, 1)
        self.assertFalse(clip.cut)
        self.assertIsNotNone(clip.icon)
        self.assertFalse(clip.icon.isNull())

    def test_text_and_emoji_clips_are_ignored(self):
        watcher = self.make_watcher()
        mime = QMimeData()
        mime.setText("hello 😺")
        watcher._remember([], mime)
        self.assertIsNone(watcher._copied)

    def test_blank_clipboard_clears_the_copy(self):
        watcher = self.make_watcher()
        with mock.patch.object(paste_module, "_preferred_drop_effect", return_value=1):
            watcher._remember([r"C:\Temp\one.txt"], None)
        self.assertIsNotNone(watcher._copied)
        watcher._remember([], QMimeData())
        self.assertIsNone(watcher._copied)

    def test_bulk_paste_emits_one_delivery_that_keeps_every_item(self):
        watcher = self.make_watcher()
        delivered = []
        watcher.delivery.connect(delivered.append)
        paths = [rf"C:\Temp\bulk\file{i}.txt" for i in range(6)]
        chrome = _info("Chrome_WidgetWin_1", "chrome.exe")
        with mock.patch.object(paste_module, "_preferred_drop_effect", return_value=1), \
                mock.patch.object(
                    paste_module.native, "foreground_window_info", return_value=chrome
                ), \
                mock.patch.object(paste_module, "QTimer") as timer:
            timer.singleShot.side_effect = lambda _ms, callback: callback()
            watcher._remember(paths, None)
            watcher._last_press = 0.0
            # One Ctrl+V is one animation, even when six files are pasted.
            watcher._on_paste_pressed()
            watcher._on_paste_pressed()
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0].count, 6)
        self.assertEqual(delivered[0].target, "app")
        self.assertEqual(delivered[0].kind, "file")

    def test_paste_into_a_shell_surface_is_not_delivered(self):
        watcher = self.make_watcher()
        delivered = []
        watcher.delivery.connect(delivered.append)
        with mock.patch.object(paste_module, "_preferred_drop_effect", return_value=1), \
                mock.patch.object(
                    paste_module.native, "foreground_window_info",
                    return_value=_info("Shell_TrayWnd"),
                ):
            watcher._remember([r"C:\Temp\one.txt"], None)
            watcher._deliver()
        self.assertEqual(delivered, [])


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

    def test_pickup_builds_a_grab_sequence(self):
        pet = self.make_pet()
        with mock.patch("cat_guardian.native.send_to_top"):
            accepted = pet.paste_pickup(
                icon=None, count=6, cut=False, point=QPoint(400, 300), kind="file",
            )
        self.assertTrue(accepted)
        self.assertTrue(pet._busy)
        self.assertTrue(pet._paste_picking)
        self.assertEqual(pet._mode, "overlay")
        self.assertEqual(
            [segment.mood for segment in pet._timeline],
            ["walk", "pickup", "carry_walk"],
        )
        self.assertEqual(pet._paste_count, 6)
        self.assertFalse(pet._paste_cut)
        pet.shutdown()

    def test_pickup_is_ignored_when_the_mode_is_off(self):
        pet = self.make_pet(paste_mode=False)
        accepted = pet.paste_pickup(point=QPoint(400, 300))
        self.assertFalse(accepted)
        self.assertFalse(pet._busy)
        self.assertEqual(pet._mode, "desktop")

    def test_pickup_never_interrupts_busy_greeting_hidden_or_overlay(self):
        pet = self.make_pet()
        point = QPoint(400, 300)
        pet._busy = True
        self.assertFalse(pet.paste_pickup(point=point))
        pet._busy = False
        pet._greeting = True
        self.assertFalse(pet.paste_pickup(point=point))
        pet._greeting = False
        pet.hide_cat()
        self.assertFalse(pet.paste_pickup(point=point))
        pet.show_cat()
        pet._enter_overlay()
        self.assertFalse(pet.paste_pickup(point=point))
        pet._enter_desktop()
        self.assertFalse(pet.paste_pickup(point=None))

    def test_pickup_finishes_back_on_the_desktop(self):
        pet = self.make_pet()
        with mock.patch("cat_guardian.native.send_to_top"):
            pet.paste_pickup(icon=None, count=2, cut=True, point=QPoint(500, 320))
        self.assertTrue(pet._paste_picking)
        self.assertTrue(pet._paste_cut)
        pet._return_to_desktop()
        self.assertFalse(pet._paste_picking)
        self.assertFalse(pet._paste_busy)
        self.assertFalse(pet._busy)
        self.assertEqual(pet._mode, "desktop")
        self.assertEqual(pet._paste_count, 1)
        self.assertFalse(pet._paste_cut)

    def test_image_kind_paints_with_fallback_art(self):
        pet = self.make_pet()
        pet._paste_icon = None
        pet._paste_kind = "image"
        pet._paste_busy = True
        pet._paste_count = 1
        pet._set_mood("carry_walk")
        image = QPixmap(pet.size())
        pet.render(image)
        self.assertFalse(image.isNull())


if __name__ == "__main__":
    unittest.main(verbosity=2)
