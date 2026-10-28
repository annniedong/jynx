"""jynx engine: audio + visuals, driven by Neovim over a local TCP socket.

Protocol (one JSON object per line):
  editor -> engine   {"type": "music"|"vis", "text": "..."}   {"type": "hush", "on": true}
  keys   -> engine   {"type": "down", "track": "keys", "key": "a", "n": 0, "octave": 4, "vel": .8, "hold": null}
                     {"type": "down", "track": "keys", "key": "1", "sound": "bd"}
                     {"type": "up", "track": "keys", "key": "a"}
  engine -> clients  {"type": "ok"|"error", "buffer": ..., "errors": [...]}
                     {"type": "tracks", "names": [...]}
                     {"type": "cycle", "n": 12}
                     {"type": "hits", "hits": [[line, col0, col1, seconds], ...]}
                     {"type": "played", "key": "a", "midi": 60, "n": "0", "pos": 12.37}
  screen -> engine   {"type": "watch"}   (jynx screen: draws the visuals in its own terminal)
  engine -> screens  {"type": "vis_code", "text": "..."}
                     {"type": "frame", "levels": {...}, "t": 12.3, "cycle": 6.1}
"""
from __future__ import annotations

import heapq
import itertools
import json
import os
import shutil
import socket
import sys
import threading
import time

import sounddevice as sd

from . import audio, lang, vis

PORT = int(os.environ.get("JYNX_PORT", "7400"))


class Hub:
    """Fan messages out to connected editors (or screens), each at its scheduled time."""

    def __init__(self):
        self.clients: list[socket.socket] = []
        self.screens: list[socket.socket] = []
        self.queue: list = []
        self.seq = itertools.count()
        self.cv = threading.Condition()

    def post(self, msg: dict, delay: float = 0.0, screens: bool = False):
        with self.cv:
            heapq.heappush(self.queue, (time.monotonic() + delay, next(self.seq), msg, screens))
            self.cv.notify()

    def run(self):
        while True:
            with self.cv:
                while not self.queue or self.queue[0][0] > time.monotonic():
                    wait = self.queue[0][0] - time.monotonic() if self.queue else None
                    self.cv.wait(wait)
                _, _, msg, screens = heapq.heappop(self.queue)
                clients = list(self.screens if screens else self.clients)
            data = (json.dumps(msg) + "\n").encode()
            for c in clients:
                try:
                    c.sendall(data)
                except OSError:
                    self.drop(c)

    def drop(self, conn):
        with self.cv:
            for group in (self.clients, self.screens):
                if conn in group:
                    group.remove(conn)


class App:
    def __init__(self):
        self.hub = Hub()
        self.engine = audio.Engine(self.hub.post)
        self.renderer = vis.Renderer()
        self.errors = {"music": None, "vis": None}
        self.flash = {"music": 0.0, "vis": 0.0}
        self.vis_text = ""
        self.t0 = time.monotonic()

    # ------------------------------------------------------------ messages

    def handle(self, msg: dict):
        kind = msg.get("type")
        if kind == "music":
            try:
                prog = lang.parse(msg.get("text", ""))
            except lang.ErrorList as e:
                return self.fail("music", e.errors)
            self.engine.set_program(prog)
            self.succeed("music")
            self.hub.post({"type": "tracks", "names": list(prog.tracks)})
        elif kind == "vis":
            try:
                ops = vis.parse(msg.get("text", ""))
            except vis.VisErrorList as e:
                return self.fail("vis", e.errors)
            self.renderer.ops = ops
            self.vis_text = msg.get("text", "")
            self.hub.post({"type": "vis_code", "text": self.vis_text}, screens=True)
            self.succeed("vis")
        elif kind == "hush":
            self.engine.hush(bool(msg.get("on")))
        elif kind == "down":
            track = str(msg.get("track") or "keys")
            midi, label = msg.get("midi"), None
            if msg.get("n") is not None:  # a = 0, w = 1, ...: through the track's .scale()
                midi, label = self.engine.key_note(self.engine.track_for(track), int(msg["n"]),
                                                   int(msg.get("octave", 4)))
                if midi is None:
                    return
            pos = self.engine.key_down(track, msg.get("key"), midi=midi,
                                       sound=msg.get("sound"), vel=float(msg.get("vel", 0.8)),
                                       hold=msg.get("hold"))
            self.hub.post({"type": "played", "key": msg.get("key"), "midi": midi, "n": label,
                           "sound": msg.get("sound"), "pos": pos})
        elif kind == "up":
            self.engine.key_up(str(msg.get("track") or "keys"), msg.get("key"))
        elif kind == "hello":
            self.hub.post({"type": "tracks", "names": list(self.engine.live.tracks)})

    def succeed(self, buf):
        self.errors[buf] = None
        self.flash[buf] = time.monotonic()
        self.hub.post({"type": "ok", "buffer": buf})

    def fail(self, buf, errors):
        self.errors[buf] = f"line {errors[0].line}: {errors[0]}"
        self.hub.post({"type": "error", "buffer": buf, "errors": [
            {"line": e.line, "col": e.col, "msg": str(e)} for e in errors]})

    def serve_client(self, conn: socket.socket):
        with self.hub.cv:
            self.hub.clients.append(conn)
        buf = b""
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        try:
                            msg = json.loads(line)
                            if msg.get("type") == "watch":
                                with self.hub.cv:  # this client is a screen, not an editor
                                    self.hub.clients.remove(conn)
                                    self.hub.screens.append(conn)
                                if self.renderer.ops is not None:
                                    self.hub.post({"type": "vis_code", "text": self.vis_text}, screens=True)
                                continue
                            self.handle(msg)
                        except Exception as e:  # never let one bad message kill the engine
                            self.errors["music"] = f"engine: {e}"
        except OSError:
            pass
        finally:
            self.hub.drop(conn)
            conn.close()

    def bind(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", PORT))
        srv.listen()
        return srv

    def serve(self, srv):
        while True:
            conn, _ = srv.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            threading.Thread(target=self.serve_client, args=(conn,), daemon=True).start()

    # ------------------------------------------------------------ screen

    def status_lines(self, cols: int) -> list[str]:
        eng = self.engine
        dots = []
        for name, lvl in sorted(eng.levels().items()):
            if name not in eng.live.tracks:
                continue
            g = int(80 + 175 * lvl)
            dots.append(f"\x1b[38;2;{g};{int(g * 0.55)};{int(255 - lvl * 120)}m●\x1b[0m {name}")
        cyc = eng.cycle_pos
        info = (f"cycle {int(cyc):>3}.{int((cyc % 1) * 4) + 1}  cpm {eng.live.cps * 60:.4g} ({eng.live.cps * 240:.4g} bpm)  "
                f"cpu {eng.load * 100:.0f}%  xruns {eng.xruns}")
        if eng.hushed:
            info = "\x1b[33mHUSH\x1b[0m  " + info
        line1 = "  ".join(dots) or "\x1b[2m(no tracks)\x1b[0m"
        err = self.errors["music"] or self.errors["vis"]
        now = time.monotonic()
        if err:
            line2 = f"\x1b[31m✗ {err}\x1b[0m"[: cols + 9]
        elif now - max(self.flash.values()) < 0.25:
            line2 = f"\x1b[32m✓ evaluated\x1b[0m   \x1b[2m{info}\x1b[0m"
        else:
            line2 = f"\x1b[2m{info}\x1b[0m"
        return [" " + line1, " " + line2]

    def env(self) -> vis.Env:
        env = vis.Env(vis.MATH)
        env.update(self.engine.levels())
        env["t"] = time.monotonic() - self.t0
        env["cycle"] = self.engine.cycle_pos
        return env

    def frame_loop(self):
        """Feed connected screens the numbers the visuals react to."""
        while True:
            if self.hub.screens:
                env = self.env()
                self.hub.post({"type": "frame", "levels": self.engine.levels(),
                               "t": env["t"], "cycle": env["cycle"]}, screens=True)
            time.sleep(1 / 30)

    def draw_loop(self):
        out = sys.stdout
        out.write("\x1b[?25l\x1b[2J")
        last_size = None
        frame_time = 1 / 24
        while True:
            start = time.monotonic()
            cols, rows = shutil.get_terminal_size((80, 24))
            screens = len(self.hub.screens)
            if (cols, rows, screens > 0) != last_size:
                out.write("\x1b[2J")
                self.renderer.prev = None
                last_size = (cols, rows, screens > 0)
            if screens:  # the visuals are on their own screen: just show the status here
                note = f"\x1b[2mvisuals are showing on {screens} jynx screen{'s' * (screens > 1)}\x1b[0m"
                out.write("\x1b[H\n " + note + "\n\n" +
                          "\n".join("\x1b[2K" + s for s in self.status_lines(cols)))
                out.flush()
                time.sleep(max(frame_time - (time.monotonic() - start), 0.001))
                continue
            img_rows = max(rows - 2, 1)
            img = self.renderer.render(cols, img_rows * 2, self.env())
            rows_txt = vis.to_ansi(img)
            if self.renderer.hint:
                rows_txt = vis.blank(cols, img_rows, self.renderer.hint)
            status = self.status_lines(cols)
            out.write("\x1b[H" + "\n".join(rows_txt) + "\n" +
                      "\n".join("\x1b[2K" + s for s in status))
            out.flush()
            time.sleep(max(frame_time - (time.monotonic() - start), 0.001))


class SilentClock:
    """Stand-in for the sound card (JYNX_NO_AUDIO=1): same timing, no output."""

    class _Info:
        outputBufferDacTime = 0.0
        currentTime = 0.0

    class _Status:
        output_underflow = False

    def __init__(self, callback):
        import numpy as np
        self.callback = callback
        self.buf = np.zeros((audio.BLOCK, 2), dtype="float32")
        self.running = False

    def _run(self):
        period = audio.BLOCK / audio.SR
        next_t = time.monotonic()
        while self.running:
            self.callback(self.buf, audio.BLOCK, self._Info, self._Status)
            next_t += period
            time.sleep(max(next_t - time.monotonic(), 0))

    def __enter__(self):
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.running = False


def main():
    sys.setswitchinterval(0.001)  # keep the audio thread snappy while we draw
    app = App()
    try:
        srv = app.bind()
    except OSError as e:
        print(f"jynx: port {PORT} is busy (is another engine running?): {e}")
        return
    threading.Thread(target=app.serve, args=(srv,), daemon=True).start()
    threading.Thread(target=app.hub.run, daemon=True).start()
    threading.Thread(target=app.frame_loop, daemon=True).start()
    if os.environ.get("JYNX_NO_AUDIO"):
        stream = SilentClock(app.engine.callback)
    else:
        stream = sd.OutputStream(samplerate=audio.SR, blocksize=audio.BLOCK, channels=2,
                                 dtype="float32", latency=0.06, callback=app.engine.callback)
    try:
        with stream:
            app.draw_loop()
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[0m\x1b[?25h\x1b[2J\x1b[H")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
