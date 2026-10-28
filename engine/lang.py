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

import difflib
import math
import re
from dataclasses import dataclass, field, replace

from . import pat as P
from . import pattern, synth

HEADS = {"s": "s", "sound": "s", "note": "note", "n": "n"}

# canonical name -> (default, min, max)
NUM_PARAMS = {
    # per note
    "gain": (1.0, 0.0, 2.0),
    "velocity": (1.0, 0.0, 1.0),
    "attack": (0.005, 0.0, 4.0),
    "decay": (0.1, 0.001, 4.0),
    "sustain": (1.0, 0.0, 1.0),
    "release": (0.1, 0.005, 6.0),
    "transpose": (0.0, -48.0, 48.0),
    "speed": (1.0, -8.0, 8.0),
    "clip": (1.0, 0.01, 8.0),
    "fm": (0.0, 0.0, 32.0),
    "fmh": (1.0, 0.0, 16.0),
    "vib": (0.0, 0.0, 30.0),
    "vibmod": (0.5, 0.0, 12.0),
    "cut": (0.0, 0.0, 64.0),
    "detune": (0.2, 0.0, 12.0),
    # filter envelope (Strudel's lpenv): the cutoff jumps up lpenv octaves and falls back
    "lpenv": (0.0, -10.0, 10.0),
    "lpattack": (0.005, 0.0, 4.0),
    "lpdecay": (0.14, 0.0, 4.0),
    "lpsustain": (0.0, 0.0, 1.0),
    "lprelease": (0.1, 0.0, 4.0),
    "orbit": (1.0, 0.0, 16.0),
    # whole track (so knobs act on notes that are still ringing)
    "lpf": (20000.0, 20.0, 20000.0),
    "hpf": (0.0, 0.0, 10000.0),
    "lpq": (1.0, 0.0, 30.0),
    "bandf": (0.0, 0.0, 20000.0),
    "bandq": (1.0, 0.1, 30.0),
    "crush": (0.0, 0.0, 16.0),
    "coarse": (1.0, 1.0, 64.0),
    "shape": (0.0, 0.0, 0.99),
    "distort": (0.0, 0.0, 10.0),
    "phaser": (0.0, 0.0, 20.0),
    "phaserdepth": (0.75, 0.0, 1.0),
    "phasercenter": (1000.0, 50.0, 10000.0),
    "phasersweep": (2000.0, 0.0, 10000.0),
    "room": (0.0, 0.0, 1.0),
    "delay": (0.0, 0.0, 1.0),
    "delaytime": (0.25, 0.01, 2.0),
    "delayfb": (0.5, 0.0, 0.95),
    "pan": (0.5, 0.0, 1.0),
    "postgain": (1.0, 0.0, 4.0),
}
STR_PARAMS = ("s", "vowel", "note", "n")
# sidechain: .duck("4:5:6") ducks those orbits each time this pattern plays;
# lists like "2:1" go one per orbit
DUCK_PARAMS = ("duckorbit", "duckattack", "duckdepth")
ALIASES = {
    "cutoff": "lpf", "lp": "lpf", "ctf": "lpf", "hcutoff": "hpf", "hp": "hpf",
    "resonance": "lpq", "delayfeedback": "delayfb", "dfb": "delayfb", "dt": "delaytime",
    "delayt": "delaytime", "sound": "s", "att": "attack", "dec": "decay", "sus": "sustain",
    "rel": "release", "trans": "transpose", "vel": "velocity", "legato": "clip",
    "bpf": "bandf", "bp": "bandf", "bpq": "bandq", "dist": "distort", "ph": "phaser",
    "phd": "phaserdepth", "phasdp": "phaserdepth", "phc": "phasercenter", "phs": "phasersweep",
    "firstOf": "every",
    "o": "orbit", "duck": "duckorbit", "duckatt": "duckattack", "lpe": "lpenv",
    "lpa": "lpattack", "lpd": "lpdecay", "lps": "lpsustain", "lpr": "lprelease",
}
TRACK_LEVEL = ("lpf", "hpf", "lpq", "bandf", "bandq", "crush", "coarse", "shape", "distort",
               "phaser", "phaserdepth", "phasercenter", "phasersweep", "vowel",
               "room", "delay", "delaytime", "delayfb", "pan", "postgain")
VOWELS = {"a", "e", "i", "o", "u"}
# accepted so Strudel code runs; they only change how strudel.cc draws or routes things
NO_OPS = ("bank", "color", "colour", "analyze", "pianoroll", "punchcard", "scope",
          "spiral", "markcss", "_pianoroll", "_punchcard", "_scope", "_spiral")
# pattern functions that make sense with no arguments: .jux(rev), .every(2, palindrome)
BARE_FNS = ("rev", "palindrome", "brak", "degrade", "arpeggiate", "press")

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11], "ionian": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10], "aeolian": [0, 2, 3, 5, 7, 8, 10],
    "dorian": [0, 2, 3, 5, 7, 9, 10], "phrygian": [0, 1, 3, 5, 7, 8, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11], "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "locrian": [0, 1, 3, 5, 6, 8, 10], "harmonic minor": [0, 2, 3, 5, 7, 8, 11],
    "major pentatonic": [0, 2, 4, 7, 9], "minor pentatonic": [0, 3, 5, 7, 10],
    "blues": [0, 3, 5, 6, 7, 10], "chromatic": list(range(12)),
    "whole tone": [0, 2, 4, 6, 8, 10],
    "melodic minor": [0, 2, 3, 5, 7, 9, 11], "harmonic major": [0, 2, 4, 5, 7, 8, 11],
    "phrygian dominant": [0, 1, 4, 5, 7, 8, 10], "lydian dominant": [0, 2, 4, 6, 7, 9, 10],
    "altered": [0, 1, 3, 4, 6, 8, 10], "super locrian": [0, 1, 3, 4, 6, 8, 10],
    "dorian b2": [0, 1, 3, 5, 7, 9, 10], "lydian augmented": [0, 2, 4, 6, 8, 9, 11],
    "locrian #2": [0, 2, 3, 5, 6, 8, 10], "mixolydian b6": [0, 2, 4, 5, 7, 8, 10],
    "locrian major": [0, 2, 4, 5, 6, 8, 10], "ultralocrian": [0, 1, 3, 4, 6, 8, 9],
    "leading whole tone": [0, 2, 4, 6, 8, 10, 11],
    "double harmonic major": [0, 1, 4, 5, 7, 8, 11], "double harmonic": [0, 1, 4, 5, 7, 8, 11],
    "byzantine": [0, 1, 4, 5, 7, 8, 11], "hungarian minor": [0, 2, 3, 6, 7, 8, 11],
    "hungarian major": [0, 3, 4, 6, 7, 9, 10], "neapolitan major": [0, 1, 3, 5, 7, 9, 11],
    "neopolitan major": [0, 1, 3, 5, 7, 9, 11], "neapolitan minor": [0, 1, 3, 5, 7, 8, 11],
    "neopolitan minor": [0, 1, 3, 5, 7, 8, 11], "persian": [0, 1, 4, 5, 6, 8, 11],
    "enigmatic": [0, 1, 4, 6, 8, 10, 11], "oriental": [0, 1, 4, 5, 6, 9, 10],
    "major blues": [0, 2, 3, 4, 7, 9], "minor blues": [0, 3, 5, 6, 7, 10],
    "bebop": [0, 2, 4, 5, 7, 9, 10, 11], "bebop major": [0, 2, 4, 5, 7, 8, 9, 11],
    "bebop minor": [0, 2, 3, 4, 5, 7, 9, 10], "diminished": [0, 2, 3, 5, 6, 8, 9, 11],
    "half-whole diminished": [0, 1, 3, 4, 6, 7, 9, 10], "augmented": [0, 3, 4, 7, 8, 11],
    "prometheus": [0, 2, 4, 6, 9, 10], "minor hexatonic": [0, 2, 3, 5, 7, 11],
    "pentatonic": [0, 2, 4, 7, 9], "ionian pentatonic": [0, 4, 5, 7, 11],
    "mixolydian pentatonic": [0, 4, 5, 7, 10], "lydian pentatonic": [0, 4, 6, 7, 11],
    "locrian pentatonic": [0, 3, 5, 6, 10], "minor six pentatonic": [0, 3, 5, 7, 9],
    "scriabin": [0, 1, 4, 7, 9], "hirajoshi": [0, 2, 3, 7, 8], "in-sen": [0, 1, 5, 7, 10],
    "iwato": [0, 1, 5, 6, 10], "kumoijoshi": [0, 1, 5, 7, 8], "pelog": [0, 1, 3, 7, 8],
    "egyptian": [0, 2, 5, 7, 10], "ritusen": [0, 2, 5, 7, 9],
}


def scale_of(text: str):
    """"Ab:minor:pentatonic" / "C4 major" -> (root midi, steps), or an error message."""
    parts = [p for p in re.split(r"[:\s]+", text.strip()) if p]
    if not parts:
        return "empty scale name"
    root = synth.note_to_midi(parts[0].lower())
    if root is None or not re.match(r"^[a-gA-G]", parts[0]):
        return f"'{parts[0]}' is not a root note (try C, Eb3, F#4)"
    mode = " ".join(parts[1:]).lower() or "major"
    if mode not in SCALES:
        close = difflib.get_close_matches(mode, SCALES, n=2, cutoff=0.6)
        hint = f" — did you mean {' or '.join(close)}?" if close else " (the README lists the scales)"
        return f"unknown scale '{mode}'{hint}"
    return root, SCALES[mode]


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
    """A track-wide setting from the main chain: a number/name, or a signal (sine...)."""
    const: object = None
    sig: object = None


@dataclass
class Track:
    name: str
    line: int
    head: str = "s"  # s | note | n
    pat: object = P.SILENCE
    params: dict = field(default_factory=dict)  # live: applied the moment code is evaluated
    scale: tuple | None = None  # (root midi, intervals)

    def query(self, cycle: int):
        """Events starting in this cycle; start is relative to the cycle (0..1)."""
        haps = self.pat.q(cycle, cycle + 1)
        haps.sort(key=lambda h: h.start)
        return [replace(h, start=h.start - cycle) for h in haps]

    def const(self, key):
        p = self.params.get(key)
        if p is None or p.const is None:
            return NUM_PARAMS[key][0] if key in NUM_PARAMS else None
        return p.const

    def value(self, key, cycle_pos: float):
        """A track setting right now (signals move with the clock)."""
        p = self.params.get(key)
        if p is not None and p.sig is not None:
            return clamp(key, p.sig(cycle_pos))
        return self.const(key)

    def midi(self, value, add: float = 0.0, head: str | None = None,
             scale: tuple | None = None) -> float | None:
        scale = scale or self.scale
        head = head or self.head
        if head == "note" and scale and _is_number(value):
            head = "n"  # note(run(8)).scale(...): numbers are scale steps, like Strudel
        if head == "n":
            try:
                deg = round(float(value) + add)
            except (TypeError, ValueError):
                return None
            if scale is None:
                return 48 + deg
            root, steps = scale
            octave, idx = divmod(deg, len(steps))
            return root + 12 * octave + steps[idx]
        m = synth.note_to_midi(str(value)) if not isinstance(value, (int, float)) else float(value)
        return None if m is None else m + add


def _is_number(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def clamp(key, v):
    if key in NUM_PARAMS:
        try:
            v = float(str(v).split(":")[0])
        except ValueError:
            return NUM_PARAMS[key][0]
        _, lo, hi = NUM_PARAMS[key]
        return min(max(v, lo), hi)
    return v


DEFAULT_TRACK = Track(name="", line=0, head="note")


@dataclass
class Program:
    cps: float = 0.5  # Strudel's default: 0.5 cycles/second = 120 bpm in 4/4
    tracks: dict = field(default_factory=dict)
    silent: bool = False


@dataclass
class Fn:
    """A function argument: x => x.add(12).gain(.5), or rev, or fast(2)."""
    calls: list  # [(tok, name, args), ...]


@dataclass
class PatArg:
    """A pattern made by a function, like run(8)."""
    pat: object


@dataclass
class SigArg:
    sig: P.Sig
    seg: float | None = None  # .segment(n) turns it into a pattern


# ---------------------------------------------------------------- tokenizer

TOKEN_RE = re.compile(r"""
    (?P<ws>[ \t\r]+) | (?P<nl>\n) | (?P<comment>//[^\n]*) |
    (?P<num>\d+\.\d*|\.\d+|\d+) |
    (?P<id>[A-Za-z_$][A-Za-z0-9_$]*) |
    (?P<str>"[^"\n]*"|'[^'\n]*'|`[^`\n]*`) |
    (?P<op>=>|[().,:+\-*/=])
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
        self.vars: dict = {}  # let name = value

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
        if t.kind == "id" and t.text in ("let", "const", "var"):
            # let lpf = slider(7420, 0, 10000)   let bank = "tr909"   let wob = sine.range(1, 4)
            self.next()
            name = self.next()
            if name is None or name.kind != "id":
                raise LangError(f"expected a name after {t.text}", t.line, t.col)
            self.expect("=", f"after {t.text} {name.text}")
            self.vars[name.text] = self.arg()
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
        src = self.arg()
        kind = HEADS[head.text]
        if isinstance(src, Tok):
            node = self.mini(src)
            self.check_values(kind, node, src)
            pat = P.from_node(node)
        elif isinstance(src, SigArg) and src.seg and kind != "s":
            pat = P.segment_sig(src.sig, src.seg)
        elif isinstance(src, PatArg) and kind != "s":
            pat = src.pat
        else:
            raise LangError(f"{head.text}() needs a pattern string, like {head.text}(\"...\")"
                            + (" (or run(n), or a signal with .segment(n))" if kind != "s" else ""),
                            head.line, head.col)
        self.expect(")", f"to close {head.text}(")

        muted = label.startswith("_")
        if label in ("$", "_$"):
            self.anon += 1
            name = f"p{self.anon}"
        else:
            name = label.lstrip("_")
        track = Track(name=name, line=head.line, head=kind, pat=pat, params={})
        self.seeds = 0

        while self.at_op("."):
            self.next()
            tok, mname, args = self.call()
            track.pat = self.apply(track, track.pat, tok, mname, args, lam=False)

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
            if kind == "vowel" and v.lower() not in VOWELS:
                raise LangError(f"'{v}' is not a vowel (a e i o u)", line, col)
            if kind in ("n", "num"):
                try:
                    float(v.split(":")[0])
                except ValueError:
                    raise LangError(f"'{v}' is not a number", line, col)

    # -- method calls
    def call(self):
        t = self.next()
        if t is None or t.kind != "id":
            where = t or self.toks[-1]
            raise LangError("expected a method name after '.'", where.line, where.col)
        args = []
        if self.at_op("("):
            self.next()
            if not self.at_op(")"):
                args.append(self.arg())
                while self.at_op(","):
                    self.next()
                    args.append(self.arg())
            self.expect(")", f"to close .{t.text}(")
        return t, ALIASES.get(t.text, t.text), args

    def apply(self, track: Track, pat, t: Tok, name: str, args: list, lam: bool):
        """One .method(...) -> the new pattern. In the main chain (lam=False) plain
        numbers become live track settings; inside a function (x => x...) they only
        touch the events that function produces."""

        def bad(msg):
            return LangError(msg, t.line, t.col)

        def need(lo, hi=None):
            hi = lo if hi is None else hi
            if not lo <= len(args) <= hi:
                want = f"{lo}" if lo == hi else f"{lo}-{hi}"
                raise bad(f".{t.text}() takes {want} argument{'s' if hi != 1 else ''}")

        def nums(idxs, make, vals=()):
            """make(*numbers), where each argument may also be a "pattern", a signal or run()."""
            if not idxs:
                return make(*vals)
            return P.patternify(numpat(idxs[0]), lambda v: nums(idxs[1:], make, vals + (v,)))

        def numpat(i):
            """A number, or a pattern of numbers ("<1 2>")."""
            a = args[i]
            if isinstance(a, float):
                return a
            if isinstance(a, Tok):
                node = self.mini(a)
                self.check_values("num", node, a)
                return P.from_node(node)
            if isinstance(a, SigArg):
                return P.segment_sig(a.sig, a.seg) if a.seg else a.sig
            if isinstance(a, PatArg):
                return a.pat
            raise bad(f".{t.text}() takes a number, a \"pattern\" or a signal like sine here")

        def strpat(i, kind="any"):
            a = args[i]
            if not isinstance(a, Tok):
                raise bad(f".{t.text}() takes a \"pattern\" in quotes here")
            node = self.mini(a)
            if kind != "any":
                self.check_values(kind, node, a)
            return P.from_node(node)

        def fn(i):
            a = args[i]
            if isinstance(a, Fn):
                return lambda p: self.run(track, p, a)
            raise bad(f".{t.text}() needs a function here, like x => x.fast(2), or rev")

        def seed():
            self.seeds += 1
            return f"{track.name}:{self.seeds}"

        # ---- controls: sound, effects, envelopes
        if name in DUCK_PARAMS:
            need(1)
            a = args[0]
            if isinstance(a, float):
                a = Tok("str", f"{a:g}", t.line, t.col, False)
            if not isinstance(a, Tok):
                raise bad(f'.{t.text}() takes numbers like "4:5:6"')
            node = self.mini(a)
            self.check_values("num", node, a)
            words = list(pattern.words(node))
            if not lam and isinstance(node, pattern.Seq) and len(node.steps) == 1 and len(words) == 1 \
                    and isinstance(node.steps[0][0], pattern.Word):
                track.params[name] = Param(const=words[0].value)
                return pat
            return P.set_param(pat, name, P.from_node(node))
        if name in NUM_PARAMS or name in STR_PARAMS:
            need(1)
            a = args[0]
            bus = lam and name in TRACK_LEVEL
            if isinstance(a, Fn):
                raise bad(f".{t.text}() takes a value, not a function")
            if isinstance(a, Tok):
                kind = {"s": "s", "vowel": "vowel", "note": "note", "n": "n"}.get(name, "num")
                node = self.mini(a)
                self.check_values(kind, node, a)
                one = list(pattern.words(node))
                single = (isinstance(node, pattern.Seq) and len(node.steps) == 1 and len(one) == 1
                          and isinstance(node.steps[0][0], pattern.Word))
                if single and name in ("s", "vowel") and not lam:
                    track.params[name] = Param(const=one[0].value)
                    return pat
                if single and name in ("s", "vowel"):
                    return P.set_param(pat, name, one[0].value, bus)
                return P.set_param(pat, name, P.from_node(node), bus)
            if name in STR_PARAMS and name not in ("note", "n"):
                raise bad(f'.{t.text}() takes a name in quotes, like .{t.text}("...")')
            if isinstance(a, PatArg):
                return P.set_param(pat, name, a.pat, bus)
            if isinstance(a, SigArg):
                if a.seg:
                    return P.set_param(pat, name, P.segment_sig(a.sig, a.seg), bus)
                if not lam and name in TRACK_LEVEL:
                    track.params[name] = Param(sig=a.sig)
                    return pat
                return P.set_param(pat, name, a.sig, bus)
            v = clamp(name, a) if name in NUM_PARAMS else a
            if not lam and name not in ("note", "n"):
                track.params[name] = Param(const=v)
                return pat
            return P.set_param(pat, name, v, bus)
        if name == "adsr":
            need(1)
            a = args[0]
            parts = a.text.split(":") if isinstance(a, Tok) else []
            try:
                vals = [float(x) for x in parts]
            except ValueError:
                vals = []
            if not 1 <= len(vals) <= 4:
                raise bad('.adsr() takes "attack:decay:sustain:release", like .adsr(".01:.1:.5:.2")')
            for key, v in zip(("attack", "decay", "sustain", "release"), vals):
                if lam:
                    pat = P.set_param(pat, key, clamp(key, v))
                else:
                    track.params[key] = Param(const=clamp(key, v))
            return pat
        if name == "scale":
            need(1)
            a = args[0]
            if not isinstance(a, Tok):
                raise bad('.scale() takes a name in quotes, like .scale("C4:minor")')
            fixed = scale_of(a.text)
            if isinstance(fixed, tuple):
                if not lam:
                    track.scale = fixed
                    return pat
                return P.set_param(pat, "scale", fixed)
            if not re.search(r"[<\[\]>*/!@?,]", a.text) and " " not in a.text.strip():
                raise bad(fixed)
            node = self.mini(a)  # a pattern of scales: "<C:major A:minor>"
            for w in pattern.words(node):
                sc = scale_of(w.value)
                if not isinstance(sc, tuple):
                    raise LangError(sc, w.span[0], w.span[1])
            spat = P.fmap(P.from_node(node), lambda h: replace(h, value=scale_of(h.value)))
            return P.set_param(pat, "scale", spat)
        if name in NO_OPS:
            return pat

        # ---- time
        if name in ("fast", "slow", "hurry"):
            need(1)
            f = {"fast": P.fast, "slow": P.slow, "hurry": P.hurry}[name]
            return P.patternify(numpat(0), lambda v: f(pat, v))
        if name in ("late", "early"):
            need(1)
            sign = 1 if name == "late" else -1
            return P.patternify(numpat(0), lambda v: P.late(pat, sign * v))
        if name == "rev":
            need(0)
            return P.rev(pat)
        if name == "ply":
            need(1)
            return P.patternify(numpat(0), lambda v: P.ply(pat, v))
        if name in ("iter", "iterBack"):
            need(1)
            return P.patternify(numpat(0), lambda v: P.iter_(pat, v, back=name == "iterBack"))
        if name == "palindrome":
            need(0)
            return P.palindrome(pat)
        if name == "brak":
            need(0)
            return P.brak(pat)
        if name == "linger":
            need(1)
            return P.patternify(numpat(0), lambda v: P.linger(pat, v))
        if name == "zoom":
            need(2)
            return nums([0, 1], lambda s, e: P.zoom(pat, s, e))
        if name in ("swingBy", "swing"):
            if name == "swing":
                need(1)
                return P.patternify(numpat(0), lambda v: P.swing_by(pat, 1 / 3, v))
            need(2)
            return nums([0, 1], lambda x, n: P.swing_by(pat, x, n))
        if name in ("press", "pressBy"):
            need(0) if name == "press" else need(1)
            if name == "press":
                return P.press_by(pat, 0.5)
            return nums([0], lambda r: P.press_by(pat, r))
        if name in ("inside", "outside"):
            need(2)
            f = fn(1)
            return nums([0], lambda n: P.inside(pat, n if name == "inside" else 1 / n if n else 1, f))

        # ---- structure
        if name == "struct":
            need(1)
            return P.struct(pat, strpat(0))
        if name == "mask":
            need(1)
            return P.mask(pat, strpat(0))
        if name in ("euclid", "euclidRot", "euclidLegato", "euclidLegatoRot"):
            rot = name.endswith("Rot")
            need(3) if rot else need(2)
            legato = name.startswith("euclidLegato")
            return nums([0, 1, 2] if rot else [0, 1],
                        lambda k, n, r=0: P.euclid(pat, round(k), round(n), round(r), legato=legato))
        if name == "segment":
            need(1)
            return P.patternify(numpat(0), lambda v: P.segment(pat, v))
        if name == "arp":
            need(1)
            return P.arp(pat, strpat(0, "num"))
        if name == "arpeggiate":
            need(0)
            return P.arpeggiate(pat)

        # ---- randomness
        if name in ("degrade", "degradeBy"):
            need(0) if name == "degrade" else need(1)
            s = seed()
            return P.patternify(0.5 if name == "degrade" else numpat(0),
                                lambda v: P.degrade_by(pat, v, s))
        chances = {"sometimes": 0.5, "often": 0.75, "rarely": 0.25, "almostNever": 0.1,
                   "almostAlways": 0.9, "always": 1.0, "never": 0.0}
        if name in chances:
            need(1)
            return P.sometimes_by(pat, chances[name], fn(0), seed())
        if name == "sometimesBy":
            need(2)
            f, s = fn(1), seed()
            return P.patternify(numpat(0), lambda v: P.sometimes_by(pat, v, f, s))
        if name in ("someCycles", "someCyclesBy"):
            need(1) if name == "someCycles" else need(2)
            f, s = fn(len(args) - 1), seed()
            if name == "someCycles":
                return P.some_cycles_by(pat, 0.5, f, s)
            return nums([0], lambda p: P.some_cycles_by(pat, p, f, s))

        # ---- layering
        if name in ("every", "lastOf"):
            need(2)
            f = fn(1)
            return P.patternify(numpat(0), lambda v: P.every(pat, v, f, last=name == "lastOf"))
        if name in ("chunk", "chunkBack"):
            need(2)
            f = fn(1)
            return nums([0], lambda n: P.chunk(pat, round(n), lambda _: f(_), back=name == "chunkBack"))
        if name == "off":
            need(2)
            f = fn(1)
            return P.patternify(numpat(0), lambda v: P.off(pat, v, f))
        if name == "superimpose":
            need(1)
            return P.stack(pat, fn(0)(pat))
        if name == "layer":
            if not args:
                raise bad(".layer() takes one or more functions")
            return P.stack(*[fn(i)(pat) for i in range(len(args))])
        if name in ("jux", "juxBy"):
            if name == "jux":
                need(1)
                return P.jux_by(pat, 1.0, fn(0))
            need(2)
            f = fn(1)
            return nums([0], lambda by: P.jux_by(pat, by, f))
        if name == "echo":
            need(3)
            return nums([0, 1, 2], lambda n, t_, fb: P.echo(pat, round(n), t_, fb))
        if name == "stut":
            need(3)
            return nums([0, 1, 2], lambda n, fb, t_: P.echo(pat, round(n), t_, fb))
        if name == "echoWith":
            need(3)
            f = fn(2)
            return nums([0, 1], lambda n, t_: P.echo_with(pat, round(n), t_, f))
        if name in ("add", "sub"):
            need(1)
            return P.add(pat, numpat(0), 1.0 if name == "add" else -1.0)

        known = sorted({*NUM_PARAMS, *STR_PARAMS, *DUCK_PARAMS, *ALIASES, *METHODS, *NO_OPS})
        close = difflib.get_close_matches(t.text, known, n=3, cutoff=0.6)
        hint = f" — did you mean {' or '.join('.' + c + '()' for c in close)}?" if close else \
            " (the README lists everything jynx knows)"
        raise bad(f"unknown method .{t.text}(){hint}")

    def run(self, track: Track, pat, f: Fn):
        for tok, name, args in f.calls:
            pat = self.apply(track, pat, tok, name, args, lam=True)
        return pat

    # -- arguments: a "pattern", a number expression, a function or a signal
    def arg(self):
        t = self.peek()
        if t is None:
            raise LangError("expected an argument", self.end_line(), 0)
        if t.kind == "str":
            return self.next()
        if t.kind == "id" and t.text in self.vars and not self.at_op("(", 1) \
                and not self.at_op("=>", 1) and not self.at_op(".", 1):
            v = self.vars[t.text]
            if isinstance(v, float):
                return self.expr()  # so lpf * 2 works too
            self.next()
            return v
        if t.kind == "id" and self.at_op("=>", 1):
            return self.lambda_()
        if self.at_op("(") and self.peek(1) and self.peek(1).kind == "id" \
                and self.at_op(")", 2) and self.at_op("=>", 3):
            self.next()
            return self.lambda_(closing=True)
        if t.kind == "id" and (t.text in P.SIGNALS or t.text == "irand"):
            return self.signal()
        if t.kind == "id" and t.text == "run" and self.at_op("(", 1):
            return self.run_arg()
        if t.kind == "id" and t.text != "slider":
            # curried: .every(4, fast(2)) or bare: .jux(rev)
            name = ALIASES.get(t.text, t.text)
            if self.at_op("(", 1):
                tok, name, args = self.call()
                return Fn([(tok, name, args)])
            if name in BARE_FNS:
                self.next()
                return Fn([(t, name, [])])
            raise LangError(f"'{t.text}' can't be used here (functions look like x => x.fast(2))",
                            t.line, t.col)
        return self.expr()

    def run_arg(self):
        """run(8) = "0 1 2 3 4 5 6 7"; the count can be a pattern or a signal too."""
        t = self.next()
        self.expect("(", "after run")
        a = self.arg()
        self.expect(")", "to close run(")
        if isinstance(a, float):
            return PatArg(P.run(a))
        if isinstance(a, Tok):
            node = self.mini(a)
            self.check_values("num", node, a)
            return PatArg(P.patternify(P.from_node(node), P.run))
        if isinstance(a, SigArg):
            return PatArg(P.patternify(P.segment_sig(a.sig, a.seg) if a.seg else a.sig, P.run))
        raise LangError("run() takes a number, like run(8)", t.line, t.col)

    def lambda_(self, closing=False):
        param = self.next()
        if closing:
            self.next()  # )
        self.next()  # =>
        body = self.next()
        if body is None or body.kind != "id" or body.text != param.text:
            where = body or param
            raise LangError(f"a function looks like {param.text} => {param.text}.fast(2)",
                            where.line, where.col)
        calls = []
        while self.at_op("."):
            self.next()
            calls.append(self.call())
        return Fn(calls)

    def signal(self):
        t = self.next()
        if t.text == "irand":
            self.expect("(", "after irand")
            n = max(int(self.expr()), 1)
            self.expect(")", "to close irand(")
            sig = P.Sig(lambda x, n=n: math.floor(P.rand(x, "irand") * n))
        else:
            sig = P.Sig(P.SIGNALS[t.text])
        out = SigArg(sig)
        while self.at_op("."):
            self.next()
            m = self.next()
            if m is None or m.kind != "id":
                raise LangError("expected a method after the signal", t.line, t.col)
            self.expect("(", f"after .{m.text}")
            vals = [self.expr()]
            while self.at_op(","):
                self.next()
                vals.append(self.expr())
            self.expect(")", f"to close .{m.text}(")
            f = out.sig.f
            if m.text == "range" and len(vals) == 2:
                lo, hi = vals
                out.sig = P.Sig(lambda x, f=f, lo=lo, hi=hi: lo + f(x) * (hi - lo))
            elif m.text == "rangex" and len(vals) == 2 and vals[0] > 0 and vals[1] > 0:
                lo, hi = vals
                out.sig = P.Sig(lambda x, f=f, lo=lo, hi=hi: lo * (hi / lo) ** f(x))
            elif m.text in ("slow", "fast") and len(vals) == 1 and vals[0] > 0:
                k = vals[0] if m.text == "fast" else 1 / vals[0]
                out.sig = P.Sig(lambda x, f=f, k=k: f(x * k))
            elif m.text in ("add", "sub", "mul", "div") and len(vals) == 1:
                v = vals[0]
                op = {"add": lambda a: a + v, "sub": lambda a: a - v, "mul": lambda a: a * v,
                      "div": lambda a: a / v if v else a}[m.text]
                out.sig = P.Sig(lambda x, f=f, op=op: op(f(x)))
            elif m.text == "segment" and len(vals) == 1 and vals[0] >= 1:
                out.seg = vals[0]
            else:
                raise LangError(f"signals know .range(lo, hi) .rangex .slow(n) .fast(n) .segment(n) "
                                f".add .sub .mul .div — not .{m.text}({len(vals)} args)", m.line, m.col)
        return out

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
        if t.kind == "id" and isinstance(self.vars.get(t.text), float):
            return self.vars[t.text]
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


METHODS = ("adsr", "scale", "fast", "slow", "hurry", "late", "early", "rev", "ply", "iter",
           "iterBack", "palindrome", "brak", "linger", "zoom", "swing", "swingBy", "press",
           "pressBy", "inside", "outside", "struct", "mask", "euclid", "euclidRot", "euclidLegato",
           "euclidLegatoRot", "segment", "arp", "arpeggiate", "degrade", "degradeBy", "sometimes",
           "often", "rarely", "almostNever", "almostAlways", "always", "never", "sometimesBy",
           "someCycles", "someCyclesBy", "every", "lastOf", "chunk", "chunkBack", "off",
           "superimpose", "layer", "jux", "juxBy", "echo", "stut", "echoWith", "add", "sub")


def parse(text: str) -> Program:
    return Parser(text).parse()
