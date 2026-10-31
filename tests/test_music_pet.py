"""Qt-side checks for music presentation and interaction priorities.

Run with:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from cat_guardian.music import BeatEvent, MusicReport  # noqa: E402
from cat_guardian.pet import CatGuardian  # noqa: E402
from cat_guardian.settings import DEFAULT_SETTINGS  # noqa: E402


def music_report(state="music", evidence=0.72, beat=0.8):
    return MusicReport(
        state=state, confidence=0.8, evidence=evidence, music=0.7, speech=0.3,
        tempo=120.0, beat_strength=beat, intensity=0.8, detail="test",
    )


class PetMusicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_pet(self, **overrides):
        settings = dict(DEFAULT_SETTINGS)
        settings.update(overrides)
        pet = CatGuardian(settings)
        pet._timer.stop()
        return pet

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()

    def test_report_activates_music_and_wakes_sleeping_cat(self):
        pet = self.make_pet()
        pet._set_mood("sleep")
        pet.music_report(music_report())
        self.assertTrue(pet._music_active)
        self.assertEqual(pet._mood, "wake")

    def test_speech_report_returns_to_idle(self):
        pet = self.make_pet()
        pet.music_report(music_report())
        pet._set_mood("music_groove")
        pet.music_report(music_report(state="speech"))
        self.assertFalse(pet._music_active)
        self.assertEqual(pet._mood, "idle")

    def test_disabled_detection_never_activates(self):
        pet = self.make_pet(music_enabled=False)
        pet.music_report(music_report())
        self.assertFalse(pet._music_active)

    def test_style_off_never_activates(self):
        pet = self.make_pet(music_style="off")
        pet.music_report(music_report())
        self.assertFalse(pet._music_active)

    def test_preview_dances_without_music_and_expires(self):
        pet = self.make_pet()
        pet.preview_music("chill", 60, seconds=5)
        self.assertTrue(pet._music_active)
        self.assertEqual(pet._music_effective_style(), "chill")
        pet._demo_until = time.monotonic() - 0.01
        pet._update_music(time.monotonic(), 0.016)
        self.assertFalse(pet._music_active)

    def test_interactions_take_priority_over_music(self):
        pet = self.make_pet(music_style="groovy")
        pet.music_report(music_report())
        self.assertTrue(pet._music_ready())
        pet._busy = True
        self.assertFalse(pet._music_ready())
        pet._busy = False
        pet._dragging = True
        self.assertFalse(pet._music_ready())
        pet._dragging = False
        pet._mode = "overlay"
        self.assertFalse(pet._music_ready())

    def test_stale_report_stops_dancing(self):
        pet = self.make_pet()
        pet.music_report(music_report())
        pet._set_mood("music_groove")
        pet._music_report_at = time.monotonic() - 5.0
        pet._update_music(time.monotonic(), 0.016)
        self.assertFalse(pet._music_active)
        self.assertEqual(pet._mood, "idle")

    def test_tick_enters_music_mood_and_blocks_sleep(self):
        pet = self.make_pet(music_style="groovy")
        pet._set_mood("idle")
        pet._last_interaction = time.monotonic() - 60.0
        pet.music_report(music_report())
        pet._tick()
        self.assertEqual(pet._mood, "music_groove")
        pet._set_mood("idle")
        pet._last_interaction = time.monotonic() - 60.0
        pet._tick()
        self.assertNotEqual(pet._mood, "sleep")

    def test_beat_sets_phase_and_pulse(self):
        pet = self.make_pet()
        pet.music_report(music_report())
        pet.music_beat(BeatEvent(0.8, 120.0, 0.8))
        self.assertEqual(pet._beat_count, 1)
        self.assertGreaterEqual(pet._beat_pulse, 0.7)

    def test_auto_style_selects_chill_and_hype(self):
        pet = self.make_pet(music_style="auto")
        pet.music_report(music_report())
        now = time.monotonic()
        pet._music_energy = 0.15
        pet._music_tempo = 80.0
        pet._auto_eval_at = now - 1.0
        pet._update_music(now, 0.016)
        self.assertEqual(pet._auto_mood, "music_chill")
        pet._music_energy = 0.95
        pet._music_tempo = 140.0
        pet._auto_eval_at = now - 1.0
        pet._update_music(now, 0.016)
        self.assertEqual(pet._auto_mood, "music_hype")


if __name__ == "__main__":
    unittest.main(verbosity=2)
