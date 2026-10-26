"""Scheduler + mixer.

Timing model: the clock counts *cycles* (Strudel's unit; setcpm sets how many
per minute). Pattern changes are swapped in on the next cycle boundary so edits
always land in time; fixed effect values (gain, lpf, ...) apply immediately so
knobs feel live. Keyboard notes (from `./jynx keys`) play instantly.
"""
from __future__ import annotations

import collections
import math
import threading
import time

import numpy as np
from scipy.signal import lfilter

from . import synth
from .lang import DEFAULT_TRACK, NUM_PARAMS, TRACK_LEVEL, Program, Track

SR = synth.SR
BLOCK = 512


def _biquad(kind: str, freq: float, q: float):
    w0 = 2 * math.pi * min(max(freq, 20.0), SR * 0.45) / SR
    alpha = math.sin(w0) / (2 * max(q, 0.3))
    c = math.cos(w0)
    if kind == "lp":
        b = [(1 - c) / 2, 1 - c, (1 - c) / 2]
    else:
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
    a = [1 + alpha, -2 * c, 1 - alpha]
    return np.array(b) / a[0], np.array(a) / a[0]


class Ring:
    """Fixed delay line, processed a block at a time (delay >= block size)."""

    def __init__(self, size: int):
        self.buf = np.zeros(size)
        self.w = 0

    def read(self, delay: int, n: int):
        idx = (self.w - delay + np.arange(n)) % len(self.buf)
        return self.buf[idx]

    def write(self, x: np.ndarray):
        idx = (self.w + np.arange(len(x))) % len(self.buf)
        self.buf[idx] = x
        self.w = (self.w + len(x)) % len(self.buf)


class Reverb:
    """Small Schroeder reverb: 4 combs into 3 allpasses."""

    COMBS = (1557, 1617, 1491, 1422)
    ALLPASS = (557, 613, 701)

    def __init__(self):
        self.combs = [Ring(d) for d in self.COMBS]
        self.aps = [(Ring(d), Ring(d)) for d in self.ALLPASS]
        self.tone = _biquad("lp", 4500, 0.7)
        self.zi = np.zeros(2)

    def process(self, x: np.ndarray) -> np.ndarray:
        n = len(x)
        out = np.zeros(n)
        for ring, d in zip(self.combs, self.COMBS):
            y = x + 0.84 * ring.read(d, n)
            ring.write(y)
            out += y
        out *= 0.25
        for (xr, yr), d in zip(self.aps, self.ALLPASS):
            y = -0.5 * out + xr.read(d, n) + 0.5 * yr.read(d, n)
            xr.write(out)
            yr.write(y)
            out = y
        out, self.zi = lfilter(*self.tone, out, zi=self.zi)
        return out


class Voice:
    __slots__ = ("buf", "pos", "offset", "key")

    def __init__(self, buf, offset=0, key=None):
        self.buf, self.pos, self.offset, self.key = buf, 0, offset, key

    def release(self, seconds: float):
        """Key-up: fade out from where the note is now."""
        end = min(self.pos + max(int(seconds * SR), 64), len(self.buf))
        tail = self.buf[self.pos:end] * np.linspace(1, 0, end - self.pos) ** 2
        self.buf = np.concatenate([self.buf[:self.pos], tail])
        self.key = None


class TrackState:
    def __init__(self):
        self.voices: list[Voice] = []
        self.lp_zi = np.zeros(2)
        self.hp_zi = np.zeros(2)
        self.delay = Ring(SR * 4)
        self.level = 0.0
        self.cur: dict[str, float] = {}  # last values of patterned track-level params


def note_buffer(track: Track, value: str, cycle: int, t: float, dur_s: float):
    """Render one pattern event for a track -> (buffer, spans of patterned params used)."""
    spans = []

    def at(key):
        v, span = track.at(key, cycle, t)
        if span:
            spans.append(span)
        return v

    if track.head == "s":
        name = synth.sound_base(value)
        if name in synth.DRUMS:
            buf = synth.drum(name)
        else:
            buf = synth.note(48 + at("transpose"), synth.wave_name(name), round(dur_s, 4),
                             at("attack"), at("decay"), at("sustain"), at("release"))
    else:
        sound = at("s") or "triangle"
        base = synth.sound_base(sound)
        if base in synth.DRUMS:
            buf = synth.drum(base)
        else:
            midi = track.midi(value)
            if midi is None:
                return None, spans
            buf = synth.note(midi + at("transpose"), synth.wave_name(base) or "triangle",
                             round(dur_s, 4), at("attack"), at("decay"), at("sustain"), at("release"))
    gain = at("velocity") * (at("gain") if track.patterned("gain") else 1.0)
    if gain != 1.0:
        buf = buf * gain
    return buf, spans


class Engine:
    def __init__(self, post):
        self.post = post  # post(msg, delay_seconds) -> send to all clients
        self.lock = threading.Lock()
        self.live = Program()  # newest code: effect values come from here
        self.active = Program()  # patterns playing this cycle
        self.states: dict[str, TrackState] = {}
        self.inbox: collections.deque = collections.deque()  # keyboard voices from other threads
        self.cycle_pos = 0.0
        self.cur_cycle = -1
        self.cache: dict[tuple[str, int], list] = {}
        self.reverb = Reverb()
        self.hushed = False
        self.xruns = 0
        self.load = 0.0

    # -------------------------------------------------------------- control

    def set_program(self, prog: Program):
        with self.lock:
            self.live = prog

    def hush(self, on: bool):
        self.hushed = on

    def levels(self) -> dict[str, float]:
        return {name: st.level for name, st in list(self.states.items())}

    def track_for(self, name: str) -> Track:
        return self.live.tracks.get(name) or self.active.tracks.get(name) or DEFAULT_TRACK

    def key_down(self, track_name: str, key, midi: float | None = None, sound: str | None = None,
                 vel: float = 0.8, hold: float | None = None):
        """Called from the server thread: render now, hand the voice to the audio thread."""
        tr = self.track_for(track_name)
        if sound is None and midi is not None:
            s = tr.const("s") or "triangle"
            base = synth.sound_base(s)
            if base in synth.DRUMS:
                buf = synth.drum(base)
            else:
                wave = synth.wave_name(base) or "triangle"
                a, d, su, r = (tr.const(k) for k in ("attack", "decay", "sustain", "release"))
                midi += tr.const("transpose")
                if hold is None:
                    buf = synth.held_note(midi, wave, a, d, su, r)
                else:
                    buf = synth.note(midi, wave, round(hold, 3), a, d, su, r)
        else:
            buf = synth.drum(synth.sound_base(sound))
        self.inbox.append(("down", track_name, Voice(buf * vel, 0, key if hold is None else None)))
        return self.cycle_pos

    def key_up(self, track_name: str, key):
        self.inbox.append(("up", track_name, key))

    # -------------------------------------------------------------- audio

    def _events(self, track: Track, cycle: int):
        k = (track.name, cycle)
        ev = self.cache.get(k)
        if ev is None:
            ev = self.cache[k] = track.query(cycle)
        return ev

    def _begin_cycle(self, k: int, dac_delay: float, offset_s: float):
        self.cur_cycle = k
        with self.lock:
            self.active = self.live
        self.cache = {key: v for key, v in self.cache.items() if key[1] >= k}
        self.post({"type": "cycle", "n": k}, dac_delay + offset_s)

    def _drain_inbox(self):
        while self.inbox:
            kind, name, item = self.inbox.popleft()
            st = self.states.setdefault(name, TrackState())
            if kind == "down":
                st.voices.append(item)
            else:
                rel = self.track_for(name).const("release")
                for v in st.voices:
                    if v.key == item:
                        v.release(max(rel, 0.03))

    def callback(self, outdata, frames, t_info, status):
        t_start = time.perf_counter()
        if status.output_underflow:
            self.xruns += 1
        try:
            dac_delay = max(t_info.outputBufferDacTime - t_info.currentTime, 0.0)
        except Exception:
            dac_delay = 0.0
        self._drain_inbox()

        live = self.live
        cps = live.cps
        c0 = self.cycle_pos
        c1 = c0 + frames * cps / SR
        spc = SR / cps  # samples per cycle

        # ---- schedule
        hits = []
        for k in range(math.floor(c0), math.ceil(c1)):
            if k > self.cur_cycle:
                self._begin_cycle(k, dac_delay, max(k - c0, 0) / cps)
            if self.active.silent:
                continue
            for name, track in self.active.tracks.items():
                lt = live.tracks.get(name, track)  # newest params, current pattern
                st = None
                for e in self._events(track, k):
                    onset = k + e.start
                    if not (c0 <= onset < c1):
                        continue
                    buf, spans = note_buffer(lt, e.value, k, e.start, e.dur / cps)
                    if buf is None:
                        continue
                    st = st or self.states.setdefault(name, TrackState())
                    for key in TRACK_LEVEL:
                        if lt.patterned(key):
                            st.cur[key], span = lt.at(key, k, e.start)
                            if span:
                                spans.append(span)
                    st.voices.append(Voice(buf, min(int((onset - c0) * spc), frames - 1)))
                    dur = round(e.dur / cps, 3)
                    hits.append([e.span[0], e.span[1], e.span[2], dur])
                    hits.extend([s[0], s[1], s[2], dur] for s in spans)
        if hits:
            self.post({"type": "hits", "hits": hits}, dac_delay)

        # ---- mix
        left = np.zeros(frames)
        right = np.zeros(frames)
        send = np.zeros(frames)
        for name, st in list(self.states.items()):
            x = np.zeros(frames)
            keep = []
            for v in st.voices:
                n = min(frames - v.offset, len(v.buf) - v.pos)
                if n > 0:
                    x[v.offset:v.offset + n] += v.buf[v.pos:v.pos + n]
                    v.pos += n
                v.offset = 0
                if v.pos < len(v.buf):
                    keep.append(v)
            st.voices = keep
            in_code = name in live.tracks or name in self.active.tracks
            if not keep and not in_code and st.level < 0.01:
                del self.states[name]
                continue
            tr = self.track_for(name)

            def p(key):
                return st.cur.get(key, NUM_PARAMS[key][0]) if tr.patterned(key) else tr.const(key)

            hpf, lpf, q = p("hpf"), p("lpf"), p("lpq")
            if hpf > 20:
                x, st.hp_zi = lfilter(*_biquad("hp", hpf, q), x, zi=st.hp_zi)
            if lpf < 19000:
                x, st.lp_zi = lfilter(*_biquad("lp", lpf, q), x, zi=st.lp_zi)
            crush = p("crush")
            if crush >= 1:
                steps = 2 ** crush
                x = np.round(x * steps) / steps
            if not tr.patterned("gain"):
                x = x * tr.const("gain")

            wet = p("delay")
            d = max(int(p("delaytime") * SR), frames)
            echoed = st.delay.read(min(d, len(st.delay.buf) - 1), frames)
            st.delay.write(x * (wet > 0) + echoed * p("delayfb"))
            x = x + echoed * wet

            send += x * p("room")
            peak = float(np.max(np.abs(x))) if frames else 0.0
            st.level = max(st.level * 0.8, min(peak * 1.5, 1.0))
            pan = p("pan") * math.pi / 2  # Strudel: 0 = left, .5 = center, 1 = right
            left += x * math.cos(pan)
            right += x * math.sin(pan)

        verb = self.reverb.process(send)
        left += verb
        right += verb
        if self.hushed:
            outdata.fill(0)
        else:
            outdata[:, 0] = np.tanh(left * 0.9)
            outdata[:, 1] = np.tanh(right * 0.9)
        self.cycle_pos = c1
        self.load = 0.9 * self.load + 0.1 * (time.perf_counter() - t_start) / (frames / SR)
