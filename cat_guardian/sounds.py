"""The launch meow.

The bundled ``assets/meow.wav`` is a short cat meow converted from the supplied
MP3 to 22.05 kHz mono so it can be played asynchronously with ``winsound``.
If the asset is missing, a synthesized meow is generated in the per-user data
folder instead. Nothing is downloaded and nothing is recorded.
"""
from __future__ import annotations

import logging
import math
import sys
import wave
from pathlib import Path

from .settings import DATA_DIR

ASSET_PATH = Path(__file__).resolve().parent / "assets" / "meow.wav"


class MeowSound:
    """A quiet one-shot meow; silently disabled when audio output is missing."""

    def __init__(self):
        self._path: Path | None = None
        self._failed = False

    def _ensure(self) -> None:
        if self._path is not None or self._failed:
            return
        if sys.platform != "win32":
            self._failed = True
            return
        try:
            if ASSET_PATH.exists():
                self._path = ASSET_PATH
            else:
                path = Path(DATA_DIR) / "meow.wav"
                if not path.exists():
                    self._write(path)
                self._path = path
            logging.info("Meow sound ready: %s", self._path)
        except Exception:
            logging.exception("Could not prepare the meow sound")
            self._failed = True

    @staticmethod
    def _write(path: Path) -> None:
        rate = 22050
        duration = 0.45
        count = int(rate * duration)
        data = bytearray()
        phase = 0.0
        for index in range(count):
            t = index / rate
            u = t / duration
            glide = 520.0 + 260.0 * math.sin(math.pi * min(1.0, u * 0.92))
            vibrato = 1.0 + 0.025 * math.sin(2 * math.pi * 7.5 * t)
            phase += 2 * math.pi * glide * vibrato / rate
            envelope = min(1.0, t / 0.045) * min(1.0, max(0.0, (duration - t) / 0.16))
            tone = (
                0.55 * math.sin(phase)
                + 0.28 * math.sin(2 * phase)
                + 0.12 * math.sin(3 * phase)
                + 0.05 * math.sin(4 * phase)
            )
            sample = int(32767 * 0.22 * envelope * tone)
            data += sample.to_bytes(2, "little", signed=True)
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(bytes(data))

    def play(self) -> None:
        self._ensure()
        if self._path is None:
            return
        try:
            import winsound

            logging.info("Playing meow: %s", self._path)
            winsound.PlaySound(
                str(self._path),
                winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
            )
        except Exception:
            logging.exception("Could not play the meow sound")
            self._failed = True
