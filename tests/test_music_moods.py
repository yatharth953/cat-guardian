"""Checks for the adaptive dance catalog and the newer dance poses.

Run with:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import random
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from cat_guardian.music import MusicReport  # noqa: E402
from cat_guardian.music_moods import (  # noqa: E402
    DANCE_NAMES, NEW_MUSIC_MOODS, DanceBrain, MusicProfile, apply_music_dance,
)
from cat_guardian.pet import MUSIC_MOODS, CatGuardian, Visual  # noqa: E402
from cat_guardian.settings import DEFAULT_SETTINGS  # noqa: E402


def _report(tempo: float = 112.0, energy: float = 0.5, beat: float = 0.5,
            state: str = "music") -> MusicReport:
    return MusicReport(
        state=state, confidence=0.8, evidence=0.72, music=0.7, speech=0.3,
        tempo=tempo, beat_strength=beat, intensity=energy, detail="test",
    )


class DanceCatalogTests(unittest.TestCase):
    def test_catalog_holds_old_and_new_moods(self):
        self.assertEqual(len(DANCE_NAMES), len(set(DANCE_NAMES)))
        self.assertGreaterEqual(len(DANCE_NAMES), 14)
        self.assertTrue(NEW_MUSIC_MOODS <= set(DANCE_NAMES))
        self.assertTrue(NEW_MUSIC_MOODS <= MUSIC_MOODS)

    def test_slow_quiet_track_opens_with_chill(self):
        brain = DanceBrain(random.Random(3))
        mood, wait = brain.choose(MusicProfile(80.0, 0.15, 0.8, 0.8), now=10.0)
        self.assertEqual(mood, "music_chill")
        self.assertGreaterEqual(wait, 9.0)

    def test_drive_change_switches_immediately(self):
        brain = DanceBrain(random.Random(3))
        brain.choose(MusicProfile(80.0, 0.15, 0.8, 0.8), now=10.0)
        mood, _ = brain.choose(MusicProfile(140.0, 0.95, 0.8, 0.8), now=10.2)
        self.assertEqual(mood, "music_hype")

    def test_long_track_keeps_rotating(self):
        brain = DanceBrain(random.Random(11))
        profile = MusicProfile(112.0, 0.5, 0.5, 0.85)
        now = 0.0
        current = None
        chosen = []
        for _ in range(40):
            current, wait = brain.choose(profile, now, current)
            chosen.append(current)
            now += wait
        self.assertGreaterEqual(len(set(chosen)), 6)
        run = longest = 1
        for previous, name in zip(chosen, chosen[1:]):
            run = run + 1 if name == previous else 1
            longest = max(longest, run)
        self.assertLessEqual(longest, 3)

    def test_every_new_mood_has_a_distinct_pose(self):
        signatures = set()
        for mood in sorted(NEW_MUSIC_MOODS):
            visual = Visual()
            apply_music_dance(
                visual, mood, now=2.0, phase=0.25, pulse=0.4, scale=0.7,
                beat_count=5,
            )
            signatures.add((
                round(visual.head_tilt, 1), round(visual.lean, 1),
                round(visual.bob, 1), visual.tail, visual.eye_mode,
                round(visual.paw_l.y(), 1), round(visual.paw_r.y(), 1),
            ))
        self.assertEqual(len(signatures), len(NEW_MUSIC_MOODS))


class DancePetTests(unittest.TestCase):
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

    def test_auto_engine_uses_the_whole_catalog(self):
        pet = self.make_pet()
        pet.music_report(_report())
        pet._auto_eval_at = 0.0
        pet._update_music(time.monotonic(), 0.016)
        self.assertIn(pet._auto_mood, DANCE_NAMES)

    def test_new_mood_dances_and_paints(self):
        pet = self.make_pet()
        pet.music_report(_report())
        pet._set_mood("music_twirl")
        visual = pet._visual(time.monotonic())
        self.assertEqual(visual.tail, "stream")
        image = QPixmap(pet.size())
        pet.render(image)
        self.assertFalse(image.isNull())
        self.assertTrue(pet.music_dancing)

    def test_manual_style_still_maps_to_its_own_mood(self):
        pet = self.make_pet(music_style="hype")
        pet.music_report(_report())
        self.assertEqual(pet._music_mood_name(), "music_hype")
        pet._music_style = "minimal"
        self.assertEqual(pet._music_mood_name(), "music_minimal")

    def test_catalog_can_be_turned_off_for_the_legacy_auto(self):
        pet = self.make_pet(music_dance_catalog=False)
        pet.music_report(_report())
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

    def test_turning_the_catalog_back_on_applies_immediately(self):
        pet = self.make_pet(music_dance_catalog=False)
        pet._auto_eval_at = time.monotonic() + 30.0
        pet.apply_settings({**pet.settings, "music_dance_catalog": True})
        self.assertTrue(pet._music_catalog)
        self.assertEqual(pet._auto_eval_at, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
