"""Pattern functions: Strudel's transformations (fast, off, jux, echo, every, ...).

A Pat answers one question: which events start between cycle b and cycle e?
Every transformation wraps that question in another one, the same way
Strudel's patterns are functions of time. Events (Haps) carry absolute
start times in cycles, the source text they came from, and the controls set
on them along the way (gain, pan, add, ...).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace

from . import pattern

EPS = 1e-9


@dataclass(slots=True)
class Hap:
    start: float  # cycles, absolute
    dur: float  # cycles
    value: object  # a word from mini-notation, or a number from a signal
    span: tuple | None  # (line, col0, col1) of the source word
    ctl: dict  # per-event controls: gain, pan, add, note, ...
    bus: tuple = ()  # track effect overrides, e.g. (("pan", 0.0),) from jux
    spans: tuple = ()  # source text of patterned params, for highlighting


class Pat:
    __slots__ = ("q",)

    def __init__(self, q):
        self.q = q  # q(b, e) -> haps with b <= start < e


def within(haps, b, e):
    return [h for h in haps if b - EPS <= h.start < e - EPS]


def cycles(b, e):
    """The whole cycles touched by [b, e), clipped: (k, from, to)."""
    for k in range(math.floor(b), math.ceil(e)):
        s0, s1 = max(b, k), min(e, k + 1)
        if s1 > s0:
            yield k, s0, s1


def rand(t: float, seed=0) -> float:
    return random.Random(f"{t:.6f}:{seed}").random()


def truthy(v) -> bool:
    return str(v).lower() not in ("0", "f", "false", "0.0")


SILENCE = Pat(lambda b, e: [])


def from_node(node) -> Pat:
    """A mini-notation pattern."""
    cache: dict[int, list] = {}

    def q(b, e):
        out = []
        for k in range(math.floor(b), math.ceil(e)):
            evs = cache.get(k)
            if evs is None:
                if len(cache) > 64:
                    cache.clear()
                evs = cache[k] = pattern.query(node, k)
            for ev in evs:
                s = k + ev.start
                if b - EPS <= s < e - EPS:
                    out.append(Hap(s, ev.dur, ev.value, ev.span, {}))
        return out
    return Pat(q)


def steps(bits, legato=False) -> Pat:
    """True/false steps spread over each cycle (euclid, segment)."""
    n = len(bits)
    on = [i for i, bit in enumerate(bits) if bit]

    def q(b, e):
        out = []
        for k, s0, s1 in cycles(b, e):
            for j, i in enumerate(on):
                s = k + i / n
                if s0 - EPS <= s < s1 - EPS:
                    nxt = on[j + 1] if j + 1 < len(on) else on[0] + n
                    out.append(Hap(s, (nxt - i) / n if legato else 1 / n, "x", None, {}))
        return out
    return Pat(q)


# ---------------------------------------------------------------- signals


class Sig:
    """A continuous value over time (sine, saw, perlin, ...): f(cycles) -> number."""

    def __init__(self, f):
        self.f = f

    def __call__(self, t: float) -> float:
        return self.f(t)


def _perlin(t):
    i = math.floor(t)
    a, b = rand(i, "perlin"), rand(i + 1, "perlin")
    x = t - i
    x = x * x * (3 - 2 * x)
    return a + (b - a) * x


SIGNALS = {
    "sine": lambda t: (math.sin(2 * math.pi * t) + 1) / 2,
    "cosine": lambda t: (math.cos(2 * math.pi * t) + 1) / 2,
    "saw": lambda t: t % 1,
    "isaw": lambda t: 1 - t % 1,
    "tri": lambda t: 2 * (t % 1) if t % 1 < 0.5 else 2 - 2 * (t % 1),
    "square": lambda t: 1.0 if t % 1 >= 0.5 else 0.0,
    "rand": lambda t: rand(t, "rand"),
    "perlin": _perlin,
}


def segment_sig(sig: Sig, n: float) -> Pat:
    def q(b, e):
        out = []
        start = math.floor(b * n)
        i = start
        while i / n < e - EPS:
            s = i / n
            if s >= b - EPS:
                out.append(Hap(s, 1 / n, sig(s), None, {}))
            i += 1
        return out
    return Pat(q)


# ---------------------------------------------------------------- helpers


def num(v) -> float:
    return float(str(v).split(":")[0])


def active(pat: Pat, t: float):
    """Haps sounding at time t (started at or before t, not yet ended)."""
    return [h for h in pat.q(math.floor(t) - 1, t + 2 * EPS)
            if h.start <= t + EPS < h.start + h.dur]


def sample(arg, t: float):
    """Value of a number / pattern / signal argument at time t -> (value, span) or (None, None)."""
    if isinstance(arg, Pat):
        hs = active(arg, t)
        return (hs[0].value, hs[0].span) if hs else (None, None)
    if isinstance(arg, Sig):
        return arg(t), None
    return arg, None


def run(n: float) -> Pat:
    """run(4) = "0 1 2 3": n steps per cycle counting up from 0."""
    n = max(int(n), 1)
    return Pat(lambda b, e: [Hap(k + i / n, 1 / n, i, None, {})
                             for k, s0, s1 in cycles(b, e) for i in range(n)
                             if s0 - EPS <= k + i / n < s1 - EPS])


SIG_STEPS = 16  # a signal steering a pattern function is read 16 times a cycle


def patternify(arg, make):
    """Run make(value) for a plain value, or piecewise for a patterned one ("<1 2>")
    or a signal (sine.range(1, 4)), like Strudel's patterned arguments."""
    if isinstance(arg, Sig):
        sig = arg
        arg = Pat(lambda b, e: [replace(h, value=round(h.value, 3))
                                for h in segment_sig(sig, SIG_STEPS).q(b, e)])
    if not isinstance(arg, Pat):
        return make(arg)
    made: dict = {}

    def q(b, e):
        out = []
        for a in arg.q(math.floor(b) - 1, e):
            s0, s1 = max(b, a.start), min(e, a.start + a.dur)
            if s1 > s0 + EPS:
                v = num(a.value)
                p = made.get(v)
                if p is None:
                    if len(made) > 512:  # random signals never repeat
                        made.clear()
                    p = made[v] = make(v)
                out += p.q(s0, s1)
        return out
    return Pat(q)


def fmap(pat: Pat, f) -> Pat:
    return Pat(lambda b, e: [f(h) for h in pat.q(b, e)])


def keep(pat: Pat, test) -> Pat:
    return Pat(lambda b, e: [h for h in pat.q(b, e) if test(h)])


def per_cycle(choose) -> Pat:
    """choose(k) -> the pattern to play during cycle k."""
    def q(b, e):
        out = []
        for k, s0, s1 in cycles(b, e):
            out += choose(k).q(s0, s1)
        return out
    return Pat(q)


# ---------------------------------------------------------------- time


def stack(*pats) -> Pat:
    return Pat(lambda b, e: [h for p in pats for h in p.q(b, e)])


def fast(pat: Pat, f: float) -> Pat:
    if f <= 0:
        return SILENCE
    if f == 1:
        return pat
    return Pat(lambda b, e: [replace(h, start=h.start / f, dur=h.dur / f) for h in pat.q(b * f, e * f)])


def slow(pat: Pat, f: float) -> Pat:
    return fast(pat, 1 / f) if f > 0 else SILENCE


def late(pat: Pat, t: float) -> Pat:
    if t == 0:
        return pat
    return Pat(lambda b, e: [replace(h, start=h.start + t) for h in pat.q(b - t, e - t)])


def rev(pat: Pat) -> Pat:
    def q(b, e):
        out = []
        for k, s0, s1 in cycles(b, e):
            for h in pat.q(k, k + 1):
                rel = h.start - k
                d = min(h.dur, 1 - rel)
                s = k + 1 - rel - d
                if s0 - EPS <= s < s1 - EPS:
                    out.append(replace(h, start=s, dur=d))
        return out
    return Pat(q)


def ply(pat: Pat, n: float) -> Pat:
    n = max(int(n), 1)
    if n == 1:
        return pat
    return Pat(lambda b, e: within([replace(h, start=h.start + i * h.dur / n, dur=h.dur / n)
                                    for h in pat.q(math.floor(b) - 1, e) for i in range(n)], b, e))


def iter_(pat: Pat, n: float, back=False) -> Pat:
    n = max(int(n), 1)
    return per_cycle(lambda k: late(pat, (1 if back else -1) * (k % n) / n))


def palindrome(pat: Pat) -> Pat:
    r = rev(pat)
    return per_cycle(lambda k: r if k % 2 else pat)


def brak(pat: Pat) -> Pat:
    def q(b, e):
        out = []
        for k, s0, s1 in cycles(b, e):
            if k % 2 == 0:
                out += pat.q(s0, s1)
            else:
                out += within([replace(h, start=k + 0.25 + (h.start - k) / 2, dur=h.dur / 2)
                               for h in pat.q(k, k + 1)], s0, s1)
        return out
    return Pat(q)


def linger(pat: Pat, frac: float) -> Pat:
    if not 0 < frac < 1:
        return pat

    def q(b, e):
        out = []
        for k, s0, s1 in cycles(b, e):
            first = pat.q(k, k + frac)
            for j in range(math.ceil(1 / frac)):
                out += within([replace(h, start=h.start + j * frac) for h in first
                               if h.start + j * frac < k + 1 - EPS], s0, s1)
        return out
    return Pat(q)


def zoom(pat: Pat, s: float, e_: float) -> Pat:
    w = e_ - s
    if w <= 0:
        return SILENCE

    def q(b, e):
        out = []
        for k, s0, s1 in cycles(b, e):
            out += within([replace(h, start=k + (h.start - k - s) / w, dur=h.dur / w)
                           for h in pat.q(k + s, k + e_)], s0, s1)
        return out
    return Pat(q)


def swing_by(pat: Pat, x: float, n: float) -> Pat:
    shift = x / n

    def move(h):
        pos = (h.start * n) % 1
        return replace(h, start=h.start + shift) if pos >= 0.5 - EPS else h
    return Pat(lambda b, e: within([move(h) for h in pat.q(b - shift, e)], b, e))


def press_by(pat: Pat, r: float) -> Pat:
    return Pat(lambda b, e: within([replace(h, start=h.start + h.dur * r, dur=h.dur * (1 - r))
                                    for h in pat.q(math.floor(b) - 1, e)], b, e))


def inside(pat: Pat, n: float, fn) -> Pat:
    return fast(fn(slow(pat, n)), n)


# ---------------------------------------------------------------- structure


def struct(pat: Pat, shape: Pat, legato=False) -> Pat:
    """Play pat's values with shape's rhythm."""
    def q(b, e):
        out = []
        for m in shape.q(b, e):
            if truthy(m.value):
                for h in active(pat, m.start):
                    out.append(replace(h, start=m.start, dur=m.dur))
        return out
    return Pat(q)


def mask(pat: Pat, m: Pat) -> Pat:
    return keep(pat, lambda h: any(truthy(x.value) for x in active(m, h.start)))


def euclid(pat: Pat, k: float, n: float, rot: float = 0, legato=False) -> Pat:
    n = max(int(n), 1)
    bits = pattern.bjorklund(int(k), n)
    r = int(rot) % n
    bits = bits[r:] + bits[:r]
    return struct(pat, steps(bits, legato))


def segment(pat: Pat, n: float) -> Pat:
    return struct(pat, steps([1] * max(int(n), 1)))


def arpeggiate(pat: Pat) -> Pat:
    def q(b, e):
        groups: dict = {}
        for h in pat.q(math.floor(b) - 1, e):
            groups.setdefault((round(h.start, 6), round(h.dur, 6)), []).append(h)
        out = []
        for hs in groups.values():
            m = len(hs)
            out += [replace(h, start=h.start + i * h.dur / m, dur=h.dur / m) for i, h in enumerate(hs)]
        return within(out, b, e)
    return Pat(q)


def arp(pat: Pat, idx: Pat) -> Pat:
    """Pick notes out of stacked chords by index: .arp("0 2 1 2")."""
    def q(b, e):
        out = []
        for m in idx.q(b, e):
            chord = active(pat, m.start)
            if chord:
                h = chord[int(num(m.value)) % len(chord)]
                out.append(replace(h, start=m.start, dur=m.dur))
        return out
    return Pat(q)


# ---------------------------------------------------------------- randomness


def degrade_by(pat: Pat, p: float, seed) -> Pat:
    return keep(pat, lambda h: rand(h.start, seed) >= p)


def sometimes_by(pat: Pat, p: float, fn, seed) -> Pat:
    kept = keep(pat, lambda h: rand(h.start, seed) >= p)
    changed = fn(keep(pat, lambda h: rand(h.start, seed) < p))
    return stack(kept, changed)


def some_cycles_by(pat: Pat, p: float, fn, seed) -> Pat:
    f = fn(pat)
    return per_cycle(lambda k: f if rand(k, seed) < p else pat)


# ---------------------------------------------------------------- layering


def every(pat: Pat, n: float, fn, last=False) -> Pat:
    n = max(int(n), 1)
    f = fn(pat)
    return per_cycle(lambda k: f if k % n == (n - 1 if last else 0) else pat)


def chunk(pat: Pat, n: float, fn, back=False) -> Pat:
    n = max(int(n), 1)
    f = fn(pat)

    def q(b, e):
        out = []
        for k, s0, s1 in cycles(b, e):
            i = (n - 1 - k % n) if back else k % n
            r0, r1 = k + i / n, k + (i + 1) / n
            for lo, hi, src in ((s0, min(s1, r0), pat), (max(s0, r0), min(s1, r1), f),
                                (max(s0, r1), s1, pat)):
                if hi > lo + EPS:
                    out += src.q(lo, hi)
        return out
    return Pat(q)


def off(pat: Pat, t: float, fn) -> Pat:
    return stack(pat, fn(late(pat, t)))


def jux_by(pat: Pat, by: float, fn) -> Pat:
    return stack(set_bus(pat, "pan", 0.5 - by / 2), set_bus(fn(pat), "pan", 0.5 + by / 2))


def echo_with(pat: Pat, times: float, t: float, fn) -> Pat:
    layers, cur = [], pat
    for i in range(max(int(times), 1)):
        layers.append(late(cur, i * t))
        cur = fn(cur)
    return stack(*layers)


def echo(pat: Pat, times: float, t: float, fb: float) -> Pat:
    return stack(*[fmap(late(pat, i * t), _amp(fb ** i)) for i in range(max(int(times), 1))])


def _amp(m):
    def f(h):
        ctl = dict(h.ctl)
        ctl["amp"] = ctl.get("amp", 1.0) * m
        return replace(h, ctl=ctl)
    return f


# ---------------------------------------------------------------- controls


def set_param(pat: Pat, key: str, arg, bus=False) -> Pat:
    """Set a control on every event: a constant, or sampled from a pattern / signal."""
    def put(h, v, span):
        if bus:
            new = tuple(sorted(dict(h.bus, **{key: v}).items()))
            return replace(h, bus=new, spans=h.spans + ((span,) if span else ()))
        ctl = dict(h.ctl)
        ctl[key] = v
        return replace(h, ctl=ctl, spans=h.spans + ((span,) if span else ()))

    if not isinstance(arg, (Pat, Sig)):
        return fmap(pat, lambda h: put(h, arg, None))

    def q(b, e):
        out = []
        for h in pat.q(b, e):
            v, span = sample(arg, h.start)
            out.append(put(h, v, span) if v is not None else h)
        return out
    return Pat(q)


def set_bus(pat: Pat, key: str, v) -> Pat:
    return set_param(pat, key, v, bus=True)


def add(pat: Pat, arg, sign=1.0) -> Pat:
    def q(b, e):
        out = []
        for h in pat.q(b, e):
            v, span = sample(arg, h.start)
            if v is None:
                out.append(h)
                continue
            ctl = dict(h.ctl)
            ctl["add"] = ctl.get("add", 0.0) + sign * num(v)
            out.append(replace(h, ctl=ctl, spans=h.spans + ((span,) if span else ())))
        return out
    return Pat(q)


def hurry(pat: Pat, f: float) -> Pat:
    def speed_up(h):
        ctl = dict(h.ctl)
        ctl["speed"] = ctl.get("speed", 1.0) * f
        return replace(h, ctl=ctl)
    return fmap(fast(pat, f), speed_up)
