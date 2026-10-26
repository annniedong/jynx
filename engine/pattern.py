"""Mini-notation: the pattern language inside Strudel's strings.

    bd sd           sequence: steps share the cycle
    ~  or  -        rest
    [bd bd]         group: subdivide one step
    [c3,e3,g3]      stack: play together (chords)
    <c3 e3 g3>      alternate: one per cycle
    bd*2 / bd/2     faster / slower
    bd!3            replicate: bd bd bd
    c3@3 e3         weight: c3 takes 3/4 of the cycle
    c3 _ _ e3       hold: same as c3@3 e3
    bd(3,8)         euclidean rhythm (3 hits over 8 steps), bd(3,8,2) rotates
    bd?             randomly drop (50%)

Every event remembers the (line, columns) of the word that made it, so the
editor can light up the exact text that is playing.
"""
from __future__ import annotations

import random
from dataclasses import dataclass


class PatternError(Exception):
    def __init__(self, msg: str, col: int = 0):
        super().__init__(msg)
        self.col = col


@dataclass
class Event:
    start: float  # position inside the cycle, 0..1
    dur: float  # in cycles
    value: str
    span: tuple  # (line, col0, col1) in the source


# ---------------------------------------------------------------- nodes


@dataclass
class Word:
    value: str
    span: tuple


@dataclass
class Rest:
    pass


@dataclass
class Seq:
    steps: list  # [(node, weight), ...]


@dataclass
class Stack:
    layers: list


@dataclass
class Alt:
    children: list


@dataclass
class Fast:
    child: object
    n: int


@dataclass
class Slow:
    child: object
    n: int


@dataclass
class Degrade:
    child: object
    seed: int


def bjorklund(k: int, n: int) -> list[int]:
    if k <= 0:
        return [0] * n
    if k >= n:
        return [1] * n
    a, b = [[1]] * k, [[0]] * (n - k)
    while len(b) > 1:
        m = min(len(a), len(b))
        a, b = [a[i] + b[i] for i in range(m)], a[m:] + b[m:]
    return sum(a + b, [])


def words(node):
    """Every Word in a pattern (for validation)."""
    if isinstance(node, Word):
        yield node
    elif isinstance(node, Seq):
        for child, _ in node.steps:
            yield from words(child)
    elif isinstance(node, (Stack, Alt)):
        for child in node.layers if isinstance(node, Stack) else node.children:
            yield from words(child)
    elif isinstance(node, (Fast, Slow, Degrade)):
        yield from words(node.child)


# ---------------------------------------------------------------- parser

SYMBOLS = set("[]<>,~*/!?@()")
WORD_CHARS = set("#._-:")


def tokenize(src: str, offset: int = 0):
    toks = []
    i = 0
    while i < len(src):
        ch = src[i]
        if ch.isspace():
            i += 1
        elif ch in SYMBOLS:
            toks.append((ch, ch, offset + i, offset + i + 1))
            i += 1
        elif ch.isalnum() or ch in WORD_CHARS:
            j = i
            while j < len(src) and (src[j].isalnum() or src[j] in WORD_CHARS):
                j += 1
            toks.append(("word", src[i:j], offset + i, offset + j))
            i = j
        else:
            raise PatternError(f"unexpected character '{ch}'", offset + i)
    return toks


class Parser:
    def __init__(self, src: str, offset: int, line: int):
        self.toks = tokenize(src, offset)
        self.i = 0
        self.end_col = offset + len(src)
        self.line = line

    def peek(self, k=0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def next(self):
        t = self.peek()
        self.i += 1
        return t

    def col(self):
        t = self.peek()
        return t[2] if t else self.end_col

    def parse(self):
        if not self.toks:
            return Seq([(Rest(), 1)])
        node = self.parse_layers(None)
        if self.peek() is not None:
            raise PatternError(f"unexpected '{self.peek()[1]}'", self.col())
        return node

    def parse_layers(self, closer):
        layers = [self.parse_seq(closer)]
        while self.peek() and self.peek()[0] == ",":
            self.next()
            layers.append(self.parse_seq(closer))
        if len(layers) == 1:
            return Seq(layers[0])
        return Stack([Seq(l) for l in layers])

    def parse_seq(self, closer):
        steps = []
        while True:
            t = self.peek()
            if t is None or t[0] == "," or t[0] == closer:
                break
            if t[0] in "]>)":
                raise PatternError(f"unmatched '{t[0]}'", t[2])
            if t[0] == "word" and t[1] == "_":
                self.next()
                if not steps:
                    raise PatternError("'_' needs a step before it to hold", t[2])
                node, w = steps[-1]
                steps[-1] = (node, w + 1)
                continue
            steps.extend(self.parse_step())
        if not steps:
            raise PatternError("empty pattern", self.col())
        return steps

    def number(self, whole=True, allow_zero=False):
        t = self.next()
        ok = t is not None and t[0] == "word"
        if ok:
            try:
                v = float(t[1])
                ok = (v > 0 or allow_zero and v == 0) and (not whole or v == int(v))
            except ValueError:
                ok = False
        if not ok:
            kind = "a whole number" if whole else "a number"
            raise PatternError(f"expected {kind}", t[2] if t else self.end_col)
        return int(v) if whole else v

    def parse_step(self):
        node = self.parse_atom()
        copies, weight = 1, 1.0
        while self.peek() and self.peek()[0] in "*/!?@(":
            kind, _, c0, _ = self.next()
            if kind == "*":
                node = Fast(node, self.number())
            elif kind == "/":
                node = Slow(node, self.number())
            elif kind == "?":
                node = Degrade(node, c0)
            elif kind == "@":
                weight = self.number(whole=False)
            elif kind == "!":
                t = self.peek()
                copies = self.number() if t and t[0] == "word" and t[1].isdigit() else 2
            elif kind == "(":
                args = [self.number(allow_zero=True)]
                while self.peek() and self.peek()[0] == ",":
                    self.next()
                    args.append(self.number(allow_zero=True))
                if not self.peek() or self.peek()[0] != ")":
                    raise PatternError("euclid needs (hits,steps) or (hits,steps,rotate)", c0)
                self.next()
                if len(args) not in (2, 3) or args[1] == 0:
                    raise PatternError("euclid needs (hits,steps) or (hits,steps,rotate)", c0)
                k, n = args[0], args[1]
                rot = args[2] if len(args) == 3 else 0
                bits = bjorklund(k, n)
                bits = bits[rot % n:] + bits[:rot % n]
                node = Seq([(node if b else Rest(), 1) for b in bits])
        return [(node, weight)] * copies

    def parse_atom(self):
        t = self.next()
        if t is None:
            raise PatternError("pattern ends too early", self.end_col)
        kind, text, c0, c1 = t
        if kind == "word":
            if text == "-":
                return Rest()
            return Word(text, (self.line, c0, c1))
        if kind == "~":
            return Rest()
        if kind == "[":
            node = self.parse_layers("]")
            if not self.peek():
                raise PatternError("missing ']'", c0)
            self.next()
            return node
        if kind == "<":
            node = self.parse_layers(">")
            if not self.peek():
                raise PatternError("missing '>'", c0)
            self.next()
            if isinstance(node, Stack):
                raise PatternError("',' is not allowed inside < >", c0)
            return Alt([child for child, _ in node.steps])
        raise PatternError(f"unexpected '{text}'", c0)


def parse(src: str, offset: int = 0, line: int = 1):
    return Parser(src, offset, line).parse()


# ---------------------------------------------------------------- query


def _chance(cycle: int, start: float, seed: int) -> float:
    return random.Random(f"{cycle}:{start:.6f}:{seed}").random()


def _collect(node, cycle: int, t0: float, t1: float, out: list):
    if isinstance(node, Word):
        out.append(Event(t0, t1 - t0, node.value, node.span))
    elif isinstance(node, Rest):
        pass
    elif isinstance(node, Seq):
        total = sum(w for _, w in node.steps)
        pos = t0
        for child, w in node.steps:
            span = (t1 - t0) * w / total
            _collect(child, cycle, pos, pos + span, out)
            pos += span
    elif isinstance(node, Stack):
        for layer in node.layers:
            _collect(layer, cycle, t0, t1, out)
    elif isinstance(node, Alt):
        n = len(node.children)
        _collect(node.children[cycle % n], cycle // n, t0, t1, out)
    elif isinstance(node, Fast):
        step = (t1 - t0) / node.n
        for i in range(node.n):
            _collect(node.child, cycle * node.n + i, t0 + i * step, t0 + (i + 1) * step, out)
    elif isinstance(node, Slow):
        # the child is stretched over n cycles; this cycle sees one slice of it
        span = t1 - t0
        k = cycle % node.n
        big0 = t0 - k * span
        inner: list = []
        _collect(node.child, cycle // node.n, big0, big0 + node.n * span, inner)
        out.extend(e for e in inner if t0 - 1e-9 <= e.start < t1 - 1e-9)
    elif isinstance(node, Degrade):
        inner = []
        _collect(node.child, cycle, t0, t1, inner)
        out.extend(e for e in inner if _chance(cycle, e.start, node.seed) >= 0.5)


def query(node, cycle: int, fast: int = 1, slow: int = 1, rev: bool = False) -> list[Event]:
    """Events for one cycle, after whole-pattern fast/slow/rev."""
    out: list[Event] = []
    if slow > 1:
        _collect(Slow(node, slow), cycle, 0.0, 1.0, out)
    elif fast > 1:
        _collect(Fast(node, fast), cycle, 0.0, 1.0, out)
    else:
        _collect(node, cycle, 0.0, 1.0, out)
    if rev:
        out = [Event(1.0 - e.start - e.dur, e.dur, e.value, e.span) for e in out]
    out.sort(key=lambda e: e.start)
    return out
