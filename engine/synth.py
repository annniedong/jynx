"""Sound sources. Everything is synthesized, so there are no sample files.

Each trigger renders a whole numpy buffer up front; the mixer then plays it
back slice by slice. Filters and effects live on the track, so they respond to
knob changes while notes are still ringing.
"""
from __future__ import annotations

import re
from functools import lru_cache

import numpy as np

SR = 44100
TAU = 2 * np.pi

NOTE_RE = re.compile(r"^([a-g])(#|s|b)?(-?\d)?$")
SEMIS = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
DRUMS = {"bd", "sd", "hh", "oh", "cp", "rim", "lt", "mt", "ht", "cb", "cr"}
WAVES = {"sawtooth", "square", "triangle", "sine", "supersaw", "bell", "pluck", "sub"}
WAVE_ALIASES = {"saw": "sawtooth", "tri": "triangle", "sin": "sine", "sqr": "square"}


def note_to_midi(tok: str) -> float | None:
    """c3, eb4, f#2 -> midi; plain numbers are already midi (Strudel's note(60))."""
    m = NOTE_RE.match(tok.lower())
    if m:
        letter, acc, octave = m.groups()
        semi = SEMIS[letter] + {"#": 1, "s": 1, "b": -1}.get(acc or "", 0)
        return 12 * (int(octave if octave is not None else 3) + 1) + semi
    try:
        return float(tok)
    except ValueError:
        return None


def wave_name(name: str) -> str | None:
    name = WAVE_ALIASES.get(name, name)
    return name if name in WAVES else None


def sound_base(name: str) -> str:
    """Strudel sample variants like bd:3 play our single bd."""
    return name.split(":")[0]


def is_sound(name: str) -> bool:
    base = sound_base(name)
    return base in DRUMS or wave_name(base) is not None


def _t(seconds: float):
    return np.arange(int(seconds * SR)) / SR


def _noise(n: int, seed: int):
    return np.random.default_rng(seed).uniform(-1, 1, n)


def _sweep(f_hi: float, f_lo: float, rate: float, seconds: float, decay: float):
    t = _t(seconds)
    f = f_lo + (f_hi - f_lo) * np.exp(-t * rate)
    return np.sin(TAU * np.cumsum(f) / SR) * np.exp(-t * decay)


@lru_cache(maxsize=None)
def drum(name: str) -> np.ndarray:
    if name == "bd":
        y = _sweep(160, 48, 28, 0.6, 5.5)
        y[:60] += _noise(60, 1) * np.linspace(0.5, 0, 60)
        return np.tanh(y * 1.6) * 0.9
    if name == "sd":
        t = _t(0.3)
        n = np.diff(_noise(len(t) + 1, 2))
        return (n * np.exp(-t * 16) * 0.45 + np.sin(TAU * 185 * t) * np.exp(-t * 28) * 0.5)
    if name in ("hh", "oh", "cr"):
        dur, decay, gain = {"hh": (0.08, 55, 0.22), "oh": (0.45, 7, 0.22), "cr": (1.6, 2.6, 0.2)}[name]
        t = _t(dur)
        n = np.diff(np.diff(_noise(len(t) + 2, 3)))
        return n * np.exp(-t * decay) * gain
    if name == "cp":
        t = _t(0.35)
        env = np.exp(-t * 20)
        for k in (0.0, 0.011, 0.022):
            env += (t >= k) * np.exp(-np.maximum(t - k, 0) * 140)
        return np.diff(_noise(len(t) + 1, 4)) * env * 0.3
    if name == "rim":
        t = _t(0.06)
        return np.sin(TAU * 1700 * t) * np.exp(-t * 90) * 0.5
    if name in ("lt", "mt", "ht"):
        base = {"lt": 90, "mt": 130, "ht": 190}[name]
        return _sweep(base * 1.6, base, 18, 0.5, 7) * 0.7
    if name == "cb":
        t = _t(0.3)
        sq = np.sign(np.sin(TAU * 540 * t)) + np.sign(np.sin(TAU * 800 * t))
        return sq * np.exp(-t * 12) * 0.12
    raise KeyError(name)


def _wave(kind: str, f, t: np.ndarray, fm: float = 0.0, fmh: float = 1.0,
          detune: float = 0.2) -> np.ndarray:
    """f is a frequency, or an array of them (vibrato)."""
    ph = np.cumsum(np.broadcast_to(f, t.shape)) / SR if np.ndim(f) else f * t
    if fm > 0:  # Strudel's .fm(index).fmh(ratio): a sine modulating the phase
        ph = ph + fm * np.sin(TAU * ph * fmh) / TAU
    if kind == "sine":
        return np.sin(TAU * ph)
    if kind == "sub":
        return np.sin(TAU * ph) * 0.8 + np.sin(TAU * ph * 0.5) * 0.5
    if kind == "square":
        return np.sign(np.sin(TAU * ph)) * 0.6
    if kind == "triangle":
        return 2 * np.abs(2 * (ph % 1) - 1) - 1
    if kind == "supersaw":
        # five saws spread over +-detune semitones (Strudel's .detune())
        return sum(2 * ((ph * 2 ** (k * detune / 12) + o) % 1) - 1 for k, o in
                   ((0, 0), (0.5, 0.3), (-0.5, 0.6), (1, 0.1), (-1, 0.8))) * 0.3
    if kind == "bell":
        return np.sin(TAU * ph + 2.5 * np.exp(-t * 5) * np.sin(TAU * ph * 3.5))
    if kind == "pluck":
        return (2 * (ph % 1) - 1) * np.exp(-t * 7)
    return 2 * (ph % 1) - 1  # sawtooth


def _note(midi, wave, dur, attack, decay, sustain, release,
          fm=0.0, fmh=1.0, vib=0.0, vibmod=0.5, detune=0.2) -> np.ndarray:
    """dur = how long the key is held (seconds). ADSR envelope. vib = vibrato rate
    (Hz), vibmod = its depth (semitones); fm / fmh = FM index / harmonic ratio."""
    f = 440.0 * 2 ** ((midi - 69) / 12)
    t = _t(dur + release)
    if vib > 0:
        f = f * 2 ** (vibmod * np.sin(TAU * vib * t) / 12)
    y = _wave(wave, f, t, fm, fmh, detune)
    a, d = max(attack, 0.002), max(decay, 0.001)
    env = np.where(t < a, t / a, np.maximum(1 - (1 - sustain) * (t - a) / d, sustain))
    held = min(int(dur * SR), len(t))
    if held < len(t):
        level = env[held - 1] if held > 0 else 0.0
        env[held:] = level * np.linspace(1, 0, len(t) - held) ** 2
    return y * env * 0.28


note = lru_cache(maxsize=256)(_note)


def held_note(midi, wave, attack, decay, sustain, release, hold=4.0):
    """A keyboard note: rendered long (uncached); the mixer cuts it on key-up."""
    return _note(midi, wave, hold, attack, decay, sustain, release)


def resample(buf: np.ndarray, speed: float) -> np.ndarray:
    """Play a buffer faster/slower (Strudel's .speed); negative plays it backwards."""
    if speed == 1 or speed == 0:
        return buf
    if speed < 0:
        buf = buf[::-1]
    pos = np.arange(0, len(buf) - 1, abs(speed))
    return np.interp(pos, np.arange(len(buf)), buf)
