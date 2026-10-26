"""The music language: a subset of Strudel (strudel.cc) syntax.

    setcpm(118/4)

    drums: s("bd*2 [~ sd] <hh*4 hh*8>")      // label: pattern
      .gain(.9)                             // one method per line, indented
      .room(.15)

    bass: note("<c2 eb2 g1 bb1>*2")
      .s("supersaw")
      .lpf(slider(700, 100, 4000))          // slider() = an inline knob
      .lpq("<2 8>")                         // method arguments can be patterns too

    _chords: n("[0,2,4]").scale("C4:minor") // a leading _ mutes, like in Strudel

Code is parsed here (in Python), never executed as JavaScript.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

from . import pattern, synth

HEADS = {"s": "s", "sound": "s", "note": "note", "n": "n"}

# canonical name -> (default, min, max)
NUM_PARAMS = {
    "gain": (1.0, 0.0, 2.0),
    "velocity": (1.0, 0.0, 1.0),
    "lpf": (20000.0, 20.0, 20000.0),
    "hpf": (0.0, 0.0, 10000.0),
    "lpq": (1.0, 0.0, 30.0),
    "room": (0.0, 0.0, 1.0),
    "delay": (0.0, 0.0, 1.0),
    "delaytime": (0.25, 0.01, 2.0),
    "delayfb": (0.5, 0.0, 0.95),
    "crush": (0.0, 0.0, 16.0),
    "pan": (0.5, 0.0, 1.0),
    "attack": (0.005, 0.0, 4.0),
    "decay": (0.1, 0.001, 4.0),
    "sustain": (1.0, 0.0, 1.0),
    "release": (0.1, 0.005, 6.0),
    "transpose": (0.0, -48.0, 48.0),
}
ALIASES = {
    "cutoff": "lpf", "lp": "lpf", "ctf": "lpf", "hcutoff": "hpf", "hp": "hpf",
    "resonance": "lpq", "delayfeedback": "delayfb", "dfb": "delayfb", "dt": "delaytime",
    "sound": "s", "att": "attack", "dec": "decay", "sus": "sustain", "rel": "release",
    "trans": "transpose", "vel": "velocity",
}
# applied to the whole track (so knobs act on ringing notes); the rest are per note
TRACK_LEVEL = ("lpf", "hpf", "lpq", "crush", "room", "delay", "delaytime", "delayfb", "pan")
INT_TRANSFORMS = {"fast": 16, "slow": 16, "ply": 8}
OTHER_METHODS = ("s", "scale", "bank", "rev", "degrade", "degradeBy")

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11], "ionian": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10], "aeolian": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10], "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11], "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "locrian": [0, 1, 3, 5, 6, 8, 10], "harmonic minor": [0, 2, 3, 5, 7, 8, 11],
    "major pentatonic": [0, 2, 4, 7, 9], "minor pentatonic": [0, 3, 5, 7, 10],
    "blues": [0, 3, 5, 6, 7, 10], "chromatic": list(range(12)),
    "whole tone": [0, 2, 4, 6, 8, 10],
}


class LangError(Exception):
    def __init__(self, msg: str, line: int, col: int = 0):
        super().__init__(msg)
        self.line = line  # 1-based
        self.col = col  # 0-based


class ErrorList(Exception):
    def __init__(self, errors: list[LangError]):
        super().__init__("; ".join(str(e) for e in errors))
        self.errors = errors


# ---------------------------------------------------------------- model


@dataclass
class Param:
    const: object = None  # float or str
    node: object = None  # a mini-notation pattern, when the argument is a string

    @property
    def patterned(self):
        return self.node is not None


@dataclass
class Track:
    name: str
    line: int
    head: str = "s"  # s | note | n
    node: object = None
    params: dict = field(default_factory=dict)
    fast: int = 1
    slow: int = 1
    ply: int = 1
    rev: bool = False
    degrade: float = 0.0
    scale: tuple | None = None  # (root midi, intervals)
    _pcache: dict = field(default_factory=dict, repr=False)

    def query(self, cycle: int):
        evs = pattern.query(self.node, cycle, self.fast, self.slow, self.rev)
        if self.ply > 1:
            evs = [pattern.Event(e.start + i * e.dur / self.ply, e.dur / self.ply, e.value, e.span)
                   for e in evs for i in range(self.ply)]
        if self.degrade > 0:
            evs = [e for e in evs
                   if random.Random(f"{cycle}:{e.start:.6f}:{self.name}").random() >= self.degrade]
        return evs

    def patterned(self, key):
        p = self.params.get(key)
        return p is not None and p.patterned

    def const(self, key):
        p = self.params.get(key)
        if p is None or p.patterned:
            return NUM_PARAMS[key][0] if key in NUM_PARAMS else None
        return p.const

    def at(self, key, cycle: int, t: float):
        """Value of a parameter for an event at cycle position t -> (value, span or None)."""
        p = self.params.get(key)
        if p is None:
            return (NUM_PARAMS[key][0] if key in NUM_PARAMS else None), None
        if not p.patterned:
            return p.const, None
        ck = (key, cycle)
        evs = self._pcache.get(ck)
        if evs is None:
            if len(self._pcache) > 64:
                self._pcache.clear()
            evs = self._pcache[ck] = pattern.query(p.node, cycle, self.fast, self.slow, self.rev)
        hit = None
        for e in evs:
            if e.start <= t + 1e-9:
                hit = e
        hit = hit or (evs[0] if evs else None)
        if hit is None:
            return (NUM_PARAMS[key][0] if key in NUM_PARAMS else None), None
        if key in NUM_PARAMS:
            _, lo, hi = NUM_PARAMS[key]
            return min(max(float(hit.value), lo), hi), hit.span
        return hit.value, hit.span

    def midi(self, value: str) -> float | None:
        if self.head == "n":
            deg = int(float(value))
            if self.scale is None:
                return 48 + deg
            root, steps = self.scale
            octave, idx = divmod(deg, len(steps))
            return root + 12 * octave + steps[idx]
        return synth.note_to_midi(value)


DEFAULT_TRACK = Track(name="", line=0, head="note")


@dataclass
class Program:
    cps: float = 0.5  # Strudel's default: 0.5 cycles/second = 120 bpm in 4/4
    tracks: dict = field(default_factory=dict)
    silent: bool = False


# ---------------------------------------------------------------- tokenizer

TOKEN_RE = re.compile(r"""
    (?P<ws>[ \t\r]+) | (?P<nl>\n) | (?P<comment>//[^\n]*) |
    (?P<num>\d+\.\d*|\.\d+|\d+) |
    (?P<id>[A-Za-z_$][A-Za-z0-9_$]*) |
    (?P<str>"[^"\n]*"|'[^'\n]*'|`[^`\n]*`) |
    (?P<op>[().,:+\-*/])
""", re.X)


@dataclass
class Tok:
    kind: str
    text: str
    line: int
    col: int
    first: bool  # first token on its line


def tokenize(text: str) -> list[Tok]:
    toks, line, line_start, pos, first = [], 1, 0, 0, True
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            ch = text[pos]
            msg = "missing closing quote" if ch in "\"'`" else f"unexpected character '{ch}'"
            raise ErrorList([LangError(msg, line, pos - line_start)])
        kind = m.lastgroup
        col = pos - line_start
        if kind == "nl":
            line, line_start, first = line + 1, m.end(), True
        elif kind not in ("ws", "comment"):
            if kind == "str":
                toks.append(Tok("str", m.group()[1:-1], line, col + 1, first))
            else:
                toks.append(Tok(kind, m.group(), line, col, first))
            first = False
        pos = m.end()
    return toks


# ---------------------------------------------------------------- parser


class Parser:
    def __init__(self, text: str):
        self.toks = tokenize(text)
        self.i = 0
        self.prog = Program()
        self.errors: list[LangError] = []
        self.anon = 0

    # -- token helpers
    def peek(self, k=0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def next(self):
        t = self.peek()
        self.i += 1
        return t

    def at_op(self, ch, k=0):
        t = self.peek(k)
        return t is not None and t.kind == "op" and t.text == ch

    def expect(self, ch, what):
        t = self.peek()
        if not self.at_op(ch):
            where = t or self.toks[-1]
            raise LangError(f"expected '{ch}' {what}", where.line, where.col)
        return self.next()

    def end_line(self):
        return (self.toks[-1].line if self.toks else 1)

    # -- statements
    def parse(self) -> Program:
        while self.peek():
            start = self.i
            try:
                self.statement()
            except LangError as e:
                self.errors.append(e)
                self.skip_statement(max(start + 1, self.i))
        if self.errors:
            raise ErrorList(self.errors)
        return self.prog

    def skip_statement(self, from_i):
        """Jump to the next line that starts a new statement (not a `.method` line)."""
        self.i = from_i
        while self.peek() and not (self.peek().first and not self.at_op(".")):
            self.i += 1

    def statement(self):
        t = self.peek()
        if self.at_op("."):
            # chain lines whose head was commented out: ignore the whole block quietly
            self.skip_statement(self.i + 1)
            return
        if t.kind == "id" and self.at_op(":", 1):
            self.next()
            self.next()
            self.chain(t.text, t)
            return
        if t.kind == "id" and t.text in ("setcpm", "setcps"):
            self.next()
            self.expect("(", f"after {t.text}")
            v = self.expr()
            self.expect(")", f"to close {t.text}(")
            if v <= 0:
                raise LangError("tempo must be positive", t.line, t.col)
            self.prog.cps = v / 60 if t.text == "setcpm" else v
            return
        if t.kind == "id" and t.text == "hush":
            self.next()
            self.expect("(", "after hush")
            self.expect(")", "to close hush(")
            self.prog.silent = True
            return
        if t.kind == "id" and t.text in HEADS:
            self.chain("$", t)
            return
        raise LangError(
            f"expected a pattern like `name: s(\"bd sd\")` or setcpm(...), got '{t.text}'",
            t.line, t.col)

    def chain(self, label: str, label_tok: Tok):
        head = self.next()
        if head is None or head.kind != "id" or head.text not in HEADS:
            where = head or label_tok
            raise LangError("a pattern starts with s(\"...\"), note(\"...\") or n(\"...\")",
                            where.line, where.col)
        self.expect("(", f"after {head.text}")
        src = self.next()
        if src is None or src.kind != "str":
            where = src or head
            raise LangError(f"{head.text}() needs a pattern string, like {head.text}(\"...\")",
                            where.line, where.col)
        self.expect(")", f"to close {head.text}(")

        muted = label.startswith("_")
        if label in ("$", "_$"):
            self.anon += 1
            name = f"p{self.anon}"
        else:
            name = label.lstrip("_")
        track = Track(name=name, line=head.line, head=HEADS[head.text],
                      node=self.mini(src), params={})
        self.check_values(track.head, track.node, src)

        while self.at_op("."):
            self.next()
            self.method(track)

        if not muted:
            if name in self.prog.tracks:
                raise LangError(f"'{name}' is defined twice", label_tok.line, label_tok.col)
            self.prog.tracks[name] = track

    def mini(self, tok: Tok):
        try:
            return pattern.parse(tok.text, tok.col, tok.line)
        except pattern.PatternError as e:
            raise LangError(str(e), tok.line, e.col)

    def check_values(self, kind: str, node, tok: Tok):
        for w in pattern.words(node):
            v = w.value
            line, col = w.span[0], w.span[1]
            if kind == "s" and not synth.is_sound(v):
                raise LangError(
                    f"unknown sound '{v}' (drums: {' '.join(sorted(synth.DRUMS))}; "
                    f"synths: {' '.join(sorted(synth.WAVES))})", line, col)
            if kind == "note" and synth.note_to_midi(v) is None:
                raise LangError(f"'{v}' is not a note (try c3, eb4, f#2 or a midi number)", line, col)
            if kind in ("n", "num"):
                try:
                    float(v)
                except ValueError:
                    raise LangError(f"'{v}' is not a number", line, col)

    def method(self, track: Track):
        t = self.next()
        if t is None or t.kind != "id":
            where = t or self.toks[-1]
            raise LangError("expected a method name after '.'", where.line, where.col)
        name = ALIASES.get(t.text, t.text)
        args = []
        if self.at_op("("):
            self.next()
            if not self.at_op(")"):
                args.append(self.arg())
                while self.at_op(","):
                    self.next()
                    args.append(self.arg())
            self.expect(")", f"to close .{t.text}(")

        def need(n):
            if len(args) != n:
                raise LangError(f".{t.text}() takes {n} argument{'s' if n != 1 else ''}", t.line, t.col)

        if name in NUM_PARAMS:
            need(1)
            a = args[0]
            if isinstance(a, Tok):
                node = self.mini(a)
                self.check_values("num", node, a)
                track.params[name] = Param(node=node)
            else:
                _, lo, hi = NUM_PARAMS[name]
                track.params[name] = Param(const=min(max(a, lo), hi))
        elif name == "s":
            need(1)
            a = args[0]
            if not isinstance(a, Tok):
                raise LangError('.s() takes a sound name in quotes, like .s("sawtooth")', t.line, t.col)
            node = self.mini(a)
            self.check_values("s", node, a)
            ws = list(pattern.words(node))
            if isinstance(node, pattern.Seq) and len(node.steps) == 1 and len(ws) == 1 \
                    and isinstance(node.steps[0][0], pattern.Word):
                track.params["s"] = Param(const=ws[0].value)
            else:
                track.params["s"] = Param(node=node)
        elif name == "scale":
            need(1)
            a = args[0]
            if not isinstance(a, Tok):
                raise LangError('.scale() takes a name in quotes, like .scale("C4:minor")', t.line, t.col)
            track.scale = self.scale(a)
        elif name == "bank":
            need(1)  # accepted for Strudel compatibility; jynx has one kit
        elif name in INT_TRANSFORMS:
            need(1)
            a = args[0]
            if isinstance(a, Tok) or a != int(a) or not 1 <= a <= INT_TRANSFORMS[name]:
                raise LangError(f".{name}() takes a whole number from 1 to {INT_TRANSFORMS[name]}",
                                t.line, t.col)
            setattr(track, name, int(a))
        elif name == "rev":
            need(0)
            track.rev = True
        elif name in ("degrade", "degradeBy"):
            if name == "degrade":
                need(0)
                track.degrade = 0.5
            else:
                need(1)
                if isinstance(args[0], Tok):
                    raise LangError(".degradeBy() takes a number from 0 to 1", t.line, t.col)
                track.degrade = min(max(args[0], 0.0), 1.0)
        else:
            known = sorted({*NUM_PARAMS, *INT_TRANSFORMS, *OTHER_METHODS, *ALIASES})
            raise LangError(f"unknown method .{t.text}() — jynx knows: {' '.join(known)}",
                            t.line, t.col)

    def scale(self, tok: Tok):
        parts = [p for p in re.split(r"[:\s]+", tok.text.strip()) if p]
        if not parts:
            raise LangError("empty scale name", tok.line, tok.col)
        root = synth.note_to_midi(parts[0].lower())
        if root is None or not re.match(r"^[a-gA-G]", parts[0]):
            raise LangError(f"'{parts[0]}' is not a root note (try C, Eb3, F#4)", tok.line, tok.col)
        mode = " ".join(parts[1:]).lower() or "major"
        if mode not in SCALES:
            raise LangError(f"unknown scale '{mode}' (known: {', '.join(SCALES)})", tok.line, tok.col)
        return root, SCALES[mode]

    # -- arguments: a string (pattern) or a number expression
    def arg(self):
        t = self.peek()
        if t is not None and t.kind == "str":
            return self.next()
        return self.expr()

    def expr(self):
        v = self.term()
        while self.at_op("+") or self.at_op("-"):
            op = self.next().text
            r = self.term()
            v = v + r if op == "+" else v - r
        return v

    def term(self):
        v = self.factor()
        while self.at_op("*") or self.at_op("/"):
            op = self.next()
            r = self.factor()
            if op.text == "/" and r == 0:
                raise LangError("division by zero", op.line, op.col)
            v = v * r if op.text == "*" else v / r
        return v

    def factor(self):
        t = self.next()
        if t is None:
            raise LangError("expected a number", self.end_line(), 0)
        if t.kind == "num":
            return float(t.text)
        if t.kind == "op" and t.text == "-":
            return -self.factor()
        if t.kind == "op" and t.text == "(":
            v = self.expr()
            self.expect(")", "to close (")
            return v
        if t.kind == "id" and t.text == "slider":
            # slider(value, min, max[, step]) — the editor draws it; the engine uses value
            self.expect("(", "after slider")
            vals = [self.expr()]
            while self.at_op(","):
                self.next()
                vals.append(self.expr())
            self.expect(")", "to close slider(")
            return vals[0]
        raise LangError(f"expected a number, a \"pattern\" or slider(...), got '{t.text}'",
                        t.line, t.col)


def parse(text: str) -> Program:
    return Parser(text).parse()
