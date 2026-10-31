"""Checks for the ten-second close window and the paw-synced delay.

The HTTP path is exercised live; these tests pin the timing contract so the
tab is never removed later than ten seconds after detection.
Run with:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cat_guardian import bridge as bridge_module  # noqa: E402
from cat_guardian.bridge import (  # noqa: E402
    CLOSE_SYNC_DELAY_MS, CLOSE_WINDOW_MS, close_delay_ms,
)
from cat_guardian.native import WindowInfo  # noqa: E402


class _Signal:
    def __init__(self):
        self.events = []

    def emit(self, value):
        self.events.append(value)


class _Timer:
    def __init__(self, *args, **kwargs):
        self.daemon = False
        self.started = False

    def start(self):
        self.started = True


def _handler(animates: bool):
    handler = object.__new__(bridge_module.LocalBridgeHandler)
    payload = {
        "tabId": 5,
        "url": "https://www.instagram.com/reel/x",
        "title": "Instagram",
        "active": True,
    }
    handler._read_json = lambda: payload
    handler.sent = []
    handler._send = lambda code, body: handler.sent.append((code, body))
    handler.server = SimpleNamespace(
        event_signals=SimpleNamespace(distraction=_Signal()),
        animates_provider=lambda: animates,
        tickets={},
        tickets_lock=threading.Lock(),
    )
    return handler


class CloseWindowTests(unittest.TestCase):
    def test_animating_close_is_timed_to_the_paw(self):
        self.assertEqual(close_delay_ms(True), CLOSE_SYNC_DELAY_MS)

    def test_no_animation_closes_at_once(self):
        self.assertEqual(close_delay_ms(False), 0)

    def test_bad_delays_are_clamped(self):
        self.assertEqual(close_delay_ms(True, 10**9), CLOSE_WINDOW_MS)
        self.assertEqual(close_delay_ms(True, -50), 0)

    def test_paw_delay_fits_inside_the_window(self):
        self.assertLess(CLOSE_SYNC_DELAY_MS, CLOSE_WINDOW_MS)
        self.assertEqual(CLOSE_WINDOW_MS, 10_000)

    def test_foreground_close_answers_with_the_paw_delay(self):
        handler = _handler(animates=True)
        settings = {
            "enabled": True,
            "blocked_domains": ["instagram.com"],
            "close_tabs": True,
        }
        front = WindowInfo(
            hwnd=1, pid=2, exe="chrome.exe", title="Instagram",
            left=0, top=0, right=800, bottom=600, shell=False,
        )
        timer = _Timer()
        with mock.patch.object(
            bridge_module.native, "foreground_window_info", return_value=front
        ), mock.patch.object(
            bridge_module, "load_settings", return_value=settings
        ), mock.patch.object(
            bridge_module.threading, "Timer", return_value=timer
        ):
            handler._handle_event()
        code, body = handler.sent[-1]
        self.assertEqual(code, 200)
        self.assertEqual(body["action"], "close")
        self.assertEqual(body["delayMs"], CLOSE_SYNC_DELAY_MS)
        self.assertIn("requestId", body)
        self.assertTrue(timer.started)
        self.assertEqual(len(handler.server.event_signals.distraction.events), 1)

    def test_hidden_cat_close_has_no_delay(self):
        handler = _handler(animates=False)
        settings = {
            "enabled": True,
            "blocked_domains": ["instagram.com"],
            "close_tabs": True,
        }
        front = WindowInfo(
            hwnd=1, pid=2, exe="chrome.exe", title="Instagram",
            left=0, top=0, right=800, bottom=600, shell=False,
        )
        with mock.patch.object(
            bridge_module.native, "foreground_window_info", return_value=front
        ), mock.patch.object(
            bridge_module, "load_settings", return_value=settings
        ), mock.patch.object(
            bridge_module.threading, "Timer", return_value=_Timer()
        ):
            handler._handle_event()
        _, body = handler.sent[-1]
        self.assertEqual(body["action"], "close")
        self.assertEqual(body["delayMs"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
