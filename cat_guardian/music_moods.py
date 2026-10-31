"""Adaptive dance selection and the newer add-on dance poses.

The audio classifier reports tempo, energy, beat strength, and confidence.
This module scores a catalog of dances against that profile, keeps a dance
long enough to read, then rotates through the rest so a long track never
sits on one move. The four original moods (chill, groove, hype, minimal)
keep their poses in ``pet.py``; the newer moods are rendered here.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QPointF

TAU = math.tau

NEW_MUSIC_MOODS = frozenset({
    "music_zen",
    "music_sway",
    "music_strut",
    "music_paw",
    "music_wave",
    "music_bounce",
    "music_twirl",
    "music_hop",
    "music_headbang",
    "music_dip",
})


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


@dataclass(frozen=True)
class MusicProfile:
    tempo: float
    energy: float
    beat: float
    confidence: float
    intensity: float = 0.55


@dataclass(frozen=True)
class DanceStep:
    name: str
    tempo: float
    tempo_span: float
    energy: float
    beat: float
    energy_span: float = 0.40
    beat_span: float = 0.90


DANCE_STEPS = (
    DanceStep("music_zen", 58.0, 20.0, 0.10, 0.05),
    DanceStep("music_chill", 82.0, 34.0, 0.20, 0.25),
    DanceStep("music_sway", 74.0, 26.0, 0.30, 0.12),
    DanceStep("music_minimal", 96.0, 22.0, 0.30, 0.15),
    DanceStep("music_dip", 88.0, 22.0, 0.36, 0.35),
    DanceStep("music_strut", 96.0, 24.0, 0.42, 0.30),
    DanceStep("music_groove", 108.0, 30.0, 0.50, 0.45),
    DanceStep("music_paw", 104.0, 24.0, 0.40, 0.40),
    DanceStep("music_wave", 112.0, 26.0, 0.48, 0.30),
    DanceStep("music_bounce", 118.0, 26.0, 0.55, 0.48),
    DanceStep("music_twirl", 126.0, 28.0, 0.66, 0.50),
    DanceStep("music_hop", 134.0, 26.0, 0.72, 0.62),
    DanceStep("music_hype", 146.0, 36.0, 0.85, 0.70),
    DanceStep("music_headbang", 156.0, 34.0, 0.88, 0.72),
)

DANCE_NAMES = tuple(step.name for step in DANCE_STEPS)


class DanceBrain:
    """Choose a dance that fits the track and keeps moving through the set.

    Fitness dominates, but a fairness bonus for dances that have not played
    lately and a fatigue penalty on the current one make long tracks rotate.
    Every third switch, and any hand-over at the max dwell, is a variety pick
    that goes to the least recently used dance, so a steady track still works
    through the whole catalog instead of looping the same top few.
    A large profile change can still jump straight to a better fit.
    """

    MIN_DWELL = 9.0
    MAX_DWELL = 26.0
    EMERGENCY = 0.20
    VARIETY_EVERY = 3

    def __init__(self, rng: random.Random | None = None):
        self._rng = rng or random.Random()
        self._current: str | None = None
        self._previous: str | None = None
        self._since: float | None = None
        self._seen: dict[str, float] = {}
        self._switches = 0

    @property
    def current(self) -> str | None:
        return self._current

    def choose(self, profile: MusicProfile, now: float,
               current: str | None = None) -> tuple[str, float]:
        if current is not None and self._current is None:
            self._current = current
            self._since = None
        elif current is not None and current != self._current:
            self._current = current
            self._since = now

        scores = self._scores(profile, now)
        ordered = sorted(DANCE_NAMES, key=lambda name: scores[name], reverse=True)
        best = ordered[0]
        initialized = self._since is not None
        variety_due = (
            initialized
            and self._switches % self.VARIETY_EVERY == self.VARIETY_EVERY - 1
            and now - self._since >= self.MIN_DWELL
        )

        if self._current is None:
            switch = True
        elif not initialized:
            switch = best != self._current
        elif now - self._since >= self.MAX_DWELL:
            # A long run always hands over, preferring a neglected dance.
            best = self._least_recent(ordered)
            switch = best != self._current
        elif variety_due:
            best = self._least_recent(ordered)
            switch = best != self._current
        elif best != self._current:
            delta = scores[best] - scores.get(self._current, -1.0)
            switch = (delta >= self.EMERGENCY
                      or (delta >= 0.03 and now - self._since >= self.MIN_DWELL))
        else:
            switch = False

        if switch and best != self._current:
            self._previous = self._current
            self._current = best
            self._since = now
            self._seen[best] = now
            self._switches += 1
            return best, self._rng.uniform(9.0, 16.0)

        if self._current is not None:
            self._seen[self._current] = now
            if self._since is None:
                self._since = now
        return self._current or "music_groove", self._rng.uniform(3.5, 6.5)

    def _least_recent(self, ordered: list[str]) -> str:
        """The dance that has waited longest, breaking ties by fitness."""
        pool = [name for name in ordered if name != self._current]
        if not pool:
            return ordered[0]
        fresh = [name for name in pool if name != self._previous]
        if fresh:
            pool = fresh
        position = {name: index for index, name in enumerate(ordered)}
        return min(pool, key=lambda name: (
            self._seen.get(name, -1.0e9), position[name]
        ))

    def _scores(self, profile: MusicProfile, now: float) -> dict[str, float]:
        # The user's intensity slider nudges the perceived drive a little.
        energy = _clamp(0.82 * profile.energy + 0.18 * profile.intensity, 0.0, 1.0)
        confidence = _clamp(profile.confidence, 0.0, 1.0)
        scores: dict[str, float] = {}
        for step in DANCE_STEPS:
            tempo_fit = _clamp(
                1.0 - abs(profile.tempo - step.tempo) / step.tempo_span, 0.0, 1.0
            )
            energy_fit = _clamp(
                1.0 - abs(energy - step.energy) / step.energy_span, 0.0, 1.0
            )
            beat_fit = _clamp(
                1.0 - abs(profile.beat - step.beat) / step.beat_span, 0.0, 1.0
            )
            score = 0.50 * tempo_fit + 0.32 * energy_fit + 0.18 * beat_fit
            score *= 0.76 + 0.24 * confidence
            if confidence < 0.35:
                # A shaky classification should not launch the loudest moves.
                score -= 0.12 * max(0.0, step.energy - 0.55)
            last = self._seen.get(step.name, now - 90.0)
            score += 0.16 * _clamp((now - last) / 45.0, 0.0, 1.0)
            if step.name == self._current:
                if self._since is not None:
                    score -= min(0.26, max(0.0, now - self._since) * 0.006)
            elif step.name == self._previous:
                score -= 0.10
            scores[step.name] = score
        return scores


def apply_music_dance(v, mood: str, *, now: float, phase: float, pulse: float,
                      scale: float, beat_count: int,
                      jump_start: float | None = None) -> None:
    """Layer one of the newer dance poses onto a Visual.

    ``v`` is duck-typed on purpose: pet.Visual imports this module, so a
    direct type import would be circular.
    """
    wave = math.sin(phase * TAU)
    counter = math.sin(phase * TAU + math.pi / 2)
    scale = _clamp(scale, 0.0, 1.0)
    pulse = _clamp(pulse, 0.0, 1.0)
    v.eye_open = 1.0
    v.blush = 0.10 + 0.10 * scale

    if mood == "music_zen":
        v.eye_mode = "half"
        v.eye_open = 0.45
        v.head_tilt = 1.5 * wave
        v.lean = 0.7 * wave
        v.bob += 1.2 + 0.6 * counter
        v.paw_l = QPointF(-1, 1)
        v.paw_r = QPointF(1, 1)
        v.tail = "wrap"
        v.blush = 0.05 + 0.05 * scale
    elif mood == "music_sway":
        v.eye_mode = "half"
        v.eye_open = 0.7
        v.head_tilt = 4.2 * wave
        v.lean = 3.4 * wave
        v.bob += 1.4 + 0.9 * abs(wave)
        v.paw_l = QPointF(2.0 * wave, 0)
        v.paw_r = QPointF(2.0 * wave, 0)
        v.tail = "sway"
    elif mood == "music_strut":
        side = wave
        v.head_tilt = 5.4 * side + 1.4
        v.lean = 4.2 * side
        v.bob += 1.2 + 1.4 * abs(side)
        v.paw_l = QPointF(0, -8.5 * max(0.0, side) * (0.55 + 0.45 * scale))
        v.paw_r = QPointF(0, -8.5 * max(0.0, -side) * (0.55 + 0.45 * scale))
        v.tail = "up"
        v.blush = 0.16
    elif mood == "music_paw":
        tap = math.sin(phase * TAU * 2.0)
        v.eye_mode = "happy"
        v.head_tilt = 2.6 * wave
        v.bob += 1.0 + 0.6 * abs(counter)
        v.paw_l = QPointF(0, -9.0 * max(0.0, tap) * (0.55 + 0.45 * scale))
        v.paw_r = QPointF(0, -9.0 * max(0.0, -tap) * (0.55 + 0.45 * scale))
        v.tail = "sway"
        v.blush = 0.28
    elif mood == "music_wave":
        v.eye_mode = "happy"
        v.head_tilt = 3.2 * wave + 1.0
        v.lean = 1.6
        v.bob += 1.2 + 1.1 * abs(counter)
        v.paw_l = QPointF(-1, 1)
        v.paw_r = QPointF(5.0 + 2.0 * math.sin(now * 8.0),
                          -17.0 - 5.0 * wave * (0.6 + 0.4 * scale))
        v.tail = "sway"
        v.blush = 0.45
    elif mood == "music_bounce":
        hop = abs(math.sin(phase * TAU))
        v.head_tilt = 2.2 * wave
        v.bob += 1.6 + 5.5 * hop * (0.5 + 0.5 * scale)
        v.paw_l = QPointF(0, -3.0)
        v.paw_r = QPointF(0, -3.0)
        v.tail = "up"
        v.eye_open = 1.03
        squash = 0.03 * pulse
        v.sx *= 1.0 + squash
        v.sy *= 1.0 - squash
    elif mood == "music_twirl":
        v.head_tilt = 6.0 * wave
        v.lean = 5.2 * counter
        v.bob += 1.4 + 2.0 * abs(counter)
        v.paw_l = QPointF(-3.0 * wave, -2.0)
        v.paw_r = QPointF(3.0 * wave, -2.0)
        v.tail = "stream"
        v.eye_open = 1.06
        v.blush = 0.22
    elif mood == "music_hop":
        hop = max(0.0, math.sin(phase * TAU * 2.0))
        v.head_tilt = 3.0 * wave
        v.bob -= 10.0 * hop * (0.55 + 0.45 * scale)
        v.paw_l = QPointF(-2.0, -4.5 * hop)
        v.paw_r = QPointF(2.0, -4.5 * hop)
        v.tail = "up"
        v.eye_open = 1.08
        v.blush = 0.24
    elif mood == "music_headbang":
        nod = math.sin(phase * TAU)
        v.head_tilt = 10.0 * nod
        v.drop = 3.2 * max(0.0, nod)
        v.lean = 2.2 * counter
        v.bob += 1.4 + 1.8 * abs(nod) * (0.6 + 0.4 * scale)
        v.paw_l = QPointF(0, -4.0)
        v.paw_r = QPointF(0, -4.0)
        v.tail = "up"
        v.eye_open = 1.10
        v.blush = 0.18
    elif mood == "music_dip":
        dip = 0.5 + 0.5 * math.sin(phase * TAU - math.pi / 2.0)
        v.eye_mode = "half"
        v.eye_open = 0.8
        v.head_tilt = 4.4 * counter
        v.bob += 2.0 + 4.2 * dip * (0.5 + 0.5 * scale)
        v.paw_l = QPointF(-1.0, -1.8 * dip)
        v.paw_r = QPointF(1.0, -1.8 * dip)
        v.tail = "wrap"
        v.blush = 0.12
        v.sx *= 1.0 + 0.02 * dip
        v.sy *= 1.0 - 0.02 * dip
    else:
        return

    if jump_start is not None:
        u = (now - jump_start) / 0.55
        if 0.0 <= u < 1.0:
            v.bob -= (16.0 + 8.0 * scale) * math.sin(math.pi * u)
