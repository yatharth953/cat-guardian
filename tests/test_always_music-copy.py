"""Checks for the user-selected always-music apps and websites.

The classifier keeps its own tests; these only prove that a pre-selected source
can override the decision without changing how everything else behaves.
Run with:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication  # noqa: E402

from cat_guardian import settings as settings_module  # noqa: E402
from cat_guardian.__main__ import SettingsDialog  # noqa: E402
from cat_guardian.always_music import (  # noqa: E402
    AlwaysMusicWatcher, matching_app, matching_site,
)
from cat_guardian.music import MusicReport  # noqa: E402
from cat_guardian.pet import CatGuardian  # noqa: E402
from cat_guardian.settings import DEFAULT_SETTINGS  # noqa: E402


def report(state: str) -> MusicReport:
    return MusicReport(
        state=state, confidence=0.8, evidence=0.7, music=0.6, speech=0.4,
        tempo=120.0, beat_strength=0.5, intensity=0.6, detail="test",
    )


class MatchingTests(unittest.TestCase):
    def test_app_match_requires_an_allowlisted_audible_process(self):
        self.assertEqual(
            matching_app(["vlc.exe", "spotify.exe"], {"spotify.exe": 0.4}),
            "spotify.exe",
        )
        self.assertEqual(matching_app(["spotify.exe"], {"chrome.exe": 0.4}), "")
        self.assertEqual(matching_app([], {"spotify.exe": 0.4}), "")

    def test_site_match_covers_subdomains_only(self):
        allowed = ["youtube.com"]
        self.assertEqual(matching_site("music.youtube.com", allowed), "music.youtube.com")
        self.assertEqual(matching_site("youtube.com", allowed), "youtube.com")
        self.assertEqual(matching_site("notyoutube.com", allowed), "")
        self.assertEqual(matching_site("", allowed), "")


class WatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make(self, settings, playing):
        self.playing = dict(playing)
        watcher = AlwaysMusicWatcher(
            lambda: dict(settings),
            probe=lambda: dict(self.playing),
            autostart=False,
        )
        seen: list[tuple[bool, str]] = []
        watcher.changed.connect(lambda active, label: seen.append((active, label)))
        return watcher, seen

    def test_app_source_emits_once_then_clears(self):
        watcher, seen = self.make(
            {"music_always_apps": ["spotify.exe"], "music_always_domains": []},
            {"spotify.exe": 0.3},
        )
        watcher.poll()
        watcher.poll()
        self.assertEqual(seen, [(True, "spotify.exe")])
        self.playing = {}
        watcher.poll()
        self.assertEqual(seen[-1], (False, ""))
        watcher.refresh(force=True)
        self.assertEqual(seen.count((False, "")), 2)

    def test_site_source_needs_audible_browser_audio(self):
        watcher, seen = self.make(
            {"music_always_apps": [], "music_always_domains": ["music.youtube.com"]},
            {"chrome.exe": 0.4},
        )
        watcher.set_web_source("music.youtube.com", True)
        self.assertEqual(seen[-1], (True, "music.youtube.com"))
        watcher.set_web_source("lectures.example", True)
        self.assertEqual(seen[-1], (False, ""))
        watcher.set_web_source("music.youtube.com", False)
        self.assertEqual(seen[-1], (False, ""))

    def test_site_without_browser_audio_stays_idle(self):
        watcher, seen = self.make(
            {"music_always_apps": [], "music_always_domains": ["music.youtube.com"]},
            {},
        )
        watcher.set_web_source("music.youtube.com", True)
        self.assertEqual(seen, [])

    def test_disabled_reactions_never_activate(self):
        watcher, seen = self.make(
            {"music_enabled": False, "music_always_apps": ["spotify.exe"]},
            {"spotify.exe": 0.3},
        )
        watcher.poll()
        self.assertEqual(seen, [])

    def test_style_off_never_activates(self):
        watcher, seen = self.make(
            {"music_style": "off", "music_always_apps": ["spotify.exe"]},
            {"spotify.exe": 0.3},
        )
        watcher.poll()
        self.assertEqual(seen, [])


class PetAlwaysMusicTests(unittest.TestCase):
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

    def test_forced_source_dances_even_on_speech(self):
        pet = self.make_pet(music_always_apps=["spotify.exe"])
        pet.music_report(report("speech"))
        self.assertFalse(pet._music_active)
        pet.set_always_music(True, "spotify.exe")
        self.assertTrue(pet._music_active)
        pet.music_report(report("speech"))
        self.assertTrue(pet._music_active)

    def test_clearing_the_source_returns_to_sleep(self):
        pet = self.make_pet()
        pet.set_always_music(True, "spotify.exe")
        pet.music_report(report("speech"))
        pet.set_always_music(False)
        self.assertFalse(pet._music_active)

    def test_disabled_or_off_never_forces(self):
        for overrides in ({"music_enabled": False}, {"music_style": "off"}):
            pet = self.make_pet(**overrides)
            pet.set_always_music(True, "spotify.exe")
            self.assertFalse(pet._music_active)

    def test_forced_source_still_respects_staleness(self):
        pet = self.make_pet()
        pet.set_always_music(True, "spotify.exe")
        pet._music_report_at = time.monotonic() - 5.0
        pet._update_music(time.monotonic(), 0.016)
        self.assertFalse(pet._music_active)

    def test_reenabling_waits_for_the_watcher_to_reconfirm(self):
        pet = self.make_pet()
        pet.set_always_music(True, "spotify.exe")
        pet.apply_settings({**DEFAULT_SETTINGS, "music_enabled": False})
        self.assertFalse(pet._always_music)
        pet.apply_settings(dict(DEFAULT_SETTINGS))
        pet.music_report(report("speech"))
        self.assertFalse(pet._music_active)
        pet.set_always_music(True, "spotify.exe")
        pet.music_report(report("speech"))
        self.assertTrue(pet._music_active)


class SettingsTests(unittest.TestCase):
    def test_defaults_include_empty_allowlists(self):
        self.assertEqual(DEFAULT_SETTINGS["music_always_apps"], [])
        self.assertEqual(DEFAULT_SETTINGS["music_always_domains"], [])

    def test_load_normalizes_allowlists(self):
        payload = {
            "music_always_apps": [" Spotify.exe ", "spotify.exe", "C:/Games/Bad Name.exe"],
            "music_always_domains": ["https://Music.YouTube.com/feed", "music.youtube.com"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(settings_module, "SETTINGS_PATH", path):
                loaded = settings_module.load_settings()
        self.assertEqual(loaded["music_always_apps"], ["spotify.exe"])
        self.assertEqual(loaded["music_always_domains"], ["music.youtube.com"])


class DialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()

    def test_dialog_prefills_and_saves_allowlists(self):
        settings = dict(DEFAULT_SETTINGS)
        settings["music_always_apps"] = ["vlc.exe"]
        settings["music_always_domains"] = ["music.youtube.com"]
        dialog = SettingsDialog(settings, "Listening")
        self.assertEqual(dialog.always_apps.toPlainText(), "vlc.exe")
        self.assertEqual(dialog.always_domains.toPlainText(), "music.youtube.com")

        dialog.always_apps.setPlainText("Spotify.exe\nC:/Tools/MusicBee.exe")
        dialog.always_domains.setPlainText("https://SoundCloud.com/you\nsoundcloud.com")
        stored = {}
        with mock.patch(
            "cat_guardian.__main__.load_settings",
            return_value=dict(DEFAULT_SETTINGS),
        ), mock.patch(
            "cat_guardian.__main__.save_settings",
            side_effect=lambda value: stored.update(value),
        ):
            dialog.accept()
        self.assertEqual(stored["music_always_apps"], ["spotify.exe", "musicbee.exe"])
        self.assertEqual(stored["music_always_domains"], ["soundcloud.com"])

    def test_picker_adds_a_process_name(self):
        dialog = SettingsDialog(dict(DEFAULT_SETTINGS), "")
        dialog.always_app_pick.addItem("Spotify — spotify.exe", "spotify.exe")
        dialog.always_app_pick.setCurrentIndex(dialog.always_app_pick.count() - 1)
        dialog._add_always_app()
        self.assertIn("spotify.exe", dialog.always_apps.toPlainText().splitlines())


if __name__ == "__main__":
    unittest.main(verbosity=2)
