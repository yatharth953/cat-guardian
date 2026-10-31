"""Offscreen checks for the post-close centre celebration.

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
from cat_guardian.native import WindowInfo  # noqa: E402
from cat_guardian.pet import CatGuardian, SpeechBubble  # noqa: E402
from cat_guardian.settings import DEFAULT_SETTINGS  # noqa: E402


class CloseCelebrationTests(unittest.TestCase):
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

    def test_confirmed_close_queues_celebration_while_the_tap_finishes(self):
        pet = self.make_pet()
        pet._close_ticket_id = "req-1"
        pet._busy = True
        pet._mode = "overlay"
        pet.close_result("req-1", True, "closed")
        self.assertTrue(pet._celebrate_pending)
        self.assertFalse(pet._celebrating)
        pet.close_result("other", True, "closed")
        self.assertTrue(pet._celebrate_pending)
        pet.shutdown()

    def test_interrupted_close_does_not_celebrate(self):
        pet = self.make_pet()
        pet._close_ticket_id = "req-3"
        pet._busy = True
        pet._mode = "overlay"
        pet._close_aborted = True
        pet.close_result("req-3", True, "closed")
        self.assertFalse(pet._celebrate_pending)
        pet.shutdown()

    def test_failed_close_never_celebrates(self):
        pet = self.make_pet()
        pet._close_ticket_id = "req-2"
        pet._busy = True
        pet._mode = "overlay"
        pet.close_result("req-2", False, "navigated")
        self.assertFalse(pet._celebrate_pending)
        pet.shutdown()

    def test_celebration_centres_shakes_and_returns_home(self):
        pet = self.make_pet()
        pet.move(10, 12)
        home = QPoint(pet.pos())
        with mock.patch("cat_guardian.native.send_to_top"), \
                mock.patch("cat_guardian.native.set_click_through"):
            pet.celebrate_close("site")
            self.assertTrue(pet._celebrating)
            self.assertEqual(pet._mode, "celebrate")
            self.assertEqual(pet._celebrate_home, home)
            self.assertEqual(pet.mood, "walk")
            self.assertTrue(
                pet.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            )

            pet._celebrate_move_in()
            self.assertEqual(pet._celebrate_anim.endValue(), pet._greeting_center())
            pet._celebrate_anim.stop()

            pet._celebrate_shake()
            self.assertEqual(pet.mood, "proud")
            self.assertIsNotNone(pet._speech)
            self.assertEqual(pet._speech._text, "NO DISTRACTIONS!!!!")
            self.assertTrue(pet._speech.isVisible())
            self.assertFalse(pet.grab().isNull())
            self.assertFalse(pet._speech.grab().isNull())

            pet._celebrate_return()
            self.assertFalse(pet._speech.isVisible())
            self.assertEqual(pet.mood, "walk")
            pet._celebrate_anim.stop()

            pet._finish_celebration()
        self.assertFalse(pet._celebrating)
        self.assertEqual(pet._mode, "desktop")
        self.assertEqual(pet.pos(), home)
        self.assertEqual(pet.mood, "idle")
        self.assertFalse(
            pet.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        )
        pet.shutdown()

    def test_celebration_continues_from_the_close_position(self):
        pet = self.make_pet()
        pet.move(40, 40)
        home = QPoint(pet.pos())
        pet._desk_pos = QPoint(home)
        pet._busy = True
        pet._mode = "overlay"
        pet._celebrate_pending = True
        pet.move(300, 20)
        tap = QPoint(pet.pos())
        with mock.patch("cat_guardian.native.send_to_top"), \
                mock.patch("cat_guardian.native.set_click_through"):
            pet._return_to_desktop()
            self.assertTrue(pet._celebrating)
            self.assertEqual(pet._mode, "celebrate")
            self.assertEqual(pet._celebrate_home, home)
            # No reset to the desktop spot before the brag.
            self.assertEqual(pet.pos(), tap)
            self.assertEqual(pet.mood, "walk")
            center = pet._greeting_center()
            pet._fade_anim.stop()
            pet.setWindowOpacity(1.0)
            pet._celebrate_move_in()
            self.assertEqual(pet._celebrate_anim.endValue(), center)
            pet._celebrate_anim.stop()
            pet._celebrate_shake()
            pet._celebrate_return()
            pet._celebrate_anim.stop()
            pet._finish_celebration()
        self.assertEqual(pet.pos(), home)
        pet.shutdown()

    def test_interrupted_visit_drops_a_pending_celebration(self):
        pet = self.make_pet()
        pet._desk_pos = QPoint(pet.pos())
        pet._busy = True
        pet._mode = "overlay"
        pet._celebrate_pending = True
        pet._close_aborted = True
        with mock.patch("cat_guardian.native.send_to_top"), \
                mock.patch("cat_guardian.native.set_click_through"):
            pet._return_to_desktop()
        self.assertFalse(pet._celebrating)
        self.assertFalse(pet._celebrate_pending)
        self.assertEqual(pet._mode, "desktop")
        pet.shutdown()

    def test_head_shake_moves_the_face_both_ways(self):
        pet = self.make_pet()
        pet._set_mood("proud")
        start = pet._mood_since
        left = pet._visual(start + 0.15).head_tilt
        right = pet._visual(start + 0.475).head_tilt
        self.assertGreater(left, 5.0)
        self.assertLess(right, -5.0)
        level = pet._visual(start + pet_module.CELEBRATE_SHAKE_MS / 1000.0)
        self.assertAlmostEqual(level.head_tilt, 0.0, places=3)
        self.assertEqual(level.eye_mode, "happy")
        pet.shutdown()

    def test_hide_cancels_the_celebration(self):
        pet = self.make_pet()
        with mock.patch("cat_guardian.native.send_to_top"), \
                mock.patch("cat_guardian.native.set_click_through"):
            pet.celebrate_close()
            pet._celebrate_shake()
            self.assertTrue(pet._celebrating)
            pet.hide_cat()
        self.assertFalse(pet._celebrating)
        self.assertEqual(pet._mode, "desktop")
        self.assertFalse(pet._speech.isVisible())
        pet.show_cat()
        pet.shutdown()

    def test_new_distraction_cancels_the_celebration(self):
        pet = self.make_pet()
        with mock.patch("cat_guardian.native.send_to_top"), \
                mock.patch("cat_guardian.native.set_click_through"):
            pet.celebrate_close()
            pet._celebrate_shake()
            self.assertTrue(pet._celebrating)
            pet.distraction(kind="site", label="example.com", info=WindowInfo(
                hwnd=0, pid=0, exe="", title="", left=0, top=0,
                right=400, bottom=300, shell=True,
            ))
        self.assertFalse(pet._celebrating)
        self.assertTrue(pet._busy)
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

    def test_app_close_confirmation_celebrates(self):
        from cat_guardian.__main__ import CatGuardianApp

        app = CatGuardianApp.__new__(CatGuardianApp)
        app.pet = mock.Mock()
        info = SimpleNamespace(pid=4242, exe="steam.exe", hwnd=1)
        with mock.patch(
                "cat_guardian.__main__.native.is_process_running",
                return_value=False):
            CatGuardianApp._force_close_app(app, info)
        app.pet.celebrate_close.assert_called_once_with("app")

    def test_app_termination_celebrates(self):
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
        app.pet.celebrate_close.assert_called_once_with("app")


if __name__ == "__main__":
    unittest.main(verbosity=2)
