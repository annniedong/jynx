"""Laptop keyboard -> instrument.  ./jynx keys [pattern]   (or <Space>k in the editor)

It plays one of your code's patterns: its sound (.s) and its effects (.lpf, .room,
...), so the code defines the instrument and the keyboard performs it.

     w e   t y u   o p          1 2 3 4 5 6 7 8 9 0   drums
    a s d f g h j k l ; '       z x    octave down / up
    C D E F G A B C D E F       c v    softer / louder
                                Tab    next pattern
                                Enter  copy what you played as a pattern
                                Bksp   forget what you played
                                Esc    quit

In terminals that support the kitty keyboard protocol (Ghostty, kitty, WezTerm,
foot) notes sound for as long as you hold the key. Elsewhere they play a fixed
length ([ and ] change it).
"""
from __future__ import annotations

import json
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import termios
import threading
import time
import tty

PORT = int(os.environ.get("JYNX_PORT", "7400"))

NOTE_KEYS = "awsedftgyhujkolp;'"  # a = C, w = C#, s = D, ...
DRUM_KEYS = dict(zip("1234567890", ["bd", "sd", "hh", "oh", "cp", "rim", "lt", "mt", "ht", "cr"]))
NAMES = ["c", "c#", "d", "d#", "e", "f", "f#", "g", "g#", "a", "a#", "b"]

KITTY_RE = re.compile(rb"\x1b\[(\d+)(?::\d*)*(?:;(\d+)(?::(\d+))?)?u")
OTHER_CSI_RE = re.compile(rb"\x1b\[[0-9;:?]*[A-Za-z~]")


def note_name(midi: int) -> str:
    return f"{NAMES[midi % 12]}{midi // 12 - 1}"


def phrase_pattern(events: list[tuple[float, str]]) -> str:
    """[(cycle position, 'c4'|'bd'), ...] -> mini-notation, quantized to the cycle grid."""
    if not events:
        return ""
    notes = [e for e in events if e[1] not in DRUM_KEYS.values()]
    events = notes or events  # a note() pattern can't hold drums, so prefer notes
    cycles: dict[int, dict[int, list[str]]] = {}
    for pos, label in events:
        k = int(pos)
        idx = round((pos - k) * 16)
        if idx == 16:  # a hair early for the next cycle's downbeat
            k, idx = k + 1, 0
        cycles.setdefault(k, {}).setdefault(idx, []).append(label)
    first, last = min(cycles), max(cycles)
    if last - first >= 4:  # keep the last four cycles
        first = last - 3
    out = []
    for k in range(first, last + 1):
        slots = cycles.get(k, {})
        grid = next(g for g in (1, 2, 4, 8, 16) if all(i % (16 // g) == 0 for i in slots))
        steps = []
        for s in range(grid):
            labels = list(dict.fromkeys(slots.get(s * (16 // grid), [])))
            steps.append("~" if not labels else labels[0] if len(labels) == 1
                         else "[" + ",".join(labels) + "]")
        out.append(" ".join(steps))
    return out[0] if len(out) == 1 else "<" + " ".join(f"[{c}]" for c in out) + ">"


class Keys:
    def __init__(self, track: str):
        self.track = track
        self.tracks: list[str] = []
        self.octave = 4
        self.vel = 0.8
        self.hold = 0.35
        self.kitty = False
        self.down: dict[str, int | str] = {}  # key -> midi or drum name currently held
        self.flash: dict[str, float] = {}  # key -> time pressed (for fixed-length mode)
        self.played: list[tuple[float, str]] = []
        self.captured = ""
        self.message = ""
        self.cycle = 0
        self.sock: socket.socket | None = None
        self.lock = threading.Lock()
        self.dirty = True

    # ------------------------------------------------------------ engine link

    def connect(self):
        self.sock = socket.create_connection(("127.0.0.1", PORT), timeout=2)
        self.sock.settimeout(None)
        threading.Thread(target=self.listen, daemon=True).start()
        self.send({"type": "hello"})

    def send(self, msg):
        try:
            self.sock.sendall((json.dumps(msg) + "\n").encode())
        except OSError:
            self.message = "lost the engine connection"

    def listen(self):
        buf = b""
        while True:
            try:
                chunk = self.sock.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                with self.lock:
                    self.on_message(msg)
        with self.lock:
            self.message = "engine stopped"
            self.dirty = True

    def on_message(self, msg):
        t = msg.get("type")
        if t == "tracks":
            self.tracks = msg.get("names", [])
            self.dirty = True
        elif t == "cycle":
            self.cycle = msg.get("n", 0)
            self.dirty = True
        elif t == "played":
            label = note_name(msg["midi"]) if msg.get("midi") is not None else msg.get("sound")
            if label:
                self.played.append((msg.get("pos", 0.0), label))
                self.played = self.played[-64:]
                self.dirty = True

    # ------------------------------------------------------------ keys

    def press(self, ch: str, held: bool | None = None):
        held = self.kitty if held is None else held
        if ch in DRUM_KEYS:
            self.send({"type": "down", "track": self.track, "key": ch, "sound": DRUM_KEYS[ch],
                       "vel": self.vel})
            self.flash[ch] = time.monotonic()
        elif ch in NOTE_KEYS:
            if ch in self.down:
                return
            midi = 12 * (self.octave + 1) + NOTE_KEYS.index(ch)
            msg = {"type": "down", "track": self.track, "key": ch, "midi": midi, "vel": self.vel}
            if not held:
                msg["hold"] = self.hold
                self.flash[ch] = time.monotonic()
            else:
                self.down[ch] = midi
            self.send(msg)
        elif ch == "z":
            self.octave = max(self.octave - 1, 1)
        elif ch == "x":
            self.octave = min(self.octave + 1, 7)
        elif ch == "c":
            self.vel = round(max(self.vel - 0.1, 0.1), 1)
        elif ch == "v":
            self.vel = round(min(self.vel + 0.1, 1.0), 1)
        elif ch == "[":
            self.hold = round(max(self.hold - 0.05, 0.05), 2)
        elif ch == "]":
            self.hold = round(min(self.hold + 0.05, 3.0), 2)
        elif ch == "\t":
            names = self.tracks or [self.track]
            i = names.index(self.track) if self.track in names else -1
            self.track = names[(i + 1) % len(names)]
        elif ch == "\r":
            self.capture()
        elif ch == "\x7f":
            self.played.clear()
            self.captured = ""
            self.message = "forgot the phrase"
        self.dirty = True

    def release(self, ch: str):
        if ch in self.down:
            del self.down[ch]
            self.send({"type": "up", "track": self.track, "key": ch})
            self.dirty = True

    def capture(self):
        self.captured = phrase_pattern(self.played)
        if not self.captured:
            self.message = "play something first"
            return
        try:
            subprocess.run(["pbcopy"], input=self.captured.encode(), check=True)
            self.message = "copied — in the editor, put the cursor on a pattern and press <Space>p"
        except (OSError, subprocess.CalledProcessError):
            self.message = "copy failed — type it in by hand"
        self.played.clear()

    # ------------------------------------------------------------ screen

    def cap(self, ch: str, label: str) -> str:
        lit = ch in self.down or time.monotonic() - self.flash.get(ch, 0) < 0.15
        return f"\x1b[7;1m {label} \x1b[0m" if lit else f"\x1b[2m[\x1b[0m{label}\x1b[2m]\x1b[0m"

    def draw(self):
        cols = shutil.get_terminal_size((80, 24)).columns
        black = "    " + self.cap("w", "w") + self.cap("e", "e") + "   " + "".join(
            self.cap(c, c) for c in "tyu") + "   " + self.cap("o", "o") + self.cap("p", "p")
        white = "  " + "".join(self.cap(c, c) for c in "asdfghjkl;'")
        names = "   " + "  ".join(note_name(12 * (self.octave + 1) + NOTE_KEYS.index(c))[:-1].upper()
                                 .ljust(1) for c in "asdfghjkl;'")
        drums = "  " + " ".join(self.cap(k, f"{k} {v}") for k, v in DRUM_KEYS.items())
        mode = ("\x1b[32mheld notes\x1b[0m" if self.kitty
                else f"\x1b[33mfixed {self.hold:g}s\x1b[0m ([ ] to change)")
        beat = "●" if self.cycle % 2 == 0 else "○"
        phrase = " ".join(lbl for _, lbl in self.played[-12:])
        lines = [
            f" \x1b[1;45m KEYS \x1b[0m → \x1b[1m{self.track}\x1b[0m   octave {self.octave} (z x)"
            f"   velocity {self.vel:g} (c v)   {mode}   {beat}",
            "",
            black,
            white,
            "\x1b[2m" + names + "\x1b[0m",
            drums,
            "",
            f" \x1b[2mplayed:\x1b[0m {phrase}" if phrase else
            " \x1b[2mplay something, then Enter turns it into a pattern\x1b[0m",
            f" \x1b[2mcaptured:\x1b[0m \x1b[35m\"{self.captured}\"\x1b[0m" if self.captured else "",
            f" \x1b[2m{self.message}\x1b[0m" if self.message else
            " \x1b[2mTab next pattern · Enter capture · Bksp forget · Esc quit\x1b[0m",
        ]
        out = "\x1b[H" + "".join(l[: cols * 4] + "\x1b[K\r\n" for l in lines) + "\x1b[J"
        sys.stdout.write(out)
        sys.stdout.flush()
        self.dirty = False


def detect_kitty(fd) -> bool:
    """Ask the terminal whether it speaks the kitty keyboard protocol."""
    os.write(sys.stdout.fileno(), b"\x1b[?u\x1b[c")
    reply, deadline = b"", time.monotonic() + 0.4
    while time.monotonic() < deadline:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            reply += os.read(fd, 1024)
            if re.search(rb"\x1b\[\?[\d;]*c", reply):
                break
    return re.search(rb"\x1b\[\?\d+u", reply) is not None


def main():
    track = sys.argv[1] if len(sys.argv) > 1 else "keys"
    k = Keys(track)
    try:
        k.connect()
    except OSError:
        print("jynx keys: no engine running. Start ./jynx (or ./jynx engine) first.")
        return
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setraw(fd)
    try:
        k.kitty = detect_kitty(fd)
        if k.kitty:
            # 1 disambiguate + 2 report press/release + 8 every key as an escape code
            os.write(sys.stdout.fileno(), b"\x1b[>11u")
        sys.stdout.write("\x1b[?25l\x1b[2J")
        while True:
            with k.lock:
                if k.dirty or k.flash:
                    k.draw()
                    now = time.monotonic()
                    k.flash = {c: t for c, t in k.flash.items() if now - t < 0.2}
            r, _, _ = select.select([fd], [], [], 0.05)
            if not r:
                continue
            data = os.read(fd, 1024)
            with k.lock:
                if not handle_input(k, data):
                    break
    finally:
        if k.kitty:
            os.write(sys.stdout.fileno(), b"\x1b[<u")
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.write("\x1b[0m\x1b[?25h\x1b[2J\x1b[H")
        sys.stdout.flush()


def handle_input(k: Keys, data: bytes) -> bool:
    """Returns False to quit."""
    if data in (b"\x1b", b"\x03", b"\x04"):
        return False
    if k.kitty and KITTY_RE.search(data):
        for m in KITTY_RE.finditer(data):
            code = int(m.group(1))
            mods = int(m.group(2) or 1) - 1
            event = int(m.group(3) or 1)
            if code == 27 or (mods & 4 and code in (99, 100)):  # Esc, Ctrl-C, Ctrl-D
                return False
            ch = {13: "\r", 9: "\t", 127: "\x7f"}.get(code, chr(code) if code < 0x110000 else "")
            if event == 3:
                k.release(ch)
            elif event == 1:
                k.press(ch)
        return True
    # plain bytes (no kitty protocol, or input that bypassed it): no key-up events
    data = OTHER_CSI_RE.sub(b"", data)  # arrow keys etc.
    for ch in data.decode(errors="ignore"):
        if ch in ("\x03", "\x04"):
            return False
        k.press(ch, held=False)
    return True


if __name__ == "__main__":
    main()
