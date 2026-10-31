"""Offscreen checks for the one-shot launch greeting and its meow sound.

Run with:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from cat_guardian import pet as pet_module  # noqa: E402
from cat_guardian import sounds as sounds_module  # noqa: E402
from cat_guardian.native import WindowInfo  # noqa: E402
from cat_guardian.pet import CatGuardian  # noqa: E402
from cat_guardian.settings import DEFAULT_SETTINGS  # noqa: E402
from cat_guardian.sounds import MeowSound  # noqa: E402


class StartupGreetingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_pet(self):
        settings = dict(DEFAULT_SETTINGS)
        pet = CatGuardian(settings)
        pet._timer.stop()
        pet.show_cat()
        pet._fade_anim.stop()
        pet.setWindowOpacity(1.0)
        return pet

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()

    def test_greeting_enters_topmost_mouse_transparent_mode(self):
        pet = self.make_pet()
        pet.move(20, 40)
        with mock.patch("cat_guardian.native.send_to_top") as send_top:
            pet.startup_greeting()
            pet._apply_native()
            send_top.assert_called()
        self.assertTrue(pet._greeting)
        self.assertEqual(pet._mode, "greeting")
        self.assertEqual(pet.mood, "wake")
        self.assertTrue(pet.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertEqual(pet._greet_home, pet.pos())
        pet._cancel_greeting()

    def test_mode_switches_never_recreate_the_window(self):
        """setWindowFlag recreates the HWND, and the new window stops
        delivering mouse presses, which breaks drag and petting."""
        pet = self.make_pet()
        with mock.patch.object(CatGuardian, "setWindowFlag") as set_flag:
            pet.startup_greeting()
            pet._cancel_greeting()
            pet._enter_overlay()
            pet._return_to_desktop()
        set_flag.assert_not_called()
        self.assertEqual(pet._mode, "desktop")
        self.assertFalse(pet.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))

    def test_greeting_stages_wave_meow_and_return_home(self):
        pet = self.make_pet()
        pet.move(30, 50)
        home = pet.pos()
        meows = []
        pet.meow.connect(lambda: meows.append(time.monotonic()))

        pet.startup_greeting()
        pet._greet_move_in()
        self.assertEqual(pet._greet_anim.endValue(), pet._greet_target)
        pet._greet_anim.stop()
        pet._greet_wave()
        self.assertEqual(pet.mood, "greet")

        pet._greet_spin()
        self.assertEqual(pet.mood, "spin")
        pet._greet_speak()
        self.assertEqual(pet._bubble_text, "Meow!")
        self.assertEqual(len(meows), 1)
        pet._greet_return()
        self.assertEqual(pet.mood, "walk")
        pet._greet_anim.stop()
        pet._finish_greeting()

        self.assertFalse(pet._greeting)
        self.assertEqual(pet._mode, "desktop")
        self.assertEqual(pet.pos(), home)
        self.assertAlmostEqual(pet.windowOpacity(), 1.0)
        self.assertFalse(pet.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))

    def test_greeting_visual_waves_and_heart_spawns(self):
        pet = self.make_pet()
        pet._set_mood("greet")
        visual = pet._visual(time.monotonic())
        self.assertEqual(visual.eye_mode, "happy")
        self.assertLess(visual.paw_r.y(), -10.0)
        self.assertEqual(visual.tail, "sway")
        self.assertGreater(visual.blush, 0.5)

        pet._particles.clear()
        pet._next_heart = 0.0
        pet._update_particle_spawners(time.monotonic())
        self.assertTrue(any(p.kind == "heart" for p in pet._particles))

    def test_greeting_does_not_start_when_busy_or_hidden(self):
        pet = self.make_pet()
        pet._busy = True
        pet.startup_greeting()
        self.assertFalse(pet._greeting)
        pet._busy = False
        pet.hide_cat()
        pet.startup_greeting()
        self.assertFalse(pet._greeting)
        self.assertEqual(pet._mode, "desktop")

    def test_hide_and_distraction_cancel_greeting(self):
        pet = self.make_pet()
        pet.startup_greeting()
        pet.hide_cat()
        self.assertFalse(pet._greeting)
        self.assertEqual(pet._mode, "desktop")

        pet.show_cat()
        pet.startup_greeting()
        self.assertTrue(pet._greeting)
        pet.distraction(kind="site", label="example.com", info=WindowInfo(
            hwnd=0, pid=0, exe="", title="", left=0, top=0, right=400, bottom=300,
            shell=True,
        ))
        self.assertFalse(pet._greeting)
        pet.shutdown()

    def test_desktop_mode_clears_native_click_through(self):
        pet = self.make_pet()
        with mock.patch("cat_guardian.native.set_click_through") as set_through:
            pet._enter_greeting()
            pet._apply_native()
            self.assertTrue(set_through.call_args[0][1])
            pet._enter_desktop()
            pet._apply_native()
            self.assertFalse(set_through.call_args[0][1])

    def test_greeting_timing_budget_is_under_seven_seconds(self):
        total = (
            120
            + pet_module.GREET_ENTER_MS
            + pet_module.GREET_WAVE_MS
            + pet_module.GREET_SPIN_MS
            + pet_module.GREET_MEOW_MS
            + pet_module.GREET_RETURN_MS
        )
        self.assertLess(total, 7000)

    def test_meow_wav_is_short_mono_22k(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "meow.wav"
            MeowSound._write(path)
            with wave.open(str(path), "rb") as handle:
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getsampwidth(), 2)
                self.assertEqual(handle.getframerate(), 22050)
                duration = handle.getnframes() / handle.getframerate()
        self.assertAlmostEqual(duration, 0.45, places=2)

    def test_bundled_meow_asset_is_ready(self):
        path = sounds_module.ASSET_PATH
        self.assertTrue(path.exists(), "assets/meow.wav is missing")
        with wave.open(str(path), "rb") as handle:
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getsampwidth(), 2)
            self.assertEqual(handle.getframerate(), 22050)
            duration = handle.getnframes() / handle.getframerate()
        self.assertGreater(duration, 0.4)
        self.assertLess(duration, 1.5)

    @unittest.skipUnless(sys.platform == "win32", "winsound is Windows-only")
    def test_meow_uses_bundled_asset(self):
        sound = MeowSound()
        sound._ensure()
        self.assertEqual(sound._path, sounds_module.ASSET_PATH)

    @unittest.skipUnless(sys.platform == "win32", "winsound is Windows-only")
    def test_meow_plays_asynchronously(self):
        import winsound

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "meow.wav"
            MeowSound._write(path)
            sound = MeowSound()
            sound._path = path
            with mock.patch("winsound.PlaySound") as play:
                sound.play()
        play.assert_called_once()
        args = play.call_args[0]
        self.assertEqual(args[0], str(path))
        self.assertTrue(args[1] & winsound.SND_ASYNC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
