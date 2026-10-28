"""Visuals in a terminal of their own.  ./jynx screen

Connects to a running engine, which sends the current .vis code and, 30 times
a second, the numbers the visuals react to (track levels, t, cycle). The
picture is drawn here, at this terminal's size, so the engine's terminal can
stay small. Ctrl-C quits; if the engine goes away, the screen waits for it.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import threading
import time

from . import vis

PORT = int(os.environ.get("JYNX_PORT", "7400"))


class Screen:
    def __init__(self):
        self.renderer = vis.Renderer()
        self.frame: dict | None = None
        self.cv = threading.Condition()
        self.connected = False

    def listen(self, sock: socket.socket):
        buf = b""
        while True:
            try:
                chunk = sock.recv(65536)
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
                with self.cv:
                    if msg.get("type") == "vis_code":
                        try:
                            self.renderer.ops = vis.parse(msg.get("text", ""))
                        except vis.VisErrorList:
                            pass  # the engine only sends code that parsed
                    elif msg.get("type") == "frame":
                        self.frame = msg
                        self.cv.notify()
        with self.cv:
            self.connected = False
            self.frame = None
            self.cv.notify()

    def connect(self):
        while True:
            try:
                sock = socket.create_connection(("127.0.0.1", PORT), timeout=2)
                break
            except OSError:
                self.message("waiting for the jynx engine…  (start it with ./jynx engine)")
                time.sleep(1)
        sock.settimeout(None)
        sock.sendall(b'{"type": "watch"}\n')
        self.connected = True
        threading.Thread(target=self.listen, args=(sock,), daemon=True).start()

    def message(self, text: str):
        cols, rows = shutil.get_terminal_size((80, 24))
        sys.stdout.write("\x1b[2J" + f"\x1b[{rows // 2};{max((cols - len(text)) // 2, 1)}H" +
                         f"\x1b[2m{text}\x1b[0m")
        sys.stdout.flush()

    def run(self):
        out = sys.stdout
        out.write("\x1b[?25l\x1b[2J")
        last_size = None
        while True:
            if not self.connected:
                last_size = None
                self.connect()
            with self.cv:
                self.cv.wait(0.5)
                frame = self.frame
                self.frame = None
                if frame is None:
                    continue
                cols, rows = shutil.get_terminal_size((80, 24))
                if (cols, rows) != last_size:
                    out.write("\x1b[2J")
                    self.renderer.prev = None
                    last_size = (cols, rows)
                env = vis.Env(vis.MATH)
                env.update(frame.get("levels", {}))
                env["t"] = frame.get("t", 0.0)
                env["cycle"] = frame.get("cycle", 0.0)
                img = self.renderer.render(cols, rows * 2, env)
                hint = self.renderer.hint
            lines = vis.blank(cols, rows, hint) if hint else vis.to_ansi(img)
            out.write("\x1b[H" + "\n".join(lines))
            out.flush()


def main():
    try:
        Screen().run()
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[0m\x1b[?25h\x1b[2J\x1b[H")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
