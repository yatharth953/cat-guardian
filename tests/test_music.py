"""Offline checks for the music-versus-speech classifier.

The fixtures are synthetic, so these tests prove that the decision logic,
hysteresis, and beat tracking behave as intended. They do not prove real-world
accuracy; that requires listening tests with actual lectures and songs.
Run with:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cat_guardian.music import MusicClassifier, SAMPLE_RATE  # noqa: E402
from cat_guardian.settings import (  # noqa: E402
    valid_music_delay, valid_music_style, valid_percent,
)

RNG = np.random.default_rng(7)


def _envelope(count: int, attack=0.01, decay=0.1, sustain=0.7, release=0.2):
    a = max(1, int(attack * SAMPLE_RATE))
    d = max(1, int(decay * SAMPLE_RATE))
    r = max(1, int(release * SAMPLE_RATE))
    s = max(0, count - a - d - r)
    return np.concatenate([
        np.linspace(0, 1, a), np.linspace(1, sustain, d),
        np.full(s, sustain), np.linspace(sustain, 0, r),
    ])[:count]


def _resonator(x, freq, q=8.0):
    w = 2 * np.pi * freq / SAMPLE_RATE
    r = np.exp(-w / (2 * q))
    y = np.zeros_like(x)
    y1 = y2 = 0.0
    for index, value in enumerate(x):
        out = (1 - r) * value - (-2 * r * np.cos(w)) * y1 - (r * r) * y2
        y1, y2 = out, y1
        y[index] = out
    return y


def speech(seconds=10.0, seed=1):
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    syllable = 0.55 + 0.45 * np.sin(2 * np.pi * 4.1 * t + 0.7)
    syllable = np.clip(syllable, 0, 1) ** 1.5
    gate = np.ones(n)
    for start in (2.6, 5.2, 7.8):
        gate[int(start * SAMPLE_RATE):int((start + 0.55) * SAMPLE_RATE)] = 0
    f0 = 125 + 22 * np.sin(2 * np.pi * 0.35 * t) + 8 * np.sin(2 * np.pi * 1.3 * t)
    phase = 2 * np.pi * np.cumsum(f0) / SAMPLE_RATE
    voice = np.zeros(n)
    for harmonic in range(1, 24):
        voice += (1.0 / harmonic) * np.sin(harmonic * phase)
    voice = (0.6 * _resonator(voice, 650, 7) + 0.3 * _resonator(voice, 1500, 9)
             + 0.1 * _resonator(voice, 2500, 10))
    noise = rng.normal(0, 1, n)
    fricative = np.zeros(n)
    for start in np.arange(0.7, seconds, 1.7):
        fricative[int(start * SAMPLE_RATE):int((start + 0.09) * SAMPLE_RATE)] = 1
    fricative *= np.convolve(noise, np.ones(5) / 5, "same")
    signal = voice * syllable * gate + 0.25 * fricative
    signal /= np.abs(signal).max() + 1e-9
    return (0.18 * signal).astype(np.float32)


def music(seconds=10.0, bpm=120.0, vocal=False, gain=0.18, quiet_passage=False):
    n = int(seconds * SAMPLE_RATE)
    beat = 60.0 / bpm
    signal = np.zeros(n)

    def add(start, wave):
        offset = int(start * SAMPLE_RATE)
        end = min(n, offset + len(wave))
        signal[offset:end] += wave[:end - offset]

    for start in np.arange(0, seconds, beat):
        count = int(0.18 * SAMPLE_RATE)
        tt = np.arange(count) / SAMPLE_RATE
        add(start, 0.9 * np.sin(2 * np.pi * (55 + 90 * np.exp(-tt * 30)) * tt)
            * np.exp(-tt * 14))
    for start in np.arange(beat / 2, seconds, beat):
        count = int(0.16 * SAMPLE_RATE)
        tt = np.arange(count) / SAMPLE_RATE
        add(start, 0.35 * RNG.normal(0, 1, count) * np.exp(-tt * 22))
    for start in np.arange(0, seconds, beat / 2):
        count = int(0.05 * SAMPLE_RATE)
        tt = np.arange(count) / SAMPLE_RATE
        add(start, 0.12 * RNG.normal(0, 1, count) * np.exp(-tt * 70))
    bass_notes = (55, 65.4, 49, 58.3)
    for index, start in enumerate(np.arange(0, seconds, beat)):
        count = int(beat * 0.9 * SAMPLE_RATE)
        tt = np.arange(count) / SAMPLE_RATE
        freq = bass_notes[index % len(bass_notes)]
        add(start, 0.35 * np.sin(2 * np.pi * freq * tt) * _envelope(count))
    for index, start in enumerate(np.arange(0, seconds, beat * 4)):
        root = (220, 174.6)[index % 2]
        count = int(beat * 3.6 * SAMPLE_RATE)
        tt = np.arange(count) / SAMPLE_RATE
        chord = np.zeros(count)
        for interval in (1, 1.26, 1.5):
            for harmonic in (1, 2, 3):
                chord += (0.10 / harmonic) * np.sin(
                    2 * np.pi * root * interval * harmonic * tt
                )
        add(start, chord * _envelope(count, 0.03, 0.3, 0.6, 0.4))
    if vocal:
        tt = np.arange(n) / SAMPLE_RATE
        f0 = 233 + 4 * np.sin(2 * np.pi * 5.2 * tt)
        phase = 2 * np.pi * np.cumsum(f0) / SAMPLE_RATE
        voice = np.zeros(n)
        for harmonic in range(1, 14):
            voice += (0.5 / harmonic) * np.sin(harmonic * phase)
        add(0, 0.28 * voice * (0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * tt)))
    if quiet_passage:
        signal[int(3.4 * SAMPLE_RATE):int(4.3 * SAMPLE_RATE)] *= 0.18
    signal /= np.abs(signal).max() + 1e-9
    return (gain * signal).astype(np.float32)


def chime(seconds=0.3):
    count = int(seconds * SAMPLE_RATE)
    tt = np.arange(count) / SAMPLE_RATE
    return 0.3 * np.sin(2 * np.pi * 1200 * tt) * np.exp(-tt * 8)


def run(signal, sensitivity=55, delay=3.0):
    classifier = MusicClassifier(sensitivity, delay)
    beats = []
    block = int(0.25 * SAMPLE_RATE)
    for index in range(0, len(signal), block):
        _, detected = classifier.feed(
            signal[index:index + block], 100.0 + index / SAMPLE_RATE
        )
        beats.extend(detected)
    return classifier, beats


class ClassifierTests(unittest.TestCase):
    def test_continuous_speech_never_starts_music(self):
        classifier, _ = run(speech(10))
        self.assertFalse(classifier.active)
        self.assertLess(classifier.last_report.evidence, classifier.threshold)

    def test_speech_with_notification_chime_stays_asleep(self):
        signal = speech(10, seed=3)
        note = chime()
        offset = int(2.2 * SAMPLE_RATE)
        signal[offset:offset + len(note)] += note
        classifier, _ = run(signal)
        self.assertFalse(classifier.active)

    def test_instrumental_music_starts_after_delay(self):
        classifier, beats = run(music(10))
        self.assertTrue(classifier.active)
        self.assertGreater(classifier.last_report.evidence, 0.55)
        self.assertGreaterEqual(len(beats), 2)
        self.assertTrue(95 <= classifier.last_report.tempo <= 145)

    def test_music_with_vocals_is_detected(self):
        classifier, _ = run(music(10, vocal=True))
        self.assertTrue(classifier.active)

    def test_quiet_music_is_detected(self):
        classifier, _ = run(music(10, gain=0.02))
        self.assertTrue(classifier.active)

    def test_short_quiet_passage_keeps_dancing(self):
        classifier, _ = run(music(10, quiet_passage=True))
        self.assertTrue(classifier.active)

    def test_music_then_speech_returns_to_sleep(self):
        signal = np.concatenate([music(7), speech(8, seed=9)])
        classifier, _ = run(signal)
        self.assertFalse(classifier.active)

    def test_song_change_without_gap_stays_active(self):
        signal = np.concatenate([music(7, bpm=120), music(8, bpm=96)])
        classifier, _ = run(signal)
        self.assertTrue(classifier.active)

    def test_silence_is_not_music(self):
        classifier, _ = run(np.zeros(int(5 * SAMPLE_RATE), dtype=np.float32))
        self.assertFalse(classifier.active)
        self.assertEqual(classifier.last_report.state, "silence")

    def test_sensitivity_changes_threshold_only(self):
        weak = MusicClassifier(10)
        strong = MusicClassifier(95)
        self.assertGreater(weak.threshold, strong.threshold)
        self.assertTrue(0.35 <= strong.threshold <= 0.72)

    def test_high_sensitivity_still_ignores_speech(self):
        classifier, _ = run(speech(10, seed=12), sensitivity=100, delay=2.0)
        self.assertFalse(classifier.active)


class SettingsTests(unittest.TestCase):
    def test_style_validation(self):
        self.assertEqual(valid_music_style("Hype"), "hype")
        self.assertEqual(valid_music_style("nonsense"), "auto")
        self.assertEqual(valid_music_style(None), "auto")

    def test_percent_and_delay_ranges(self):
        self.assertEqual(valid_percent(-20, 55), 0)
        self.assertEqual(valid_percent(140, 55), 100)
        self.assertEqual(valid_percent("bad", 55), 55)
        self.assertEqual(valid_music_delay(100), 30.0)
        self.assertEqual(valid_music_delay(0), 2.0)
        self.assertEqual(valid_music_delay("bad"), 6.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
