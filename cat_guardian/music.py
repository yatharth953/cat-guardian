"""Local music-versus-speech analysis of Windows playback.

The classifier never treats loudness as proof of music. It looks for a periodic
beat, sustained harmonics, bass presence, timbre stability, and compares those
against syllable-rate modulation, pitch movement, and voicing gaps. Detection
is deliberately conservative: a lecture should keep the cat asleep even when a
song is missed.

Audio is captured through WASAPI loopback, analysed in memory, and discarded.
Nothing is written to disk and nothing leaves this machine.
"""
from __future__ import annotations

import logging
import math
import sys
import threading
import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QObject, Signal

SAMPLE_RATE = 16000
FRAME = 1024
HOP = 512
WINDOW_SECONDS = 2.2
ANALYSIS_SECONDS = 0.25
SILENCE_DB = -55.0
STOP_GRACE = 3.5
CAPTURE_RATE = 48000
CAPTURE_BLOCK = 4096


@dataclass
class MusicReport:
    state: str          # "music", "speech", "silence", "uncertain"
    confidence: float
    evidence: float
    music: float
    speech: float
    tempo: float
    beat_strength: float
    intensity: float
    detail: str = ""


@dataclass
class BeatEvent:
    strength: float
    tempo: float
    intensity: float


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _com_initialize() -> bool:
    """COM is per-thread, but soundcard only initialises the thread that
    imports it. Every capture thread needs its own initialisation, otherwise
    restarting the monitor fails with 0x800401f0."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return ctypes.windll.ole32.CoInitializeEx(None, 0x2) in (0, 1)
    except Exception:
        return False


def _com_uninitialize() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.ole32.CoUninitialize()
    except Exception:
        pass


def _normalized_envelope(flux: np.ndarray) -> np.ndarray:
    env = flux - float(flux.min())
    return env / (float(np.percentile(env, 90)) + 1e-9)


def _fir_decimator():
    """Small low-pass filter for 48 kHz -> 16 kHz playback capture."""
    taps = 31
    cutoff = 0.30  # 7.2 kHz at a 24 kHz source Nyquist
    mid = (taps - 1) / 2
    n = np.arange(taps) - mid
    kernel = np.sinc(2 * cutoff * n) * np.hamming(taps)
    return (kernel / kernel.sum()).astype(np.float32)


_FIR = _fir_decimator()


class _StreamResampler:
    def __init__(self):
        self._tail = np.zeros(len(_FIR) - 1, dtype=np.float32)

    def process(self, block: np.ndarray) -> np.ndarray:
        if block.size == 0:
            return block
        joined = np.concatenate([self._tail, block.astype(np.float32, copy=False)])
        filtered = np.convolve(joined, _FIR, mode="valid")
        self._tail = joined[-(len(_FIR) - 1):]
        return filtered[::3]


class MusicClassifier:
    """Streaming feature extractor and hysteretic music decision."""

    def __init__(self, sensitivity: int = 55, activation_delay: float = 6.0):
        self._buffer = np.zeros(0, dtype=np.float32)
        self._last_analysis = 0.0
        self._last_beat_at = 0.0
        self._music_since: float | None = None
        self._nonmusic_since: float | None = None
        self.active = False
        self.sensitivity = int(_clamp(sensitivity, 0, 100))
        self.activation_delay = _clamp(float(activation_delay), 2.0, 30.0)
        self.last_report = MusicReport("silence", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    def configure(self, sensitivity: int | None = None, activation_delay: float | None = None):
        if sensitivity is not None:
            self.sensitivity = int(_clamp(sensitivity, 0, 100))
        if activation_delay is not None:
            self.activation_delay = _clamp(float(activation_delay), 2.0, 30.0)

    @property
    def threshold(self) -> float:
        # 0.66 at the least sensitive, 0.42 at the most sensitive.
        return 0.66 - 0.0024 * self.sensitivity

    def reset(self):
        self._buffer = np.zeros(0, dtype=np.float32)
        self._last_analysis = 0.0
        self._last_beat_at = 0.0
        self._music_since = None
        self._nonmusic_since = None
        self.active = False
        self.last_report = MusicReport("silence", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    def feed(self, samples: np.ndarray, now: float | None = None
             ) -> tuple[MusicReport | None, list[BeatEvent]]:
        if samples is None or samples.size == 0:
            return None, []
        now = time.monotonic() if now is None else now
        self._buffer = np.concatenate(
            [self._buffer, samples.astype(np.float32, copy=False)]
        )
        keep = int((WINDOW_SECONDS + 1.0) * SAMPLE_RATE)
        if self._buffer.size > keep:
            self._buffer = self._buffer[-keep:]
        if now - self._last_analysis < ANALYSIS_SECONDS:
            return None, []
        if self._buffer.size < int(1.0 * SAMPLE_RATE):
            return None, []
        self._last_analysis = now
        return self._analyse(now)

    # ------------------------------------------------------------- analysis

    def _analyse(self, now: float) -> tuple[MusicReport, list[BeatEvent]]:
        buf = self._buffer[-int(WINDOW_SECONDS * SAMPLE_RATE):]
        frames = np.lib.stride_tricks.sliding_window_view(buf, FRAME)[::HOP]
        if frames.shape[0] < 6:
            report = MusicReport("uncertain", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                 "not enough audio")
            return report, []

        window = np.hanning(FRAME).astype(np.float32)
        spec = np.abs(np.fft.rfft(frames * window, axis=1))
        power = spec * spec
        freqs = np.fft.rfftfreq(FRAME, 1.0 / SAMPLE_RATE)
        rms = np.sqrt(np.mean(frames * frames, axis=1))
        db = 20.0 * np.log10(rms + 1e-7)
        active = db > SILENCE_DB
        if active.mean() < 0.15:
            self._music_since = None
            self._nonmusic_since = None
            self.active = False
            report = MusicReport("silence", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                 "below the silence floor")
            self.last_report = report
            return report, []

        total = power.sum(axis=1) + 1e-12
        band = lambda lo, hi: power[:, (freqs >= lo) & (freqs < hi)].sum(axis=1) / total
        bass_ratio = float(np.median(band(40, 160)[active]))
        mid_ratio = float(np.median(band(300, 3000)[active]))
        high_ratio = float(np.median(band(4000, 7800)[active]))
        centroid = (power * freqs).sum(axis=1) / total
        centroid_mean = float(np.mean(centroid[active])) + 1e-6
        centroid_cv = float(np.std(centroid[active]) / centroid_mean)
        flat = np.exp(np.mean(np.log(power + 1e-12), axis=1)) / (np.mean(power, axis=1) + 1e-12)
        flatness = float(np.mean(flat[active]))

        # Harmonic sustain: how strongly each frame repeats at a pitch period.
        centred = frames - frames.mean(axis=1, keepdims=True)
        nfft = 2048
        xf = np.fft.rfft(centred, nfft, axis=1)
        ac = np.fft.irfft(xf * np.conj(xf), nfft, axis=1)[:, :FRAME]
        ac = ac / (ac[:, :1] + 1e-9)
        lag_lo, lag_hi = 40, 228  # 70 Hz .. 400 Hz
        peaks = ac[:, lag_lo:lag_hi].max(axis=1)
        lags = ac[:, lag_lo:lag_hi].argmax(axis=1) + lag_lo
        voiced = active & (peaks > 0.20)
        voiced_frac = float(voiced.sum() / max(1, active.sum()))
        harmonic = float(np.median(peaks[voiced])) if voiced.any() else 0.0
        pitch_jitter = 0.0
        if voiced.sum() >= 4:
            semis = 12.0 * np.log2(SAMPLE_RATE / lags[voiced].astype(np.float64))
            pitch_jitter = float(np.median(np.abs(np.diff(semis))))

        # Onset envelope and beat periodicity. Speech has a strong syllabic
        # amplitude modulation, so a periodic full-band envelope alone can look
        # like a beat. Real beats also show up as simultaneous transients in
        # the high band (percussion, plucks) and the low band (kick, bass), so
        # all three bands must agree before beat evidence is trusted.
        flux = np.zeros(len(spec), dtype=np.float32)
        if len(spec) > 1:
            flux[1:] = np.maximum(0.0, np.diff(spec, axis=0)).sum(axis=1)
        flux[~active] = 0.0
        env = _normalized_envelope(flux)
        beat_raw, tempo = self._beat_evidence(env)
        hf_beat, _ = self._beat_evidence(
            self._band_envelope(spec, freqs, 3000.0, 7800.0, active)
        )
        low_beat, _ = self._beat_evidence(
            self._band_envelope(spec, freqs, 40.0, 160.0, active)
        )
        beat_mix = (0.45 * beat_raw + 0.35 * hf_beat
                    + 0.20 * max(0.0, low_beat - 0.15))
        sharpness = float(np.mean(np.maximum(0.0, np.diff(env))) /
                          (np.mean(env) + 1e-9))
        duty = float(np.mean(env > 0.5))
        percussive = _clamp((sharpness - 0.34) / 0.32, 0.0, 1.0)
        continuous = _clamp((duty - 0.20) / 0.16, 0.0, 1.0)

        # Syllable-rate modulation: speech peaks around 3-6 Hz.
        env_freqs = np.fft.rfftfreq(len(env), HOP / SAMPLE_RATE)
        env_power = np.abs(np.fft.rfft(env - env.mean())) ** 2
        syllable_band = (env_freqs >= 2.5) & (env_freqs <= 7.5)
        base_band = (env_freqs >= 0.8) & (env_freqs <= 12.0)
        syllable_ratio = float(env_power[syllable_band].sum() /
                               (env_power[base_band].sum() + 1e-12))

        mean_db = float(np.mean(db[active]))
        timbre = 1.0 - _clamp((centroid_cv - 0.12) / 0.75, 0.0, 1.0)
        energy_stability = 1.0 - _clamp((float(np.std(db[active])) - 2.0) / 13.0, 0.0, 1.0)
        bass = _clamp((bass_ratio - 0.08) / 0.30, 0.0, 1.0)
        harmonic_s = _clamp((harmonic - 0.18) / 0.55, 0.0, 1.0)
        beat = _clamp((beat_mix - 0.08) / 0.55, 0.0, 1.0)
        syllable = _clamp((syllable_ratio - 0.08) / 0.45, 0.0, 1.0)
        jitter_s = _clamp((pitch_jitter - 0.08) / 1.30, 0.0, 1.0)
        gap = _clamp(((1.0 - voiced_frac) - 0.12) / 0.55, 0.0, 1.0)
        flat_s = _clamp(flatness / 0.45, 0.0, 1.0)

        music = (0.36 * beat + 0.22 * harmonic_s + 0.18 * bass
                 + 0.12 * timbre + 0.12 * energy_stability)
        # Distorted or percussive playback makes pitch tracking jumpy, so the
        # voicing cues are only trusted when the stream is not percussive.
        speech = (0.36 * syllable
                  + 0.22 * jitter_s * (1.0 - 0.70 * percussive)
                  + 0.16 * gap * (1.0 - 0.50 * percussive)
                  + 0.10 * flat_s + 0.16 * continuous
                  + 0.08 * (1.0 - percussive))
        evidence = 0.70 * music + 0.30 * (1.0 - speech)
        # Sustained, non-percussive amplitude modulation at a syllable rate is
        # speech, not a beat, even when it repeats faithfully enough to fool
        # envelope autocorrelation. This gate is what keeps lectures asleep.
        speechish = syllable >= 0.70 and percussive <= 0.45
        if speechish:
            evidence *= 0.55
        evidence = _clamp(evidence, 0.0, 1.0)

        # A steady lecture has almost no periodic beat. Requiring some beat
        # evidence is what keeps continuous speech from ever being "music".
        threshold = self.threshold
        if self.active:
            music_now = (evidence >= max(0.30, threshold - 0.06)
                         and beat >= 0.28 and not speechish)
        else:
            music_now = evidence >= threshold and beat >= 0.20 and not speechish

        if music_now:
            self._nonmusic_since = None
            if not self.active:
                if self._music_since is None:
                    self._music_since = now
                elif now - self._music_since >= self.activation_delay:
                    self.active = True
                    logging.info(
                        "Music detected (evidence %.2f, beat %.2f, tempo %.0f)",
                        evidence, beat, tempo,
                    )
        else:
            self._music_since = None
            if self.active:
                if self._nonmusic_since is None:
                    self._nonmusic_since = now
                elif now - self._nonmusic_since >= STOP_GRACE:
                    self.active = False
                    logging.info("Music no longer detected (evidence %.2f)", evidence)
            else:
                self._nonmusic_since = None

        intensity = _clamp((mean_db + 55.0) / 40.0, 0.10, 1.0) * (0.65 + 0.35 * beat)
        if self.active:
            state = "music"
            confidence = _clamp((evidence - 0.30) / 0.50, 0.45, 1.0)
        elif active.mean() < 0.2:
            state = "silence"
            confidence = 0.0
        elif speech >= 0.50:
            state = "speech"
            confidence = _clamp(speech, 0.0, 1.0)
        else:
            state = "uncertain"
            confidence = _clamp(1.0 - abs(evidence - threshold) * 2.0, 0.0, 1.0)

        detail = (
            f"music {music:.2f} speech {speech:.2f} beat {beat:.2f} "
            f"tempo {tempo:.0f} bass {bass:.2f} harm {harmonic_s:.2f} "
            f"syl {syllable:.2f} jit {jitter_s:.2f} gap {gap:.2f} flat {flat_s:.2f} "
            f"cont {continuous:.2f} perc {percussive:.2f}"
        )
        report = MusicReport(
            state, round(confidence, 3), round(evidence, 3), round(music, 3),
            round(speech, 3), round(tempo, 1), round(beat, 3),
            round(intensity, 3), detail,
        )
        self.last_report = report
        beats = self._recent_beats(env, now) if self.active else []
        return report, beats

    @staticmethod
    def _band_envelope(spec: np.ndarray, freqs: np.ndarray, low: float, high: float,
                       active: np.ndarray) -> np.ndarray:
        band = spec[:, (freqs >= low) & (freqs < high)]
        if band.size == 0:
            return np.zeros(spec.shape[0], dtype=np.float32)
        flux = np.zeros(len(band), dtype=np.float32)
        if len(band) > 1:
            flux[1:] = np.maximum(0.0, np.diff(band, axis=0)).sum(axis=1)
        flux[~active] = 0.0
        return _normalized_envelope(flux)

    def _beat_evidence(self, env: np.ndarray) -> tuple[float, float]:
        if len(env) < 12:
            return 0.0, 0.0
        centred = env - env.mean()
        ac = np.correlate(centred, centred, mode="full")[len(env) - 1:]
        ac = ac / (ac[0] + 1e-9)
        lag_lo, lag_hi = 9, 40  # 48 .. 214 BPM at a 32 ms envelope hop
        window = ac[lag_lo:lag_hi]
        lag = int(np.argmax(window)) + lag_lo
        strength = float(ac[lag])
        if 0 < lag < len(ac) - 1:
            left, centre, right = ac[lag - 1], ac[lag], ac[lag + 1]
            denom = left - 2 * centre + right
            if abs(denom) > 1e-9:
                lag = lag + 0.5 * (left - right) / denom
        tempo = 60.0 / max(0.30, lag * HOP / SAMPLE_RATE)
        return strength, tempo

    def _recent_beats(self, env: np.ndarray, now: float) -> list[BeatEvent]:
        if len(env) < 6:
            return []
        recent = env[-10:]
        threshold = float(np.mean(env)) + 1.1 * float(np.std(env)) + 0.12
        best_index = -1
        best_value = 0.0
        for offset, value in enumerate(recent):
            index = len(env) - len(recent) + offset
            if index <= 0 or index >= len(env) - 1:
                continue
            if (value >= threshold and value >= env[index - 1]
                    and value >= env[index + 1] and value > best_value):
                best_index = index
                best_value = float(value)
        if best_index < 0:
            return []
        stamp = now - (len(env) - 1 - best_index) * HOP / SAMPLE_RATE
        if stamp <= self._last_beat_at + 0.18:
            return []
        self._last_beat_at = stamp
        strength = _clamp(0.35 + best_value * 0.55, 0.0, 1.0)
        report = self.last_report
        return [BeatEvent(round(strength, 3), report.tempo, report.intensity)]


class MusicMonitor(QObject):
    """Capture thread plus Qt signals; only compact reports reach the UI."""

    report = Signal(object)
    beat = Signal(object)
    status = Signal(str, str)

    def __init__(self):
        super().__init__()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._classifier = MusicClassifier()
        self._settings = {
            "music_sensitivity": 55,
            "music_delay": 6.0,
        }

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, settings: dict):
        self._settings = dict(settings)
        self._classifier.configure(
            settings.get("music_sensitivity", 55),
            settings.get("music_delay", 6.0),
        )
        if self.running:
            return
        self._classifier.reset()
        self._stop.clear()
        self.status.emit("starting", "Starting…")
        self._thread = threading.Thread(
            target=self._run, name="cat-guardian-music", daemon=True
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.5)
        if thread is None or not thread.is_alive():
            self._classifier.reset()
        self.status.emit("off", "Off")

    def _run(self):
        com_ready = _com_initialize()
        try:
            self._capture()
        finally:
            if com_ready:
                _com_uninitialize()

    def _capture(self):
        try:
            import soundcard as sc
        except Exception:
            logging.warning("soundcard is not installed; music detection is unavailable")
            self.status.emit("unavailable", "Unavailable (soundcard missing)")
            return

        warning_logged = False
        while not self._stop.is_set():
            try:
                speaker = sc.default_speaker()
                if speaker is None:
                    raise RuntimeError("no default speaker")
                loopback = sc.get_microphone(id=str(speaker.name), include_loopback=True)
                if loopback is None:
                    raise RuntimeError("no loopback device")
                with loopback.recorder(
                    samplerate=CAPTURE_RATE, channels=2, blocksize=CAPTURE_BLOCK
                ) as recorder:
                    self.status.emit("listening", "Listening")
                    resampler = _StreamResampler()
                    while not self._stop.is_set():
                        block = recorder.record(numframes=CAPTURE_BLOCK)
                        if self._stop.is_set():
                            break
                        if block is None or len(block) == 0:
                            continue
                        mono = block.mean(axis=1) if block.ndim > 1 else block
                        samples = resampler.process(mono)
                        now = time.monotonic()
                        report, beats = self._classifier.feed(samples, now)
                        if report is not None:
                            self.report.emit(report)
                        for event in beats:
                            self.beat.emit(event)
            except Exception as exc:
                if self._stop.is_set():
                    break
                logging.warning("Music capture unavailable: %s", exc)
                if not warning_logged:
                    warning_logged = True
                    self.status.emit("unavailable", "Unavailable (audio device)")
                self._stop.wait(2.0)
        self.status.emit("off", "Off")


class BeatSound:
    """A very quiet beat tick; generated locally, never downloaded."""

    def __init__(self):
        self._effect = None
        self._failed = False
        self._last = 0.0

    def _ensure(self):
        if self._effect is not None or self._failed:
            return
        try:
            import wave
            from pathlib import Path

            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect

            from .settings import DATA_DIR

            path = Path(DATA_DIR) / "beat.wav"
            if not path.exists():
                rate = 22050
                duration = 0.07
                count = int(rate * duration)
                data = bytearray()
                for index in range(count):
                    t = index / rate
                    envelope = math.exp(-t * 45.0)
                    sample = int(32767 * 0.18 * envelope *
                                 math.sin(2 * math.pi * 720 * t))
                    data += int(sample).to_bytes(2, "little", signed=True)
                with wave.open(str(path), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(rate)
                    handle.writeframes(bytes(data))
            effect = QSoundEffect()
            effect.setSource(QUrl.fromLocalFile(str(path)))
            effect.setVolume(0.12)
            self._effect = effect
        except Exception:
            logging.exception("Could not prepare the beat sound")
            self._failed = True

    def play(self, enabled: bool, strength: float):
        if not enabled:
            return
        now = time.monotonic()
        if now - self._last < 0.55:
            return
        self._last = now
        self._ensure()
        if self._effect is not None:
            try:
                self._effect.setVolume(0.08 + 0.08 * _clamp(strength, 0.0, 1.0))
                self._effect.play()
            except Exception:
                pass
