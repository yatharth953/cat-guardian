from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, Signal

from . import native
from .settings import domain_matches, load_settings

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 8765
EXTENSION_ORIGIN_PREFIX = "chrome-extension://"

RESULT_TTL = 30.0

# One close event must finish inside ten seconds. While the cat is on screen the
# tab is removed as its paw lands (about 2.5 s into the visit); with no cat to
# animate it closes at once. Both paths are clamped to this window.
CLOSE_WINDOW_MS = 10_000
CLOSE_SYNC_DELAY_MS = 2_500


def close_delay_ms(animating: bool, delay_ms: int = CLOSE_SYNC_DELAY_MS) -> int:
    """Delay before a tab is removed, never outside the ten-second window."""
    if not animating:
        return 0
    return max(0, min(int(delay_ms), CLOSE_WINDOW_MS))


def _expire_close_window(server, request_id: str, tab_id: int) -> None:
    """Report a close that was never confirmed inside the ten-second window."""
    with server.tickets_lock:
        pending = server.tickets.pop(request_id, None)
    if pending is not None:
        logging.warning(
            "Close window (%s s) expired before tab %s was confirmed",
            CLOSE_WINDOW_MS // 1000, tab_id,
        )

# The cat only climbs onto a window when a browser is actually in front.
BROWSER_EXES = {
    "chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe",
    "opera.exe", "chromium.exe", "firefox.exe",
}


@dataclass
class SiteEvent:
    url: str
    title: str
    tab_id: int
    tab_index: int | None
    tab_count: int | None
    info: object
    ticket: "CloseTicket | None"
    close: bool


class CloseTicket:
    """Bookkeeping for one tab-close request.

    The extension is answered at once and removes the stored tab by id, so the
    close never waits for the cat. The ticket only lets the animation show
    success or failure while it plays.
    """

    def __init__(self, tab_id: int, tab_index=None, tab_count=None):
        self.request_id = uuid.uuid4().hex[:12]
        self.tab_id = tab_id
        self.tab_index = tab_index
        self.tab_count = tab_count
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._contact = False

    @property
    def resolved(self) -> bool:
        return self._event.is_set()

    @property
    def contact(self) -> bool:
        return self._contact

    def reach_contact(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._contact = True
            self._event.set()

    def close_now(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._contact = False
            self._event.set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)


class BridgeSignals(QObject):
    # SiteEvent object
    distraction = Signal(object)
    # request id, ok, reason
    close_result = Signal(str, bool, str)
    # hostname, audible (only allowlisted hosts are forwarded)
    music_source = Signal(str, bool)


class LocalBridgeHandler(BaseHTTPRequestHandler):
    # The extension may only talk to localhost; no LAN binding.
    server_version = "CatGuardianLocal/3.0"

    def log_message(self, fmt, *args):
        logging.info("Bridge: " + fmt, *args)

    def _send(self, code: int, payload: dict):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", ""))
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        if not origin.startswith(EXTENSION_ORIGIN_PREFIX):
            self._send(403, {"ok": False, "error": "origin not allowed"})
            return
        self._send(200, {"ok": True})

    def do_POST(self):
        origin = self.headers.get("Origin", "")
        if not origin.startswith(EXTENSION_ORIGIN_PREFIX):
            self._send(403, {"ok": False, "error": "extension origin required"})
            return
        if self.path == "/event":
            self._handle_event()
        elif self.path == "/closed":
            self._handle_closed()
        elif self.path == "/music":
            self._handle_music()
        else:
            self._send(404, {"ok": False, "error": "not found"})

    def _read_json(self) -> dict | None:
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > 8192:
            self._send(400, {"ok": False, "error": "invalid body size"})
            return None
        try:
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
        except Exception:
            self._send(400, {"ok": False, "error": "invalid JSON"})
            return None
        if not isinstance(payload, dict):
            self._send(400, {"ok": False, "error": "invalid JSON"})
            return None
        return payload

    def _handle_event(self):
        payload = self._read_json()
        if payload is None:
            return
        try:
            url = str(payload.get("url", ""))[:2048]
            title = str(payload.get("title", ""))[:200]
            tab_id = payload.get("tabId")
            if not isinstance(tab_id, int) or not url.startswith(("http://", "https://")):
                self._send(400, {"ok": False, "error": "invalid event"})
                return
            tab_index = payload.get("tabIndex")
            tab_count = payload.get("tabCount")
            if not isinstance(tab_index, int) or isinstance(tab_index, bool):
                tab_index = None
            if not isinstance(tab_count, int) or isinstance(tab_count, bool):
                tab_count = None
            settings = load_settings()
            if not settings.get("enabled"):
                self._send(200, {"ok": True, "action": "ignore"})
                return
            if not domain_matches(url, settings["blocked_domains"]):
                self._send(200, {"ok": True, "action": "ignore"})
                return

            close = bool(settings.get("close_tabs"))
            active = bool(payload.get("active", True))
            front = native.foreground_window_info()
            show_cat = active and bool(front and front.exe.lower() in BROWSER_EXES)
            animating = close and show_cat
            if animating:
                provider = getattr(self.server, "animates_provider", None)
                if callable(provider):
                    try:
                        animating = bool(provider())
                    except Exception:
                        logging.debug("Cat visibility check failed", exc_info=True)
            delay_ms = close_delay_ms(animating)
            ticket = None
            if close and show_cat and hasattr(self.server, "event_signals"):
                ticket = CloseTicket(tab_id, tab_index, tab_count)
                event = SiteEvent(
                    url=url, title=title, tab_id=tab_id,
                    tab_index=tab_index, tab_count=tab_count,
                    info=front, ticket=ticket, close=True,
                )
                # The cat animates on the UI thread. The answer below tells the
                # extension to remove the stored tab at the paw contact (or at
                # once when there is no cat to show); it never waits for the
                # animation, so a stalled visit cannot lose the close.
                self.server.event_signals.distraction.emit(event)
            elif show_cat and hasattr(self.server, "event_signals"):
                event = SiteEvent(
                    url=url, title=title, tab_id=tab_id,
                    tab_index=tab_index, tab_count=tab_count,
                    info=front, ticket=None, close=False,
                )
                self.server.event_signals.distraction.emit(event)

            response = {
                "ok": True,
                "action": "close" if close else "warn",
                "tabId": tab_id,
                "contact": False,
                "delayMs": delay_ms,
            }
            if ticket is not None:
                response["requestId"] = ticket.request_id
                stamp = time.monotonic()
                with self.server.tickets_lock:
                    stale = [rid for rid, ts in self.server.tickets.items()
                             if stamp - ts > RESULT_TTL]
                    for rid in stale:
                        self.server.tickets.pop(rid, None)
                    self.server.tickets[ticket.request_id] = stamp
                if delay_ms:
                    logging.info(
                        "Closing tab %s in %s ms, timed to the paw contact",
                        tab_id, delay_ms,
                    )
                else:
                    logging.info(
                        "Closing tab %s immediately through the extension", tab_id
                    )
                watchdog = threading.Timer(
                    CLOSE_WINDOW_MS / 1000.0, _expire_close_window,
                    args=(self.server, ticket.request_id, tab_id),
                )
                watchdog.daemon = True
                watchdog.start()
            self._send(200, response)
        except Exception as exc:
            logging.exception("Bridge event failed")
            self._send(400, {"ok": False, "error": str(exc)[:200]})

    def _handle_music(self):
        """Audible-tab beacon for the user's always-music website list.

        The extension sends the hostnames of audible tabs (older versions send
        the active tab's host and audible flag). Hostnames that are not on the
        list are never forwarded or logged; they only clear any previous
        allowlisted source, so the pet cannot keep dancing to a site the user
        left.
        """
        payload = self._read_json()
        if payload is None:
            return
        allowed = load_settings().get("music_always_domains", [])
        matched = ""
        hosts = payload.get("hosts")
        if isinstance(hosts, list):
            for raw in hosts[:16]:
                candidate = str(raw).strip().lower().rstrip(".")[:200]
                if candidate and domain_matches(f"https://{candidate}", allowed):
                    matched = candidate
                    break
        else:
            host = str(payload.get("host", "")).strip().lower().rstrip(".")[:200]
            if (bool(payload.get("audible")) and host
                    and domain_matches(f"https://{host}", allowed)):
                matched = host
        if hasattr(self.server, "event_signals"):
            self.server.event_signals.music_source.emit(matched, bool(matched))
        if matched:
            logging.info("Always-music site beacon: %s", matched)
        self._send(200, {"ok": True, "tracked": bool(matched)})

    def _handle_closed(self):
        payload = self._read_json()
        if payload is None:
            return
        request_id = str(payload.get("requestId", ""))[:64]
        ok = bool(payload.get("ok"))
        reason = str(payload.get("reason", ""))[:40]
        known = False
        server = self.server
        with server.tickets_lock:
            known = request_id in server.tickets
            if known:
                server.tickets.pop(request_id, None)
        if not known:
            self._send(200, {"ok": True, "ignored": True})
            return
        if hasattr(server, "event_signals"):
            server.event_signals.close_result.emit(request_id, ok, reason)
        logging.info("Close result %s ok=%s reason=%s", request_id, ok, reason or "-")
        self._send(200, {"ok": True})


class BridgeServer:
    def __init__(self, animates_provider=None):
        self.httpd = ThreadingHTTPServer((BRIDGE_HOST, BRIDGE_PORT), LocalBridgeHandler)
        self.httpd.daemon_threads = True
        self.signals = BridgeSignals()
        self.httpd.event_signals = self.signals
        # Read on the HTTP thread. The app supplies a plain Python flag (the
        # pet's hidden state), never a widget call from another thread.
        self.httpd.animates_provider = animates_provider
        self.httpd.tickets: dict[str, float] = {}
        self.httpd.tickets_lock = threading.Lock()
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        logging.info("Local bridge listening on %s:%s", BRIDGE_HOST, BRIDGE_PORT)

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            logging.exception("Bridge shutdown failed")
