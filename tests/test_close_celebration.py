"""Offscreen checks for the brag appended to a confirmed close.

The cat no longer plays a standalone celebration: after the close is
confirmed, the running close timeline grows by a walk to the centre, a head
shake with the "NO DISTRACTIONS!!!!" callout, and a walk back home.

Run with:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from cat_guardian import pet as pet_module  # noqa: E402
from cat_guardian.pet import CatGuardian, SpeechBubble  # noqa: E402
from cat_guardian.settings import DEFAULT_SETTINGS  # noqa: E402


class CloseBragTests(unittest.TestCase):
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

    def start_app_visit(self, pet):
        """Start the targetless visit used while a blocked app is closed."""
        with mock.patch("cat_guardian.native.send_to_top"), \
                mock.patch("cat_guardian.native.set_click_through"):
            pet.distraction(
                kind="app", label="steam.exe", info=None, targetless=True
            )
        pet._fade_anim.stop()
        pet.setWindowOpacity(1.0)
        self.assertTrue(pet._busy)
        self.assertEqual(pet._mode, "overlay")
        self.assertTrue(pet._timeline)

    def tearDown(self):
        for widget in QApplication.topLevelWidgets():
            widget.close()

    # ------------------------------------------------------------ the brag

    def test_confirmed_close_extends_the_running_visit(self):
        pet = self.make_pet()
        self.start_app_visit(pet)
        base = len(pet._timeline)
        pet._close_ticket_id = "req-1"
        pet.close_result("req-1", True, "closed")
        self.assertEqual(pet._mode, "overlay")
        self.assertTrue(pet._busy)
        self.assertTrue(pet._brag_planned)
        self.assertEqual(len(pet._timeline), base + 3)
        enter, shake, back = pet._timeline[-3:]
        self.assertEqual([enter.mood, shake.mood, back.mood],
                         ["walk", "proud", "walk"])
        centre = pet._greeting_center()
        self.assertEqual(enter.end, centre)
        self.assertEqual(shake.start, centre)
        self.assertEqual(shake.end, centre)
        self.assertEqual(back.end, pet._clamp_pos(pet._desk_pos))
        pet.shutdown()

    def test_site_confirmation_settles_the_close_beats(self):
        from cat_guardian.pet import Segment

        pet = self.make_pet()
        now = time.monotonic()
        spot = QPoint(200, 300)
        pet._desk_pos = QPoint(spot)
        pet._busy = True
        pet._mode = "overlay"
        pet._timeline = [
            Segment(0.3, spot, spot, "tap"),
            Segment(0.4, spot, spot, "react"),
            Segment(0.6, spot, spot, "smug"),
        ]
        pet._seg_i = 1
        pet._seg_t0 = now - 0.1
        pet.confirm_close("site")
        self.assertLessEqual(pet._timeline[1].dur, 0.29)
        self.assertLessEqual(pet._timeline[2].dur, 0.18)
        self.assertEqual(pet._timeline[-1].end, pet._clamp_pos(spot))
        pet.shutdown()

    def test_brag_walks_out_of_the_close_pose(self):
        pet = self.make_pet()
        self.start_app_visit(pet)
        tap_spot = pet._timeline[-1].end
        pet.confirm_close("app")
        self.assertEqual(pet._timeline[-3].start, tap_spot)
        self.assertEqual(pet._timeline[-2].start, pet._timeline[-3].end)
        pet.shutdown()

    def test_confirmation_is_only_appended_once(self):
        pet = self.make_pet()
        self.start_app_visit(pet)
        base = len(pet._timeline)
        pet.confirm_close("app")
        pet.confirm_close("app")
        self.assertEqual(len(pet._timeline), base + 3)
        pet.shutdown()

    def test_interrupted_close_never_brags(self):
        pet = self.make_pet()
        self.start_app_visit(pet)
        base = len(pet._timeline)
        pet._close_aborted = True
        pet._close_ticket_id = "req-3"
        pet.close_result("req-3", True, "closed")
        self.assertEqual(len(pet._timeline), base)
        self.assertFalse(pet._brag_planned)
        pet.shutdown()

    def test_failed_close_never_brags(self):
        pet = self.make_pet()
        self.start_app_visit(pet)
        base = len(pet._timeline)
        pet._close_ticket_id = "req-2"
        pet.close_result("req-2", False, "navigated")
        self.assertEqual(len(pet._timeline), base)
        self.assertFalse(pet._brag_planned)
        self.assertTrue(pet._close_failed)
        pet.shutdown()

    def test_confirmation_outside_a_visit_does_nothing(self):
        pet = self.make_pet()
        pet.confirm_close("site")
        self.assertFalse(pet._timeline)
        self.assertFalse(pet._brag_planned)
        self.assertEqual(pet._mode, "desktop")
        self.assertIsNone(pet._speech)
        pet.shutdown()

    def test_hidden_cat_never_brags(self):
        pet = self.make_pet()
        self.start_app_visit(pet)
        base = len(pet._timeline)
        pet._user_hidden = True
        pet.confirm_close("app")
        self.assertEqual(len(pet._timeline), base)
        pet.shutdown()

    def test_brag_keeps_the_whole_visit_inside_ten_seconds(self):
        pet = self.make_pet()
        self.start_app_visit(pet)
        pet.confirm_close("app")
        self.assertLess(sum(seg.dur for seg in pet._timeline), 10.0)
        pet.shutdown()

    # ------------------------------------------------- bubble and head shake

    def test_bubble_opens_on_proud_and_closes_on_the_walk_home(self):
        pet = self.make_pet()
        self.start_app_visit(pet)
        pet.confirm_close("app")
        walk_i = len(pet._timeline) - 3
        pet._seg_i = walk_i
        pet._seg_t0 = time.monotonic() - pet._timeline[walk_i].dur - 0.001
        pet._update_timeline(time.monotonic())
        self.assertEqual(pet.mood, "proud")
        self.assertIsNotNone(pet._speech)
        self.assertEqual(pet._speech._text, "NO DISTRACTIONS!!!!")
        self.assertTrue(pet._speech.isVisible())
        self.assertFalse(pet.grab().isNull())

        shake_i = walk_i + 1
        pet._seg_t0 = time.monotonic() - pet._timeline[shake_i].dur - 0.001
        pet._update_timeline(time.monotonic())
        self.assertEqual(pet.mood, "walk")
        self.assertFalse(pet._speech.isVisible())
        pet.shutdown()

    def test_finishing_the_visit_returns_home_and_clears_the_brag(self):
        pet = self.make_pet()
        home = QPoint(pet.pos())
        self.start_app_visit(pet)
        pet.confirm_close("app")
        last = len(pet._timeline) - 1
        pet._seg_i = last
        pet._seg_t0 = time.monotonic() - pet._timeline[last].dur - 0.001
        with mock.patch("cat_guardian.native.send_to_top"), \
                mock.patch("cat_guardian.native.set_click_through"):
            pet._update_timeline(time.monotonic())
            self.assertFalse(pet._timeline)
            pet._fade_anim.stop()
            pet._return_to_desktop()
        self.assertFalse(pet._busy)
        self.assertEqual(pet._mode, "desktop")
        self.assertEqual(pet.pos(), home)
        self.assertFalse(pet._brag_planned)
        self.assertFalse(
            pet.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        )
        pet.shutdown()

    def test_head_shake_moves_the_face_both_ways(self):
        pet = self.make_pet()
        pet._set_mood("proud")
        start = pet._mood_since
        left = pet._visual(start + 0.15).head_tilt
        right = pet._visual(start + 0.475).head_tilt
        self.assertGreater(left, 5.0)
        self.assertLess(right, -5.0)
        level = pet._visual(start + pet_module.BRAG_SHAKE_MS / 1000.0)
        self.assertAlmostEqual(level.head_tilt, 0.0, places=3)
        self.assertEqual(level.eye_mode, "happy")
        pet.shutdown()

    def test_hide_dismisses_the_callout(self):
        pet = self.make_pet()
        with mock.patch("cat_guardian.native.send_to_top"), \
                mock.patch("cat_guardian.native.set_click_through"):
            self.start_app_visit(pet)
            pet.confirm_close("app")
            pet._set_mood("proud")
            self.assertTrue(pet._speech.isVisible())
            pet.hide_cat()
        self.assertFalse(pet._speech.isVisible())
        self.assertTrue(pet.hidden_by_user)
        pet.show_cat()
        self.assertFalse(pet._speech.isVisible())
        pet.shutdown()

    def test_bubble_is_wide_enough_and_on_screen(self):
        bubble = SpeechBubble()
        text = "NO DISTRACTIONS!!!!"
        bubble.say(text, QPoint(5, 5))
        metrics = QFontMetrics(bubble._font)
        self.assertGreaterEqual(
            bubble.width(), metrics.horizontalAdvance(text) + 20
        )
        area = (QGuiApplication.primaryScreen().availableGeometry()
                if QGuiApplication.primaryScreen() else None)
        if area is not None:
            self.assertGreaterEqual(bubble.x(), area.left())
            self.assertLessEqual(bubble.geometry().right(), area.right())
        bubble.close()

    # ----------------------------------------------------------- app closing

    def test_app_visit_holds_for_the_close_confirmation(self):
        from cat_guardian.__main__ import APP_CLOSE_SYNC_MS

        pet = self.make_pet()
        self.start_app_visit(pet)
        self.assertGreaterEqual(
            pet._timeline[-1].dur, APP_CLOSE_SYNC_MS / 1000.0
        )
        pet.shutdown()

    def test_app_exit_is_confirmed_while_the_visit_runs(self):
        from cat_guardian.__main__ import CatGuardianApp

        app = CatGuardianApp.__new__(CatGuardianApp)
        app.pet = mock.Mock()
        info = SimpleNamespace(pid=4242, exe="steam.exe", hwnd=1)
        with mock.patch(
                "cat_guardian.__main__.native.is_process_running",
                return_value=False):
            CatGuardianApp._wait_close_app(app, info, 1)
        app.pet.confirm_close.assert_called_once_with("app")

    def test_app_close_force_fallback_confirms(self):
        from cat_guardian.__main__ import CatGuardianApp

        app = CatGuardianApp.__new__(CatGuardianApp)
        app.pet = mock.Mock()
        info = SimpleNamespace(pid=4242, exe="steam.exe", hwnd=1)
        with mock.patch(
                "cat_guardian.__main__.native.is_process_running",
                return_value=True), mock.patch(
                "cat_guardian.__main__.native.terminate_process",
                return_value=True):
            CatGuardianApp._force_close_app(app, info)
        app.pet.confirm_close.assert_called_once_with("app")


if __name__ == "__main__":
    unittest.main(verbosity=2)
