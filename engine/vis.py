"""The visuals language: one operation per line, rendered as ANSI half-blocks.

    osc 12 .2              -- sources draw an image
    add shape 4 .3+drums*.4   -- later sources blend in: add / mult / diff
    kaleid 5               -- geometry bends every source (wherever it is written)
    rotate 0 .1
    color 1 .4 .9          -- color ops apply top to bottom
    feedback .85
    vis | screen           -- nothing is drawn until the visuals are piped to the screen

Any argument can be an expression. Track names are live audio levels (0..1),
plus `t` (seconds) and `cycle`. So `kaleid 3+bass*6` reacts to the bass.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SOURCES = {  # name: default args
    "osc": (10, 0.1, 0.8),
    "noise": (3, 0.3),
    "shape": (4, 0.4, 0.02),
    "rings": (10, 1),
    "grid": (8, 0.08),
    "solid": (1, 1, 1),
}
GEOMETRY = {
    "kaleid": (4,),
    "rotate": (0, 0.1),
    "scale": (1,),
    "repeat": (3, 3),
    "scroll": (0, 0.1),
    "warp": (0.2, 3),
    "pixelate": (20,),
}
COLOR = {
    "color": (1, 1, 1),
    "invert": (1,),
    "bright": (0.2,),
    "contrast": (1.5,),
    "hue": (0.2,),
    "thresh": (0.5,),
    "posterize": (4,),
    "feedback": (0.8,),
}
BLENDS = {"add", "mult", "diff"}
ALL = {**SOURCES, **GEOMETRY, **COLOR}
MATH = {k: getattr(math, k) for k in ("sin", "cos", "tan", "pi", "floor", "sqrt")}
MATH["abs"] = abs
MATH["min"] = min
MATH["max"] = max


class VisError(Exception):
    def __init__(self, msg: str, line: int, col: int = 0):
        super().__init__(msg)
        self.line = line
        self.col = col


class Env(dict):
    def __missing__(self, key):
        return 0.0


@dataclass
class Op:
    name: str
    blend: str | None
    args: list  # compiled expressions


def parse(text: str) -> list[Op]:
    ops, errors = [], []
    for idx, raw in enumerate(text.split("\n")):
        line = raw.split("--", 1)[0].strip()
        if not line:
            continue
        if "|" in line:
            col = len(raw) - len(raw.lstrip())
            if line.replace(" ", "").replace("\t", "") == "vis|screen":
                ops.append(Op("screen", None, []))
            else:
                errors.append(VisError("the visuals pipe is `vis | screen`", idx + 1, col))
            continue
        words = line.split()
        blend = None
        if words[0] in BLENDS:
            blend, words = words[0], words[1:]
        col = raw.index(words[0]) if words else 0
        try:
            if not words or words[0] not in ALL:
                name = words[0] if words else ""
                raise VisError(
                    f"unknown op '{name}' (sources: {' '.join(SOURCES)}; "
                    f"geometry: {' '.join(GEOMETRY)}; color: {' '.join(COLOR)})",
                    idx + 1, col,
                )
            name, args = words[0], words[1:]
            if blend and name not in SOURCES:
                raise VisError(f"'{blend}' only works before a source", idx + 1, col)
            defaults = ALL[name]
            if len(args) > len(defaults):
                raise VisError(f"'{name}' takes at most {len(defaults)} values", idx + 1, col)
            compiled = []
            for a in args:
                try:
                    compiled.append(compile(a, "<vis>", "eval"))
                except SyntaxError:
                    raise VisError(f"bad expression '{a}' (no spaces inside expressions)",
                                   idx + 1, raw.index(a, col))
            for d in defaults[len(args):]:
                compiled.append(compile(repr(d), "<vis>", "eval"))
            ops.append(Op(name, blend, compiled))
        except VisError as e:
            errors.append(e)
    if errors:
        raise VisErrorList(errors)
    return ops


class VisErrorList(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(map(str, errors)))
        self.errors = errors


# ---------------------------------------------------------------- render


def _rgb(v, offset):
    return np.stack([0.5 + 0.5 * np.sin(v), 0.5 + 0.5 * np.sin(v + offset * 2),
                     0.5 + 0.5 * np.sin(v + offset * 4)], axis=-1)


def _gray(v):
    return np.repeat(v[..., None], 3, axis=-1)


class Renderer:
    def __init__(self):
        self.ops: list[Op] | None = None  # None until visuals code arrives: a dark screen
        self.prev = None

    @property
    def piped(self) -> bool:
        return any(op.name == "screen" for op in self.ops or [])

    @property
    def hint(self) -> str:
        """Why the screen is dark, if it is."""
        if self.ops is None:
            return "no visuals yet: write them in the .vis file and evaluate"
        return "" if self.piped else "visuals aren't piped to the screen:  vis | screen"

    def render(self, w: int, h: int, env: Env) -> np.ndarray:
        """Returns float image (h, w, 3) in 0..1. h is in pixels (2 per text row)."""
        if not self.piped:
            self.prev = None
            return np.zeros((h, w, 3))
        def val(op):
            out = []
            for c in op.args:
                try:
                    out.append(float(eval(c, {"__builtins__": {}}, env)))
                except Exception:
                    out.append(0.0)
            return out

        t = env["t"]
        aspect = w / max(h, 1)
        x = np.linspace(-aspect, aspect, w)[None, :].repeat(h, 0)
        y = np.linspace(-1, 1, h)[:, None].repeat(w, 1)

        # geometry first: it bends the space every source is drawn into
        for op in self.ops:
            if op.name not in GEOMETRY:
                continue
            a = val(op)
            if op.name == "kaleid":
                n = max(a[0], 1)
                r, th = np.hypot(x, y), np.arctan2(y, x)
                seg = 2 * math.pi / n
                th = np.abs((th % seg) - seg / 2)
                x, y = r * np.cos(th), r * np.sin(th)
            elif op.name == "rotate":
                ang = a[0] + t * a[1]
                c, s = math.cos(ang), math.sin(ang)
                x, y = x * c - y * s, x * s + y * c
            elif op.name == "scale":
                k = a[0] if a[0] != 0 else 1e-3
                x, y = x / k, y / k
            elif op.name == "repeat":
                nx, ny = max(a[0], 1), max(a[1], 1)
                x = ((x * nx + 1) % 2) - 1
                y = ((y * ny + 1) % 2) - 1
            elif op.name == "scroll":
                x = x + t * a[0]
                y = y + t * a[1]
            elif op.name == "warp":
                x, y = x + a[0] * np.sin(y * a[1] + t), y + a[0] * np.cos(x * a[1] + t)
            elif op.name == "pixelate":
                n = max(a[0], 1)
                x, y = np.floor(x * n) / n, np.floor(y * n) / n

        img = None
        for op in self.ops:
            a = val(op)
            if op.name in SOURCES:
                src = self._source(op.name, a, x, y, t)
                if img is None:
                    img = src
                elif op.blend == "mult":
                    img = img * src
                elif op.blend == "diff":
                    img = np.abs(img - src)
                else:
                    img = img + src
            elif op.name in COLOR:
                if img is None:
                    img = np.zeros((h, w, 3))
                img = self._color(op.name, a, img)
        if img is None:
            img = np.zeros((h, w, 3))
        img = np.clip(img, 0, 1)
        self.prev = img
        return img

    @staticmethod
    def _source(name, a, x, y, t):
        if name == "osc":
            return _rgb((x * a[0] + t * a[1] * 2 * math.pi), a[2])
        if name == "noise":
            s, sp = a[0], a[1]
            v = (np.sin(x * s * 1.7 + t * sp * 3) * np.sin(y * s * 2.3 - t * sp * 2)
                 + np.sin((x + y) * s * 0.9 + t * sp) * 0.6
                 + np.sin(np.hypot(x - 0.3, y) * s * 3.1 - t * sp * 4) * 0.4)
            return _gray(0.5 + 0.3 * v)
        if name == "shape":
            n, r, soft = max(a[0], 3), a[1], max(a[2], 1e-3)
            th = np.arctan2(y, x)
            seg = 2 * math.pi / n
            d = np.hypot(x, y) * np.cos((th % seg) - seg / 2) / math.cos(seg / 2)
            return _gray(np.clip((r - d) / soft, 0, 1))
        if name == "rings":
            return _gray(0.5 + 0.5 * np.sin(np.hypot(x, y) * a[0] * 2 * math.pi - t * a[1] * 2 * math.pi))
        if name == "grid":
            n, wdt = a[0], a[1]
            gx = np.abs(((x * n) % 1) - 0.5) > 0.5 - wdt
            gy = np.abs(((y * n) % 1) - 0.5) > 0.5 - wdt
            return _gray((gx | gy).astype(float))
        if name == "solid":
            return np.broadcast_to(np.array(a[:3]), x.shape + (3,)).copy()
        raise KeyError(name)

    def _color(self, name, a, img):
        if name == "color":
            return img * np.array(a[:3])
        if name == "invert":
            return img * (1 - a[0]) + (1 - img) * a[0]
        if name == "bright":
            return img + a[0]
        if name == "contrast":
            return (img - 0.5) * a[0] + 0.5
        if name == "hue":
            ang = a[0] * 2 * math.pi
            c, s = math.cos(ang), math.sin(ang)
            k = 1 / 3
            sq = math.sqrt(k)
            m = np.array([
                [c + (1 - c) * k, k * (1 - c) - sq * s, k * (1 - c) + sq * s],
                [k * (1 - c) + sq * s, c + k * (1 - c), k * (1 - c) - sq * s],
                [k * (1 - c) - sq * s, k * (1 - c) + sq * s, c + k * (1 - c)],
            ])
            return img @ m.T
        if name == "thresh":
            return (img > a[0]).astype(float)
        if name == "posterize":
            n = max(a[0], 1)
            return np.floor(img * n) / n
        if name == "feedback":
            if self.prev is not None and self.prev.shape == img.shape:
                return np.maximum(img, self.prev * a[0])
            return img
        return img


def to_ansi(img: np.ndarray) -> list[str]:
    """Pack two pixel rows into one text row using the upper-half block."""
    q = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    rows = []
    for top, bot in zip(q[0::2].tolist(), q[1::2].tolist()):
        rows.append("".join(
            f"\x1b[38;2;{a[0]};{a[1]};{a[2]};48;2;{b[0]};{b[1]};{b[2]}m▀"
            for a, b in zip(top, bot)
        ) + "\x1b[0m")
    return rows


def blank(cols: int, rows: int, text: str) -> list[str]:
    """An empty screen with one dim line of text in the middle."""
    out = ["\x1b[0m" + " " * cols] * rows
    pad = max((cols - len(text)) // 2, 0)
    out[rows // 2] = "\x1b[0m" + " " * pad + f"\x1b[2m{text}\x1b[0m" + " " * max(cols - pad - len(text), 0)
    return out
