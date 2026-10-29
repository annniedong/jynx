"""Scheduler + mixer.

Timing model: the clock counts *cycles* (Strudel's unit; setcpm sets how many
per minute). Pattern changes are swapped in on the next cycle boundary so edits
always land in time; fixed effect values (gain, lpf, ...) apply immediately so
knobs feel live. Keyboard notes (from `./jynx keys`) play instantly.
"""
from __future__ import annotations

import collections
from functools import lru_cache
import math
import threading
import time

import numpy as np
from scipy.signal import lfilter

from . import pat as P
from . import synth
from .lang import DEFAULT_TRACK, NUM_PARAMS, TRACK_LEVEL, Program, Track, clamp

SR = synth.SR
BLOCK = 512


def _biquad(kind: str, freq: float, q: float):
    w0 = 2 * math.pi * min(max(freq, 20.0), SR * 0.45) / SR
    alpha = math.sin(w0) / (2 * max(q, 0.3))
    c = math.cos(w0)
    if kind == "lp":
        b = [(1 - c) / 2, 1 - c, (1 - c) / 2]
    elif kind == "bp":  # 0 dB at the center
        b = [alpha, 0.0, -alpha]
    else:
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
    a = [1 + alpha, -2 * c, 1 - alpha]
    return np.array(b) / a[0], np.array(a) / a[0]


@lru_cache(maxsize=512)
def filtered_note(note_args: tuple, base: float, env: float, q: float,
                  att: float, dec: float, sus: float, rel: float) -> np.ndarray:
    """A note through its own low-pass whose cutoff follows an ADSR: from base up to
    base * 2**env and back down to the sustain level, like Strudel's lpenv. The
    cutoff moves every 128 samples, on a log scale so the sweep sounds even."""
    buf = synth.note(*note_args)
    hold = note_args[2]
    lo = min(max(base, 20.0), 20000.0)
    hi = min(max(base * 2 ** abs(env), 20.0), 20000.0)
    if env < 0:
        lo, hi = hi, lo
    out = np.empty_like(buf)
    zi = np.zeros(2)
    a, d = max(att, 0.001), max(dec, 0.001)
    level = 0.0
    for i in range(0, len(buf), 128):
        t = (i + 64) / SR
        if t < hold:
            level = t / a if t < a else max(1 - (1 - sus) * (t - a) / d, sus)
            e = level
        else:
            e = level * max(1 - (t - hold) / max(rel, 0.001), 0.0)
        f = lo * (hi / lo) ** e
        out[i:i + 128], zi = lfilter(*_biquad("lp", f, q), buf[i:i + 128], zi=zi)
    return out


def _parts(v) -> list[str]:
    """ "3:.2:diode" -> ["3", ".2", "diode"] """
    return str(v).split(":") if v is not None else []


def _floats(v, default: float) -> list[float]:
    out = []
    for x in _parts(v):
        try:
            out.append(float(x))
        except ValueError:
            pass
    return out or [default]


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


# formants (Hz) and their levels for .vowel("a e i o u")
FORMANTS = {
    "a": ((800, 1150, 2900), (1.0, 0.5, 0.25)),
    "e": ((400, 1700, 2600), (1.0, 0.4, 0.25)),
    "i": ((270, 2140, 2950), (1.0, 0.3, 0.2)),
    "o": ((450, 800, 2830), (1.0, 0.6, 0.15)),
    "u": ((325, 700, 2530), (1.0, 0.4, 0.1)),
}


class Voice:
    __slots__ = ("buf", "pos", "offset", "key", "cut")

    def __init__(self, buf, offset=0, key=None, cut=0):
        self.buf, self.pos, self.offset, self.key, self.cut = buf, 0, offset, key, cut

    def release(self, seconds: float):
        """Key-up: fade out from where the note is now."""
        end = min(self.pos + max(int(seconds * SR), 64), len(self.buf))
        tail = self.buf[self.pos:end] * np.linspace(1, 0, end - self.pos) ** 2
        self.buf = np.concatenate([self.buf[:self.pos], tail])
        self.key = None


class Bus:
    """One effects chain. A track has a main bus; jux / every(.., x => x.lpf(..))
    make extra buses whose settings override the track's."""

    def __init__(self, over: dict):
        self.over = over
        self.voices: list[Voice] = []
        self.cur: dict[str, object] = {}  # latest values of patterned track settings
        self.lp_zi = np.zeros(2)
        self.hp_zi = np.zeros(2)
        self.bp_zi = np.zeros(2)
        self.vowel_zi = [np.zeros(2) for _ in range(3)]
        self.ph_zi = [np.zeros(1) for _ in range(4)]
        self.ph_phase = 0.0
        self.delay = Ring(SR * 4)
        self.idle = 0  # blocks since this bus last made sound


class TrackState:
    def __init__(self):
        self.buses: dict[tuple, Bus] = {}
        self.level = 0.0

    def bus(self, key: tuple) -> Bus:
        b = self.buses.get(key)
        if b is None:
            b = self.buses[key] = Bus(dict(key))
        return b

    @property
    def voices(self):
        return [v for b in self.buses.values() for v in b.voices]


def note_buffer(track: Track, h, cycle_pos: float, dur_s: float):
    """Render one event for a track -> buffer (None if it can't sound)."""
    ctl = h.ctl

    def at(key):
        return clamp(key, ctl[key]) if key in ctl else track.const(key)

    default = h.value if track.head == "s" else "triangle"
    base = synth.sound_base(str(ctl.get("s") or track.const("s") or default))
    speed = at("speed")
    dur_s *= at("clip")
    if base in synth.DRUMS:
        buf = synth.drum(base)
        if speed != 1:
            buf = synth.resample(buf, speed)
    else:
        wave = synth.wave_name(base)
        if wave is None:
            return None
        add = float(ctl.get("add", 0.0))
        if track.head == "s":  # s("sawtooth").note("c3 e3"), or a plain c3
            if "note" in ctl:
                midi = track.midi(ctl["note"], add, head="note", scale=ctl.get("scale"))
            elif "n" in ctl:
                midi = track.midi(ctl["n"], add, head="n", scale=ctl.get("scale"))
            else:
                midi = 48 + add
        else:
            midi = track.midi(h.value, add, scale=ctl.get("scale"))
        if midi is None:
            return None
        if speed > 0 and speed != 1:
            midi += 12 * math.log2(speed)
        fe = [at(k) for k in ("fmattack", "fmdecay", "fmsustain", "fmrelease")]
        fmenv = None
        if any(v >= 0 for v in fe) or at("fmenv"):  # like Strudel: decay set, sustain unset -> 0
            fa, fd, fs, fr = fe
            fmenv = (fa if fa >= 0 else 0.001, fd if fd >= 0 else 0.1,
                     fs if fs >= 0 else (0.0 if fd >= 0 else 1.0), fr if fr >= 0 else 0.1,
                     str(at("fmenv") or "lin").lower())
        args = (midi + at("transpose"), wave, round(dur_s, 4),
                at("attack"), at("decay"), at("sustain"), at("release"),
                at("fm"), at("fmh"), at("vib"), at("vibmod"), round(at("detune"), 2), fmenv)
        env = at("lpenv")
        if env:  # the note gets its own enveloped filter; the track's lpf is skipped
            base = clamp("lpf", ctl["lpf"]) if "lpf" in ctl else track.value("lpf", cycle_pos)
            buf = filtered_note(args, round(base, 1), env, at("lpq"), at("lpattack"),
                                at("lpdecay"), at("lpsustain"), at("lprelease"))
        else:
            buf = synth.note(*args)
    gain = at("velocity") * float(ctl.get("amp", 1.0)) * (clamp("gain", ctl["gain"]) if "gain" in ctl else 1.0)
    if gain != 1.0:
        buf = buf * gain
    return buf


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
        self.samples = 0  # samples played so far
        self.ducks: dict[int, list] = {}  # orbit -> [(start sample, depth, attack samples)]

    # -------------------------------------------------------------- control

    def set_program(self, prog: Program):
        self.warm(prog)
        with self.lock:
            self.live = prog

    def warm(self, prog: Program, budget: float = 0.25):
        """Render the next cycles' notes now, on the editor's thread, so the audio thread
        finds them cached instead of building every new note at once on the downbeat."""
        start = time.perf_counter()
        k0 = math.floor(self.cycle_pos)
        for k in (k0, k0 + 1, k0 + 2):
            for track in prog.tracks.values():
                try:
                    for h in track.query(k):
                        note_buffer(track, h, k + h.start, h.dur / prog.cps)
                except Exception:
                    return  # the audio thread will report it when it gets there
                if time.perf_counter() - start > budget:
                    return

    def hush(self, on: bool):
        self.hushed = on

    def levels(self) -> dict[str, float]:
        return {name: st.level for name, st in list(self.states.items())}

    def track_for(self, name: str) -> Track:
        return self.live.tracks.get(name) or self.active.tracks.get(name) or DEFAULT_TRACK

    def key_scale(self, tr: Track):
        """The scale the keyboard plays in: the track's .scale(), or the one its pattern
        is in right now (for a patterned scale like "<C:major A:minor>")."""
        if tr.scale:
            return tr.scale
        try:
            for h in P.active(tr.pat, self.cycle_pos):
                if "scale" in h.ctl:
                    return h.ctl["scale"]
        except Exception:
            pass
        return None

    def key_note(self, tr: Track, n: int, octave: int):
        """Keyboard number (a = 0, w = 1, s = 2, ...) -> (midi, label for a captured pattern).
        With a scale it's a scale step, like n("0 1 2").scale(...); z / x move by an octave
        of the scale. Without one it's chromatic from C."""
        scale = self.key_scale(tr)
        if scale:
            deg = n + (octave - 4) * len(scale[1])
            return tr.midi(deg, head="n", scale=scale), str(deg)
        return 12 * (octave + 1) + n, None

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
                st.bus(()).voices.append(item)
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
                lt = live.tracks.get(name, track)  # newest settings, current pattern
                st = None
                for h in self._events(track, k):
                    onset = k + h.start
                    if not (c0 <= onset < c1):
                        continue
                    buf = note_buffer(lt, h, onset, h.dur / cps)
                    if buf is None:
                        continue
                    st = st or self.states.setdefault(name, TrackState())
                    bus = st.bus(h.bus)
                    for key in TRACK_LEVEL:
                        if key in h.ctl:
                            bus.cur[key] = h.ctl[key]
                    cut = int(clamp("cut", h.ctl["cut"]) if "cut" in h.ctl else lt.const("cut"))
                    if cut:
                        for other in self.states.values():
                            for v in other.voices:
                                if v.cut == cut and v.pos < len(v.buf):
                                    v.release(0.01)
                    offset = min(int((onset - c0) * spc), frames - 1)
                    bus.voices.append(Voice(buf, offset, cut=cut))
                    duck = h.ctl.get("duckorbit", lt.const("duckorbit"))
                    if duck is not None:
                        self._duck(duck, h.ctl.get("duckattack", lt.const("duckattack")),
                                   h.ctl.get("duckdepth", lt.const("duckdepth")), self.samples + offset)
                    dur = round(h.dur / cps, 3)
                    if h.span:
                        hits.append([h.span[0], h.span[1], h.span[2], dur])
                    hits.extend([sp[0], sp[1], sp[2], dur] for sp in h.spans)
        if hits:
            self.post({"type": "hits", "hits": hits}, dac_delay)

        # ---- mix
        duck_gain = self._duck_gains(frames)
        left = np.zeros(frames)
        right = np.zeros(frames)
        send = np.zeros(frames)
        for name, st in list(self.states.items()):
            tr = self.track_for(name)
            in_code = name in live.tracks or name in self.active.tracks
            peak = 0.0
            for bkey, bus in list(st.buses.items()):
                x = np.zeros(frames)
                keep = []
                for v in bus.voices:
                    n = min(frames - v.offset, len(v.buf) - v.pos)
                    if n > 0:
                        x[v.offset:v.offset + n] += v.buf[v.pos:v.pos + n]
                        v.pos += n
                    v.offset = 0
                    if v.pos < len(v.buf):
                        keep.append(v)
                bus.voices = keep
                bus.idle = 0 if keep else bus.idle + 1
                if bkey and bus.idle > SR * 8 // BLOCK:  # extra buses go once their tails die
                    del st.buses[bkey]
                    continue
                x = self._effects(tr, bus, x, c0, frames, left, right, send, duck_gain)
                peak = max(peak, float(np.max(np.abs(x))) if frames else 0.0)
            st.level = max(st.level * 0.8, min(peak * 1.5, 1.0))
            if not st.voices and not in_code and st.level < 0.01:
                del self.states[name]

        verb = self.reverb.process(send)
        left += verb
        right += verb
        if self.hushed:
            outdata.fill(0)
        else:
            outdata[:, 0] = np.tanh(left * 0.9)
            outdata[:, 1] = np.tanh(right * 0.9)
        self.cycle_pos = c1
        self.samples += frames
        self.load = 0.9 * self.load + 0.1 * (time.perf_counter() - t_start) / (frames / SR)

    def _duck(self, orbits, attack, depth, start: int):
        """A hit from a pattern with .duck("4:5:6"): dip those orbits, then let them
        come back up over duckattack seconds. Lists ("2:1") go one per orbit."""
        atts = _floats(attack, 0.1)
        depths = _floats(depth, 1.0)
        for i, o in enumerate(_floats(orbits, 1.0)):
            d = min(max(depths[i % len(depths)], 0.0), 1.0)
            a = max(int(atts[i % len(atts)] * SR), 1)
            trig = self.ducks.setdefault(int(o), [])
            trig.append((start, d, a))
            if len(trig) > 8:
                del trig[0]

    def _duck_gains(self, frames: int) -> dict:
        """orbit -> gain for this block (1 = untouched)."""
        out = {}
        t = self.samples + np.arange(frames)
        for orbit, trig in list(self.ducks.items()):
            trig[:] = [x for x in trig if t[0] - x[0] < x[2]]  # drop the ones that recovered
            if not trig:
                del self.ducks[orbit]
                continue
            g = np.ones(frames)
            for start, depth, att in trig:
                ramp = np.clip((t - start) / att, 0.0, 1.0)
                g = np.minimum(g, np.where(t >= start, 1 - depth * (1 - ramp), 1.0))
            out[orbit] = g
        return out

    def _effects(self, tr: Track, bus: Bus, x, cycle_pos, frames, left, right, send, duck=None):
        """The track's effect chain on one bus; adds the result to the outputs."""
        def raw(key):
            if key in bus.over:
                return bus.over[key]
            if key in bus.cur:
                return bus.cur[key]
            p = tr.params.get(key)
            return p.const if p is not None and p.sig is None else None

        def p(key):
            if key in bus.over:
                return clamp(key, bus.over[key])
            if key in bus.cur:
                return clamp(key, bus.cur[key])
            return tr.value(key, cycle_pos)

        coarse = int(p("coarse"))
        if coarse > 1:
            x = np.repeat(x[::coarse], coarse)[:frames]
        crush = p("crush")
        if crush >= 1:
            steps = 2 ** crush
            x = np.round(x * steps) / steps
        shape = p("shape")
        if shape > 0:
            k = 2 * shape / (1 - shape)
            x = (1 + k) * x / (1 + k * np.abs(x))
        dist = p("distort")
        if dist > 0:  # .distort("amount:postgain:type"), like Strudel
            parts = _parts(raw("distort"))
            kind = parts[2].lower() if len(parts) > 2 else ""
            k = 2 ** (dist * 1.5) - 1
            if kind == "diode":  # pushes the top half, lets less of the bottom through
                x = np.where(x > 0, (1 + k) * x / (1 + k * x), 0.4 * np.tanh(x * (1 + k) * 0.5))
            elif kind == "hard":
                x = np.clip(x * (1 + k), -1, 1)
            elif kind in ("fold", "sinefold"):
                x = np.sin(x * (1 + dist) * math.pi / 2)
            elif kind == "cubic":
                y = np.clip(x * (1 + k * 0.3), -1.5, 1.5)
                x = y - y ** 3 / 6.75
            else:
                x = (1 + k) * x / (1 + k * np.abs(x))
            x = x / (1 + dist * 0.3)
            if len(parts) > 1:
                try:
                    x = x * min(max(float(parts[1]), 0.0), 4.0)
                except ValueError:
                    pass
        hpf, lpf, q = p("hpf"), p("lpf"), p("lpq")
        if hpf > 20:
            x, bus.hp_zi = lfilter(*_biquad("hp", hpf, q), x, zi=bus.hp_zi)
        if lpf < 19000 and not tr.const("lpenv"):  # with lpenv each note has its own filter
            x, bus.lp_zi = lfilter(*_biquad("lp", lpf, q), x, zi=bus.lp_zi)
        bandf = p("bandf")
        if bandf > 20:
            x, bus.bp_zi = lfilter(*_biquad("bp", bandf, p("bandq")), x, zi=bus.bp_zi)
        vowel = p("vowel")
        if vowel and str(vowel).lower() in FORMANTS:
            freqs, amps = FORMANTS[str(vowel).lower()]
            y = np.zeros(frames)
            for i, (f, a) in enumerate(zip(freqs, amps)):
                out, bus.vowel_zi[i] = lfilter(*_biquad("bp", f, 8), x, zi=bus.vowel_zi[i])
                y += out * a
            x = y * 1.6
        rate = p("phaser")
        if rate > 0:
            bus.ph_phase = (bus.ph_phase + 2 * math.pi * rate * frames / SR) % (2 * math.pi)
            depth, center, sweep = p("phaserdepth"), p("phasercenter"), p("phasersweep")
            f = min(max(center + sweep * depth * 0.5 * math.sin(bus.ph_phase), 40.0), SR * 0.45)
            tn = math.tan(math.pi * f / SR)
            a = (tn - 1) / (tn + 1)
            y = x
            for i in range(4):
                y, bus.ph_zi[i] = lfilter([a, 1.0], [1.0, a], y, zi=bus.ph_zi[i])
            x = (x + y) * 0.5
        if tr.const("gain") != 1.0:
            x = x * tr.const("gain")
        post = p("postgain")
        if post != 1.0:
            x = x * post

        wet = p("delay")
        d = max(int(p("delaytime") * SR), frames)
        echoed = bus.delay.read(min(d, len(bus.delay.buf) - 1), frames)
        bus.delay.write(x * (wet > 0) + echoed * p("delayfb"))
        x = x + echoed * wet

        if duck:  # .o("2:3:4") plays on several orbits: any of them ducking dips it
            gains = [duck[int(o)] for o in _floats(raw("orbit"), 1.0) if int(o) in duck]
            if gains:
                x = x * (gains[0] if len(gains) == 1 else np.minimum.reduce(gains))
        send += x * p("room")
        pan = p("pan") * math.pi / 2  # Strudel: 0 = left, .5 = center, 1 = right
        left += x * math.cos(pan)
        right += x * math.sin(pan)
        return x
