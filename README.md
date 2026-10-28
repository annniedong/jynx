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
- **You choose when it's heard.** Edits, in insert mode or normal mode, are a
  silent rehearsal: nothing changes until you press `<CR>` in normal mode, and
  then it lines up to the next cycle. KNOB mode and `:Every` macros play as
  they go.
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
./jynx screen        # visuals in their own terminal; the engine then shows only status
./jynx               # just the editor, if an engine is already running
./jynx music         # only the music code (live.jynx)
./jynx vis           # only the visuals code (live.vis)
./jynx keys bass     # the laptop keyboard plays the `bass` pattern
./jynx stop          # stop a leftover engine (e.g. "port is busy")
```

If an engine is already running, `./jynx` opens only the editor and connects
to it. Otherwise it opens the usual split layout.

For one terminal per job, start the engine first, then `./jynx music` and
`./jynx vis` in two more. Each takes a song name too: `./jynx vis mysong`.
Add `./jynx screen` for the visuals on their own (it waits if the engine
isn't up yet, and reconnects if it restarts).

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
- **Variables:** `let lpf = slider(7420, 0, 10000)`, `let bank = "tr909"` or
  `const wob = sine.range(1, 4)`, then `.lpf(lpf)`. Numbers work in sums too:
  `.lpf(lpf * 2)`.

### Mini-notation (inside the strings)

| syntax | meaning |
|---|---|
| `bd sd` | sequence: the steps share the cycle |
| `~` or `-` | rest |
| `[bd bd]` | group: subdivide one step |
| `[c3,e3,g3]` | stack: play together (chords) |
| `<c3 e3 g3>` | alternate: one per cycle |
| `<- hh, - sd>` | several alternations at once: `<- hh>` and `<- sd>` together |
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

### Effects and sound

| method | meaning (Strudel's units) |
|---|---|
| `.s("sawtooth")` | sound for `note()` / `n()` patterns (can be a pattern: `"<sawtooth square>"`) |
| `.note("c3 e3")` `.n("0 2")` | pitches for an `s("sawtooth*4")` pattern |
| `.gain()` `.velocity()` `.postgain()` | volume (postgain comes after the effects) |
| `.lpf()` `.hpf()` `.lpq()` | low/high-pass filter in Hz, resonance (aliases: `cutoff`, `hcutoff`, `resonance`) |
| `.lpenv(8)` | filter envelope: each note's cutoff jumps up 8 octaves from `lpf` and falls back. Shape it with `.lpattack()` `.lpdecay()` `.lpsustain()` `.lprelease()` (defaults .005, .14, 0, .1) |
| `.bandf()` `.bandq()` | band-pass filter (aliases: `bpf`, `bpq`) |
| `.vowel("a")` | formant filter: `a e i o u`, can be a pattern: `"<a e i>"` |
| `.distort()` | heavy distortion, 0–10 (alias `dist`). `"3:.2:diode"` = amount, then postgain, then type: `diode`, `hard`, `fold`, `cubic` or soft (the default) |
| `.shape()` | softer waveshaping, 0–1 |
| `.crush()` | bit depth (lower = dirtier), 1–16 |
| `.coarse()` | sample-rate reduction, 1 = off |
| `.phaser(rate)` `.phaserdepth()` `.phasercenter()` `.phasersweep()` | phaser, rate in Hz |
| `.room()` | reverb amount, 0–1 |
| `.delay()` `.delaytime()` `.delayfeedback()` | delay: wet amount, seconds, feedback |
| `.pan()` | 0 = left, .5 = center, 1 = right |
| `.attack()` `.decay()` `.sustain()` `.release()` `.adsr(".01:.1:.5:.2")` | envelope |
| `.fm()` `.fmh()` | FM synthesis: index and harmonic ratio |
| `.detune()` | spread of the `supersaw`'s five saws, in semitones (default .2). `.detune(rand)` makes every note different |
| `.vib()` `.vibmod()` | vibrato: rate in Hz, depth in semitones |
| `.speed()` | playback speed: `2` = octave up, negative plays drums backwards |
| `.clip()` | note length multiplier (alias `legato`) |
| `.cut(1)` | choke group: a new note cuts off ringing ones in the same group |
| `.transpose()` | semitones |
| `.scale("C4:minor")` | turns `n()` numbers (and `note()` numbers, like `note(run(8))`) into notes. Write spaces as `:`, like `"Ab:minor:pentatonic"`. With no octave, the root is in octave 3. It can be a pattern: `"<C:major A:minor>"` |
| `.o(3)` / `.orbit(3)` | which orbit (group) the pattern plays on, for ducking. Default 1 |
| `.duck("4:5:6")` `.duckatt()` `.duckdepth()` | sidechain: each note of this pattern dips orbits 4, 5 and 6, which come back over `duckatt` seconds (default .1). `duckdepth` is 0–1 (default 1). Lists like `"2:1"` go one per orbit (aliases `duckorbit`, `duckattack`) |
| `.bank()` `.color()` `._pianoroll()` `._scope()` … | accepted so Strudel code runs; they do nothing here (jynx has one built-in drum kit) |

### Pattern functions

| function | what it does |
|---|---|
| `.fast(2)` `.slow(2)` `.hurry(2)` | speed up / slow down (hurry also raises the pitch) |
| `.early(.25)` `.late(.25)` | shift in time, in cycles |
| `.rev()` `.palindrome()` | backwards / every other cycle backwards |
| `.ply(2)` | repeat each event |
| `.iter(4)` `.iterBack(4)` | start one step later each cycle |
| `.linger(.25)` | loop the first quarter |
| `.zoom(.25, .75)` | play only part of the cycle, stretched |
| `.swing(4)` `.swingBy(1/3, 4)` | swing |
| `.press()` `.brak()` | push onto the off-beat / breakbeat variation |
| `.struct("x ~ x x")` `.mask("1 0 1 1")` | impose a rhythm / silence parts |
| `.euclid(3,8)` `.euclidRot(3,8,2)` `.euclidLegato(3,8)` | euclidean rhythms |
| `.segment(4)` | sample a pattern (or a signal) 4 times a cycle |
| `.arp("0 2 1")` `.arpeggiate()` | play chords as arpeggios |
| `.add(12)` `.sub(12)` | shift notes: semitones for `note()`, scale steps for `n()` |
| `.degrade()` `.degradeBy(.3)` | randomly drop events |
| `.sometimes(f)` `.often(f)` `.rarely(f)` `.almostNever(f)` `.almostAlways(f)` `.sometimesBy(.3, f)` | randomly apply `f` to some events |
| `.someCycles(f)` `.someCyclesBy(.3, f)` | randomly apply `f` to whole cycles |
| `.every(4, f)` `.firstOf(4, f)` `.lastOf(4, f)` | apply `f` every 4th cycle |
| `.chunk(4, f)` `.chunkBack(4, f)` | apply `f` to a moving quarter of the cycle |
| `.off(1/8, f)` | add a shifted copy changed by `f` |
| `.superimpose(f)` `.layer(f, g)` | add / stack transformed copies |
| `.jux(f)` `.juxBy(.5, f)` | original on the left, `f` on the right |
| `.echo(3, 1/8, .5)` `.stut(3, .5, 1/8)` `.echoWith(3, 1/8, f)` | rhythmic echoes |
| `.inside(2, f)` `.outside(2, f)` | apply `f` at a slower / faster time scale |

A function `f` can be written three ways: `x => x.add(12).gain(.5)`, a bare
name like `rev`, or a single call like `fast(2)`. Inside a function, effects
only touch the events the function makes: `.every(4, x => x.lpf(300))` filters
every fourth cycle and leaves the rest alone.

Most numbers can be patterns: `.fast("<1 2 4>")`, `.degradeBy("<0 .5>")`,
`.add("<0 3 5>")`.

**Scales:** major, minor, ionian, dorian, phrygian, lydian, mixolydian,
aeolian, locrian, harmonic minor, melodic minor, harmonic major, phrygian
dominant, lydian dominant, altered (super locrian), dorian b2, lydian
augmented, locrian #2, mixolydian b6, locrian major, ultralocrian, leading
whole tone, double harmonic (byzantine), hungarian minor, hungarian major,
neapolitan major / minor, persian, enigmatic, oriental, bebop, bebop major,
bebop minor, diminished, half-whole diminished, augmented, prometheus, whole
tone, chromatic, blues (minor blues), major blues, pentatonic, major
pentatonic, minor pentatonic, minor hexatonic, ionian / mixolydian / lydian /
locrian pentatonic, minor six pentatonic, scriabin, hirajoshi, in-sen, iwato,
kumoijoshi, pelog, egyptian, ritusen.

### run()

`run(n)` counts up from 0: `run(4)` is the same as `"0 1 2 3"`. Use it
wherever a pattern of numbers goes. The count can be a pattern or a signal too.

```js
arp: n(run(8)).scale("C4:minor").s("pluck")         // up the scale
grow: n(run("<4 8>")).scale("Ab:minor:pentatonic")   // 4 steps, then 8
up: n(run(saw.range(2, 8).slow(4))).scale("D:dorian") // more steps each cycle
```

### Signals

Continuous values for sweeps: `sine cosine saw isaw tri square rand perlin`
and `irand(n)`. They go from 0 to 1. Shape them with `.range(lo, hi)`,
`.rangex(lo, hi)` (exponential, good for filters), `.slow(n)`, `.fast(n)`,
`.add() .sub() .mul() .div()` and `.segment(n)`.

```js
bass: note("c2*8").s("sawtooth")
  .lpf(sine.range(200, 3000).slow(4))     // a smooth filter sweep over 4 cycles

lead: n(irand(8).segment(8)).scale("C4:minor").s("pluck")   // random melody
```

On an effect like `lpf`, a signal moves smoothly even while a note rings.

**Every number argument takes a signal**, a `"pattern"` or `run()`, not only
effects: `.fast(sine.range(1, 4))`, `.euclid(saw.range(3, 7), 8)`,
`.echo(3, sine.range(1/16, 1/8), .5)`, `.juxBy(sine, rev)` and so on. A function
like `fast` reads the signal 16 times a cycle. Like on strudel.cc, a signal on
a timing function (`fast`, `late`, `ply`, ...) can make an event play twice or
not at all at the moment the value changes, which is part of the sound.

**Not supported yet:** `stack()` / `cat()` at the top level, `chord()` /
`voicing()`, and sample functions like `chop`, `striate` and `loopAt`. jynx
has no sample files.

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
 0 2 4 5 7 9 . . . . .       c v    softer / louder
                             Tab    next pattern
                             Enter  capture what you played as a pattern
                             Bksp   forget it
                             Esc    quit
```

- **Your code defines the instrument.** The keyboard plays the pattern you
  point it at, using that pattern's `.s()` sound and all its effects. The
  template has a silent `keys: note("~")` pattern just for this.
- **The keys are numbers, like `n("0 1 2")`:** `a` = 0, `w` = 1, `s` = 2 … `k` =
  12, up to `'` = 17. If the pattern has a `.scale()`, the numbers are steps
  of that scale, so `keys: n("~").scale("Ab:minor:pentatonic")` makes every
  key land in Ab minor pentatonic. A patterned scale (`"<C:major A:minor>"`)
  follows along as it changes. Without a scale, the keys are semitones from C,
  like a piano. `z` / `x` move a whole octave of the scale.
- **Held notes:** in terminals with the kitty keyboard protocol (Ghostty,
  kitty, WezTerm, and Neovim's own terminal, so `<Space>k` too), a note sounds
  for as long as you hold the key. Elsewhere, notes play a fixed length that
  `[` and `]` adjust.
- **Capture:** play a phrase over the beat, then press Enter. jynx snaps it
  to the cycle grid and copies it as mini-notation: numbers like `0 ~ 2 4`
  in a scale (ready for `n("...")`), or note names like `c4 ~ e4 g4` without one,
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
vis | screen              -- pipe it to the screen: nothing shows without this
```

- **Arguments are expressions.** Write them without spaces inside.
- **Pattern names are live audio levels** from 0 to 1, so `kaleid 3+bass*6`
  reacts to the bass.
- You can also use `t` (seconds), `cycle`, `sin`, `cos`, `abs` and `pi`.
- `gs` (KNOB) works on any number in a `.vis` buffer.
- **`vis | screen`** sends the picture to the screen, whether that's the
  engine's terminal or a `./jynx screen`. Without it the screen stays black,
  while the music and the levels the visuals read keep running. So a set can
  start dark: delete the line (`dd`) to cut the picture and `p` to bring it back.

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
