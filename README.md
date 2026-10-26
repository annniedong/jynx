# jynx

A livecoding language for music and visuals, designed around vim.
The music is written in [Strudel](https://strudel.cc) syntax. Everything runs
in the terminal: Neovim is the editor, and a Python engine synthesizes the
sound and draws the visuals as colored half-block characters.

```
┌─ live.jynx ──────────────────────────────┬─ ENGINE ───────────────────────┐
│ setcpm(118/4)                             │ ▀▄▀▀▄▄▀▄  ░▒▓█▓▒░  ▄▀▄▀▀▄▄▀▄   │
│ drums: s("bd*2 [~ sd] <hh*4 hh*8> ~")     │ ▄▀▄▄▀▀▄▀  ▒▓████▓▒  ▀▄▀▄▄▀▀▄   │
│   .gain(.9)  ██████░░░░                   │      (visuals react to the     │
│ bass: note("<c2 eb2 g1 bb1>*2 [c2 c3]")   │       music in real time)      │
│   .lpf(slider(700, 100, 4000)) ◀ ███░ ▶   │                                │
├─ live.vis ────────────────────────────────┤ ● bass  ● drums  ● hats        │
│ osc 8+bass*20 .15 .9                      │ cycle 12.3  cpm 29.5 (118 bpm) │
├─ KEYS → bass ─────────────────────────────┤                                │
│  [w][e]   [t][y][u]   [o][p]              │                                │
│ [a][s][d][f][g][h][j][k][l][;][']         │                                │
└───────────────────────────────────────────┴────────────────────────────────┘
```

## Why vim?

Livecoding means editing text during a performance. Vim's editing grammar
(*operator + count + motion*) already works like a small composition language.
jynx gives vim's existing commands musical meaning and adds a few new ones.

- **The text is the music.** Strudel style already puts one effect on each
  indented `.method()` line, and that's exactly the shape vim is good at:
  - `dd` removes an effect and `p` puts it back.
  - `yap` copies a whole pattern.
  - `}` jumps to the next pattern.
  - `gcc` mutes a line.
- **Modes mean something.** In normal mode, every change is heard, lined up to
  the next cycle. Insert mode is a silent rehearsal space: you hear nothing
  until you press `<Esc>`.
- **Macros become generative music.** `:Every 4 @q` runs a recorded macro
  every 4 cycles, so the code rewrites itself while you play over it.
- **New vim-style commands for patterns:** step text objects, rotate and
  reverse operators, a KNOB mode for turning numbers like a dial, and a
  laptop-keyboard instrument.

## Run it

```bash
cd ~/Developer/jynx
./jynx            # opens live.jynx + live.vis (created from templates)
./jynx mysong     # opens mysong.jynx + mysong.vis
```

Use a terminal with truecolor support: Ghostty, iTerm2, kitty, WezTerm, or
macOS Terminal on macOS 26. Make the window large, because the visuals fill
the right half.

Setup, which has already been done on this machine:

```bash
brew install neovim
python3 -m venv .venv && .venv/bin/pip install numpy scipy sounddevice
```

`JYNX_NO_AUDIO=1 ./jynx` runs with the clock and visuals but no sound, as a
backup for when the audio device is being difficult.

### More terminals

```bash
./jynx engine        # audio + full-screen visuals (put this on the projector)
./jynx               # just the editor, if an engine is already running
./jynx keys bass     # the laptop keyboard plays the `bass` pattern
./jynx stop          # stop a leftover engine (e.g. "port is busy")
```

If an engine is already running, `./jynx` opens only the editor and connects
to it. Otherwise it opens the usual split layout.

---

## The music language (`.jynx`): Strudel syntax

```js
setcpm(118/4)                             // tempo: cycles per minute (118 bpm in 4/4)

drums: s("bd*2 [~ sd] <hh*4 hh*8> ~")     // name: pattern
  .gain(.9)                               // one method per line, indented
  .room(.15)

bass: note("<c2 eb2 g1 bb1>*2 [c2 c3]")
  .s("supersaw")
  .lpf(slider(700, 100, 4000))            // slider() = a knob with its own range
  .lpq("<2 8>")                           // arguments can be patterns too

_chords: n("<[0,2,4] [-1,1,3]>").scale("C4:minor")   // a leading _ mutes it
  .s("bell")
```

jynx parses a subset of Strudel. Code written within this subset should also
run on strudel.cc. The exceptions are jynx's extra synths `bell`, `pluck` and
`sub`, which strudel.cc doesn't have.

- **Pattern starts:** a pattern begins with `s("...")` / `sound("...")` for
  drums and synth names, `note("...")` for note names or midi numbers, or
  `n("...")` with `.scale()`.
- **Labels:** each pattern has a label, either `name:` or `$:` for an unnamed
  one. Names show up in the visuals as live levels (`bass`, `drums`, ...).
  Unnamed patterns become `p1`, `p2`, ...
- **Muting:** add `_` in front of the label (`<Space>_` toggles it), or comment
  the first line out with `gcc`. Either one silences the whole block.
- **Globals:** `setcpm(n)`, `setcps(n)`, `hush()`.

### Mini-notation (inside the strings)

| syntax | meaning |
|---|---|
| `bd sd` | sequence: the steps share the cycle |
| `~` or `-` | rest |
| `[bd bd]` | group: subdivide one step |
| `[c3,e3,g3]` | stack: play together (chords) |
| `<c3 e3 g3>` | alternate: one per cycle |
| `bd*2` / `bd/2` | faster / slower |
| `bd!3` | replicate: `bd bd bd` |
| `c3@3 e3` / `c3 _ _ e3` | weight / hold: c3 lasts 3 steps |
| `bd(3,8)` / `bd(3,8,2)` | euclidean rhythm (and rotation) |
| `bd?` | randomly drop it half the time |

**Sounds:**
- Drums: `bd sd hh oh cp rim lt mt ht cb cr`
- Synths: `sawtooth square triangle sine supersaw bell pluck sub`
- Variants like `bd:3` are accepted, but they all play the one built-in kit.

Everything is synthesized, so there are no sample files.

### Methods

| method | meaning (Strudel's units) |
|---|---|
| `.s("sawtooth")` | sound for `note()` / `n()` patterns (can be a pattern: `"<sawtooth square>"`) |
| `.gain()` `.velocity()` | volume |
| `.lpf()` `.hpf()` `.lpq()` | filters in Hz, resonance (aliases: `cutoff`, `hcutoff`, `resonance`) |
| `.room()` | reverb amount, 0–1 |
| `.delay()` `.delaytime()` `.delayfeedback()` | echo: wet amount, seconds, feedback |
| `.crush()` | bit depth (lower = dirtier), 1–16 |
| `.pan()` | 0 = left, .5 = center, 1 = right |
| `.attack()` `.decay()` `.sustain()` `.release()` | envelope |
| `.transpose()` | semitones |
| `.scale("C4:minor")` | turns `n()` numbers into notes. Also: major, dorian, phrygian, lydian, mixolydian, locrian, "minor pentatonic", "major pentatonic", blues, chromatic |
| `.fast()` `.slow()` `.ply()` `.rev()` `.degrade()` `.degradeBy()` | pattern transforms |
| `.bank()` | accepted for compatibility (there is one kit) |

When you edit, **pattern changes apply at the next cycle**, so they always land
on the beat. **Fixed effect values apply immediately**, so knobs feel live. If
your code has an error, the old version keeps playing and the error shows up
inline at the exact column.

---

## Vim additions (in `.jynx` buffers)

The step commands work inside any pattern string on the line: `s("...")`,
`note("...")`, and patterned arguments like `.lpf("<400 2000>")`.

| keys | what it does |
|---|---|
| `<CR>` | evaluate (the buffer flashes) |
| `is` / `as` | text object: inside / around a **step** (`[~ sd]*2` or `cp(3,8)` counts as one step) |
| `]s` / `[s` | jump to the next / previous step |
| `g>{motion}` / `g<{motion}` | **rotate** the steps right / left. A count sets the amount: `3g>ip` |
| `gR{motion}` | **reverse** the steps |
| `g>>` `g<<` `gRR` | same, on the current line only |
| `g*` / `g/` | double / halve the step under the cursor: `hh` → `hh*2` → `hh*4` |
| `g?` | toggle random dropout on a step |
| `g~` | make a step alternate with silence: `bd` ↔ `<bd ~>` |
| `gs` | **KNOB mode** on the number under the cursor |
| `<Space>_` | mute / unmute the pattern under the cursor (Strudel's `_` prefix) |
| `<Space>k` | open the **laptop keyboard**, playing the pattern under the cursor |
| `<Space>p` | paste the phrase you just played into the pattern string under the cursor |
| `gcc` | comment a line out with `//` |
| `<Space>v` | switch between the music and visuals buffers |
| `<Space>h` | hush (toggle silence) |
| `<Space>m{a-z}` / `<Space>'{a-z}` | save / recall a **scene**, like marks but for the whole set |

Vim commands that already work musically without changes:

| keys | musical meaning |
|---|---|
| `Ctrl-a` / `Ctrl-x` | nudge a number |
| `.` | repeat the last transformation |
| `u` | undo while performing |
| `"ayap` / `"ap` | keep a groove in a register and bring it back |
| `cis` | rewrite one step |
| `das` | delete a step |
| `ci"` | rewrite a whole pattern |

### KNOB mode

Put the cursor on a number inside a method, such as `.lpf(700)` or
`.lpf(slider(700, 100, 4000))`, and press `gs`:

| keys | does |
|---|---|
| `h` / `l` | fine adjust (1%) |
| `j` / `k` | coarse adjust (10%) |
| `H` / `L` | big jumps (25%) |
| `0` / `$` | minimum / maximum |
| `<Esc>` | done |

- The value updates in the text and in the sound at the same time.
- `slider(value, min, max)` uses its own range, like Strudel's slider. Other
  methods use a sensible range for that effect.
- Frequencies move on a log scale, so they sound even.
- A whole knob session undoes in one `u`.
- Every numeric method shows a small slider bar after it.

### Laptop keyboard

`<Space>k` opens it in a split, or run `./jynx keys <pattern>` in another
terminal window:

```
  w e   t y u   o p          1 2 3 4 5 6 7 8 9 0   drums (bd sd hh oh cp rim lt mt ht cr)
 a s d f g h j k l ; '       z x    octave down / up
 C D E F G A B C D E F       c v    softer / louder
                             Tab    next pattern
                             Enter  capture what you played as a pattern
                             Bksp   forget it
                             Esc    quit
```

- **Your code defines the instrument.** The keyboard plays the pattern you
  point it at, using that pattern's `.s()` sound and all its effects. The
  template has a silent `keys: note("~")` pattern just for this.
- **Held notes:** in terminals with the kitty keyboard protocol (Ghostty,
  kitty, WezTerm, and Neovim's own terminal, so `<Space>k` too), a note sounds
  for as long as you hold the key. Elsewhere, notes play a fixed length that
  `[` and `]` adjust.
- **Capture:** play a phrase over the beat, then press Enter. jynx snaps it
  to the cycle grid and copies it as mini-notation, for example `c4 ~ e4 g4`,
  or `<[...] [...]>` if it spans several cycles. Back in the editor, put the
  cursor on a pattern string and press `<Space>p`.

### Generative macros

```vim
qq  4G g>>  q        " record: rotate the drums one step
:Every 4 @q          " run it every 4 cycles, automatically
:Every off
```

The statusline counts down to the next run.

### Commands

| command | does |
|---|---|
| `:Every N @r` | run macro `r` every N cycles |
| `:Every off` | stop it |
| `:Scene save a` | save a scene |
| `:Scene a` | recall a scene |
| `:Hush` | toggle silence |
| `:JynxStart` | start the engine and the window layout |

### Statusline

| shows | means |
|---|---|
| `● live` | the code you see is what's playing |
| `◐ rehearsing` | you have insert-mode edits that haven't been sent yet |
| `✗ error` | the old code is still playing |
| `KNOB lpf 800` | you're in KNOB mode |

---

## The visuals language (`.vis`)

This is a line-based language loosely inspired by Hydra, with one operation per line:

```
osc 8+bass*20 .15 .9      -- sources draw an image
add shape 4 .2+drums*.5   -- later sources blend in: add / mult / diff
kaleid 5                  -- geometry bends every source, wherever it's written
rotate 0 .08
color .9 .5 1             -- color ops apply top to bottom
feedback .8               -- trails
```

- **Arguments are expressions.** Write them without spaces inside.
- **Pattern names are live audio levels** from 0 to 1, so `kaleid 3+bass*6`
  reacts to the bass.
- You can also use `t` (seconds), `cycle`, `sin`, `cos`, `abs` and `pi`.
- `gs` (KNOB) works on any number in a `.vis` buffer.

| kind | ops |
|---|---|
| sources | `osc freq speed offset`, `noise scale speed`, `shape sides radius soft`, `rings freq speed`, `grid n width`, `solid r g b` |
| geometry | `kaleid n`, `rotate angle speed`, `scale k`, `repeat nx ny`, `scroll x y`, `warp amount freq`, `pixelate n` |
| color | `color r g b`, `invert`, `bright`, `contrast`, `hue`, `thresh`, `posterize n`, `feedback amount` |

---

## How it works

```
Neovim (nvim/lua/jynx)  ── JSON over TCP 127.0.0.1:7400 ──▶  engine (python -m engine)
  operators, KNOB, :Every                                       lang.py     parse Strudel syntax
  statusline, diagnostics   ◀── cycle ticks, "hits", errors ──  pattern.py  mini-notation
  highlights playing steps                                      audio.py    scheduler + mixer + fx
                                                                synth.py    drums + synth voices
keyboard (engine/keys.py) ── key down / up ──────────────────▶  vis.py      visuals → ANSI
```

- **No JavaScript runs.** The Strudel code is parsed by jynx's own parser
  (`lang.py`). It's never executed as JavaScript, so only the documented
  subset works.
- **Highlighting what plays:** every event remembers the line and columns of
  source text that created it, including patterned arguments like
  `.lpf("<400 2000>")`. The engine sends those "hits" back, timed to match the
  audio output latency, so the editor highlights each step exactly when you
  hear it.
- **Clock:** the engine counts cycles, as Strudel does. It swaps in the newest
  patterns only at cycle boundaries.
