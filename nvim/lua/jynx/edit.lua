-- Vim grammar for Strudel patterns: step text objects/motions, musical operators, KNOB mode.
-- Patterns live inside quoted strings: s("bd*2 [~ sd]"), .lpf("<400 2000>").
local M = {}
local api = vim.api

---------------------------------------------------------------------- strings + steps

-- Quoted strings on a line as { {first, last}, ... }: 1-based inclusive columns of the
-- contents (an empty string has last = first - 1). Stops at a // comment.
function M.strings(line)
  local out, i = {}, 1
  while i <= #line do
    local ch = line:sub(i, i)
    if ch == "/" and line:sub(i + 1, i + 1) == "/" then break end
    if ch == '"' or ch == "'" or ch == "`" then
      local j = line:find(ch, i + 1, true)
      if not j then break end
      out[#out + 1] = { i + 1, j - 1 }
      i = j + 1
    else
      i = i + 1
    end
  end
  return out
end

-- Top-level steps inside columns [s, e] as { {first, last}, ... }.
-- Brackets count as one step, so `[~ sd]*2` and `<c3 e3>` are single steps.
function M.steps_in(line, s, e)
  local out, depth, cur = {}, 0, nil
  for i = s, e do
    local ch = line:sub(i, i)
    if ch:match("%s") and depth == 0 then
      if cur then out[#out + 1] = { cur, i - 1 }; cur = nil end
    else
      cur = cur or i
      if ch == "[" or ch == "<" or ch == "(" then depth = depth + 1
      elseif ch == "]" or ch == ">" or ch == ")" then depth = math.max(depth - 1, 0) end
    end
  end
  if cur then out[#out + 1] = { cur, e } end
  return out
end

-- The string under (or after) column c, or the last one on the line.
local function string_at(line, c)
  local strs = M.strings(line)
  if #strs == 0 then return nil end
  for _, st in ipairs(strs) do
    if c >= st[1] - 1 and c <= st[2] + 1 then return st end
  end
  for _, st in ipairs(strs) do
    if st[1] > c then return st end
  end
  return strs[#strs]
end

-- The step under (or just right of) the cursor.
local function step_at_cursor()
  local row, col = unpack(api.nvim_win_get_cursor(0))
  local line = api.nvim_get_current_line()
  local c = col + 1
  local st = string_at(line, c)
  if not st then return nil end
  local steps = M.steps_in(line, st[1], st[2])
  if #steps == 0 then return nil end
  for i, s in ipairs(steps) do
    if c <= s[2] then return row, line, steps, i end
  end
  return row, line, steps, #steps
end

-- Rebuild a line with new step texts, keeping the original spacing between steps.
local function rebuild(line, steps, texts)
  local parts = { line:sub(1, steps[1][1] - 1) }
  for i, st in ipairs(steps) do
    parts[#parts + 1] = texts[i]
    local next_start = steps[i + 1] and steps[i + 1][1] or (#line + 1)
    parts[#parts + 1] = line:sub(st[2] + 1, next_start - 1)
  end
  return table.concat(parts)
end

local function step_texts(line, steps)
  local t = {}
  for i, st in ipairs(steps) do t[i] = line:sub(st[1], st[2]) end
  return t
end

---------------------------------------------------------------------- operators

local transforms = {}

function transforms.rotate(texts, n)
  local out, len = {}, #texts
  for i = 1, len do out[i] = texts[((i - 1 - n) % len) + 1] end
  return out
end

function transforms.reverse(texts)
  local out = {}
  for i = #texts, 1, -1 do out[#out + 1] = texts[i] end
  return out
end

-- Transform every pattern string on the lines (right to left so columns stay valid).
local function apply_lines(first, last, kind, count)
  local lines = api.nvim_buf_get_lines(0, first - 1, last, false)
  local changed = false
  for i, line in ipairs(lines) do
    local strs = M.strings(line)
    for j = #strs, 1, -1 do
      local steps = M.steps_in(line, strs[j][1], strs[j][2])
      if #steps > 1 then
        local texts = step_texts(line, steps)
        if kind == "rotr" then texts = transforms.rotate(texts, count)
        elseif kind == "rotl" then texts = transforms.rotate(texts, -count)
        elseif kind == "rev" then texts = transforms.reverse(texts) end
        line = rebuild(line, steps, texts)
        changed = true
      end
    end
    lines[i] = line
  end
  if changed then api.nvim_buf_set_lines(0, first - 1, last, false, lines) end
end

local pending = { kind = nil, count = 1 }

function M.opfunc()
  local first = api.nvim_buf_get_mark(0, "[")[1]
  local last = api.nvim_buf_get_mark(0, "]")[1]
  apply_lines(first, last, pending.kind, pending.count)
end

-- expr mapping: remember the count as the *amount*, then wait for a motion.
function M.operator(kind)
  pending.kind, pending.count = kind, vim.v.count1
  vim.o.operatorfunc = "v:lua.require'jynx.edit'.opfunc"
  return "<Esc>g@"
end

function M.line(kind)
  local row = api.nvim_win_get_cursor(0)[1]
  apply_lines(row, row, kind, vim.v.count1)
end

function M.visual(kind)
  local count = vim.v.count1
  local a, b = vim.fn.line("v"), vim.fn.line(".")
  if a > b then a, b = b, a end
  api.nvim_feedkeys(api.nvim_replace_termcodes("<Esc>", true, false, true), "nx", false)
  apply_lines(a, b, kind, count)
end

---------------------------------------------------------------------- step edits

local function edit_step(fn)
  local row, line, steps, i = step_at_cursor()
  if not row then return end
  local st = steps[i]
  local text = line:sub(st[1], st[2])
  local new = fn(text)
  if new and new ~= text then
    api.nvim_buf_set_text(0, row - 1, st[1] - 1, row - 1, st[2], { new })
    api.nvim_win_set_cursor(0, { row, st[1] - 1 })
  end
end

local function split_q(text)
  if text:sub(-1) == "?" then return text:sub(1, -2), "?" end
  return text, ""
end

-- g*: bd -> bd*2 -> bd*4
function M.double()
  edit_step(function(text)
    local body, q = split_q(text)
    local base, n = body:match("^(.-)%*(%d+)$")
    if base then return base .. "*" .. (tonumber(n) * 2) .. q end
    local sbase, sn = body:match("^(.-)/(%d+)$")
    if sbase then
      local m = tonumber(sn) / 2
      return (m <= 1 and sbase or (sbase .. "/" .. math.floor(m))) .. q
    end
    return body .. "*2" .. q
  end)
end

-- g/: bd*4 -> bd*2 -> bd -> bd/2
function M.halve()
  edit_step(function(text)
    local body, q = split_q(text)
    local base, n = body:match("^(.-)%*(%d+)$")
    if base then
      local m = math.floor(tonumber(n) / 2)
      return (m <= 1 and base or (base .. "*" .. m)) .. q
    end
    local sbase, sn = body:match("^(.-)/(%d+)$")
    if sbase then return sbase .. "/" .. (tonumber(sn) * 2) .. q end
    return body .. "/2" .. q
  end)
end

-- g?: toggle random dropout
function M.maybe()
  edit_step(function(text)
    local body, q = split_q(text)
    return q == "?" and body or (body .. "?")
  end)
end

-- g~: bd -> <bd ~>  (alternate with a rest), <bd ~> -> bd
function M.alternate()
  edit_step(function(text)
    local inner = text:match("^<(.*) ~>$")
    if inner then return inner end
    return "<" .. text .. " ~>"
  end)
end

-- Replace the whole pattern string under the cursor (used to paste a played phrase).
function M.replace_string(text)
  local row, col = unpack(api.nvim_win_get_cursor(0))
  local line = api.nvim_get_current_line()
  local st = string_at(line, col + 1)
  if not st then return false end
  api.nvim_buf_set_text(0, row - 1, st[1] - 1, row - 1, st[2], { text })
  api.nvim_win_set_cursor(0, { row, st[1] - 1 })
  return true
end

---------------------------------------------------------------------- text objects + motions

function M.select_step(around)
  local row, line, steps, i = step_at_cursor()
  if not row then return end
  local a, b = steps[i][1], steps[i][2]
  if around then
    local e = b
    while line:sub(e + 1, e + 1):match("^%s$") do e = e + 1 end
    if e == b then
      local prev_end = steps[i - 1] and steps[i - 1][2] or 0
      while a - 1 > prev_end and line:sub(a - 1, a - 1):match("^%s$") do a = a - 1 end
    end
    b = e
  end
  vim.fn.setpos("'<", { 0, row, a, 0 })
  vim.fn.setpos("'>", { 0, row, b, 0 })
  vim.cmd("normal! gv")
end

function M.jump_step(dir)
  local count = vim.v.count1
  local row, col = unpack(api.nvim_win_get_cursor(0))
  local line = api.nvim_get_current_line()
  local steps = {}
  for _, st in ipairs(M.strings(line)) do
    vim.list_extend(steps, M.steps_in(line, st[1], st[2]))
  end
  if #steps == 0 then return end
  local c = col + 1
  local idx
  for i, st in ipairs(steps) do
    if c >= st[1] and c <= st[2] then idx = i end
  end
  if not idx then
    idx = dir > 0 and 0 or #steps + 1
    for i, st in ipairs(steps) do
      if dir > 0 and st[2] < c then idx = i end
      if dir < 0 and st[1] > c and idx == #steps + 1 then idx = i end
    end
  end
  idx = math.min(math.max(idx + dir * count, 1), #steps)
  api.nvim_win_set_cursor(0, { row, steps[idx][1] - 1 })
end

---------------------------------------------------------------------- KNOB mode

M.ALIASES = {
  cutoff = "lpf", lp = "lpf", ctf = "lpf", hcutoff = "hpf", hp = "hpf", resonance = "lpq",
  delayfeedback = "delayfb", dfb = "delayfb", dt = "delaytime", delayt = "delaytime", att = "attack",
  dec = "decay", sus = "sustain", rel = "release", trans = "transpose", vel = "velocity",
  legato = "clip", bpf = "bandf", bp = "bandf", bpq = "bandq", dist = "distort", ph = "phaser",
  phd = "phaserdepth", phasdp = "phaserdepth", phc = "phasercenter", phs = "phasersweep",
  o = "orbit", duck = "duckorbit", duckatt = "duckattack", lpe = "lpenv", lpa = "lpattack",
  lpd = "lpdecay", lps = "lpsustain", lpr = "lprelease",
}
M.RANGES = {
  lpf = { 50, 18000, log = true }, hpf = { 20, 8000, log = true }, lpq = { 0, 20 },
  bandf = { 50, 12000, log = true }, bandq = { 0.1, 20 },
  gain = { 0, 1.5 }, velocity = { 0, 1 }, postgain = { 0, 3 }, room = { 0, 1 }, delay = { 0, 1 },
  delaytime = { 0.02, 1 }, delayfb = { 0, 0.95 }, crush = { 1, 16, int = true }, pan = { 0, 1 },
  coarse = { 1, 32, int = true }, shape = { 0, 0.95 }, distort = { 0, 8 },
  phaser = { 0, 12 }, phaserdepth = { 0, 1 }, phasercenter = { 100, 6000, log = true },
  phasersweep = { 0, 6000 },
  attack = { 0, 1 }, decay = { 0.01, 2 }, sustain = { 0, 1 }, release = { 0.01, 2 },
  transpose = { -24, 24, int = true }, speed = { -4, 4 }, clip = { 0.05, 4 },
  fm = { 0, 16 }, fmh = { 0.25, 8 }, vib = { 0, 16 }, vibmod = { 0, 4 },
  detune = { 0, 2 }, lpenv = { -8, 8 }, lpattack = { 0, 1 }, lpdecay = { 0, 1 }, lpsustain = { 0, 1 },
  lprelease = { 0, 1 }, orbit = { 1, 8, int = true }, duckattack = { 0, 1 }, duckdepth = { 0, 1 },
  fast = { 1, 8 }, slow = { 1, 8 }, hurry = { 1, 8 }, ply = { 1, 8, int = true },
  iter = { 1, 8, int = true }, every = { 1, 16, int = true }, chunk = { 1, 16, int = true },
  echo = { 1, 8, int = true }, degradeBy = { 0, 1 }, sometimesBy = { 0, 1 }, linger = { 0.05, 1 },
  late = { 0, 1 }, early = { 0, 1 }, off = { 0, 1 }, add = { -24, 24, int = true },
  segment = { 1, 32, int = true }, range = { 0, 8000 },
  setcpm = { 10, 200 }, setcps = { 0.05, 2 },
}

function M.range_for(name)
  return M.RANGES[M.ALIASES[name] or name]
end

local ns_knob = api.nvim_create_namespace("jynx_knob")
M.knob = nil -- active knob state (read by the statusline)

function M.numbers(line)
  local out = {}
  for s, num, e in line:gmatch("()(%-?%d*%.?%d+)()") do
    local before = line:sub(s - 1, s - 1)
    if not before:match("[%a_#]") then out[#out + 1] = { s, e - 1, tonumber(num) } end
  end
  return out
end

-- Name of the call whose parentheses enclose column c, and the column of its "(".
local function enclosing_call(line, c)
  local depth = 0
  for i = c - 1, 1, -1 do
    local ch = line:sub(i, i)
    if ch == ")" then depth = depth + 1
    elseif ch == "(" then
      if depth == 0 then return line:sub(1, i - 1):match("([%a_][%w_]*)%s*$"), i end
      depth = depth - 1
    end
  end
end

local function close_paren(line, open)
  local depth = 0
  for i = open, #line do
    local ch = line:sub(i, i)
    if ch == "(" then depth = depth + 1
    elseif ch == ")" then
      depth = depth - 1
      if depth == 0 then return i end
    end
  end
  return #line
end

-- slider(value, min, max) at column `open` -> { value number, lo, hi }
function M.slider_args(line, open)
  local close = close_paren(line, open)
  local nums = {}
  for _, n in ipairs(M.numbers(line:sub(open + 1, close - 1))) do
    nums[#nums + 1] = { n[1] + open, n[2] + open, n[3] }
  end
  return nums
end

local function fmt(v, k)
  if k.int then return tostring(math.floor(v + 0.5)) end
  local span = k.hi - k.lo
  local s
  if span >= 500 then s = string.format("%d", math.floor(v + 0.5))
  elseif span >= 20 then s = string.format("%.1f", v)
  elseif span >= 2 then s = string.format("%.2f", v)
  else s = string.format("%.3f", v) end
  s = s:gsub("(%..-)0+$", "%1"):gsub("%.$", "")
  s = s:gsub("^0%.", "."):gsub("^%-0%.", "-.")
  return s
end

function M.pos_of(k, v)
  if k.log then return math.log(math.max(v, k.lo) / k.lo) / math.log(k.hi / k.lo) end
  return (v - k.lo) / (k.hi - k.lo)
end

local function val_of(k, p)
  p = math.min(math.max(p, 0), 1)
  if k.log then return k.lo * (k.hi / k.lo) ^ p end
  return k.lo + p * (k.hi - k.lo)
end

function M.bar(p, width)
  local filled = math.floor(math.min(math.max(p, 0), 1) * width + 0.5)
  return string.rep("█", filled) .. string.rep("░", width - filled)
end

local function draw_knob()
  local k = M.knob
  if not k then return end
  api.nvim_buf_clear_namespace(k.buf, ns_knob, 0, -1)
  api.nvim_buf_set_extmark(k.buf, ns_knob, k.row - 1, 0, {
    virt_text = { { "  ◀ ", "JynxKnob" }, { M.bar(M.pos_of(k, k.value), 20), "JynxKnob" },
      { string.format(" ▶ %s  [%s – %s]", fmt(k.value, k), fmt(k.lo, k), fmt(k.hi, k)), "JynxKnob" } },
    virt_text_pos = "eol",
    line_hl_group = "JynxKnobLine",
  })
  vim.cmd("redrawstatus")
end

local function set_value(v)
  local k = M.knob
  if k.int then v = math.floor(v + 0.5) end
  v = math.min(math.max(v, k.lo), k.hi)
  local txt = fmt(v, k)
  if k.edits > 0 then pcall(vim.cmd, "undojoin") end
  api.nvim_buf_set_text(k.buf, k.row - 1, k.s - 1, k.row - 1, k.e, { txt })
  k.e = k.s + #txt - 1
  k.value = tonumber(txt) or v
  k.edits = k.edits + 1
  api.nvim_win_set_cursor(0, { k.row, k.s - 1 })
  draw_knob()
  require("jynx").eval(k.buf, { quiet = true })
end

local function nudge(delta)
  local k = M.knob
  local p = M.pos_of(k, k.value) + delta * vim.v.count1
  local v = val_of(k, p)
  if k.int and math.floor(v + 0.5) == k.value then v = k.value + (delta > 0 and 1 or -1) end
  set_value(v)
end

local KNOB_KEYS = { "h", "l", "j", "k", "H", "L", "0", "$", "<Esc>", "q", "<CR>", "gs" }

function M.knob_exit()
  local k = M.knob
  if not k then return end
  M.knob = nil
  api.nvim_buf_clear_namespace(k.buf, ns_knob, 0, -1)
  for _, key in ipairs(KNOB_KEYS) do pcall(vim.keymap.del, "n", key, { buffer = k.buf }) end
  require("jynx").buffer_maps(k.buf)
  require("jynx").draw_sliders(k.buf)
  vim.cmd("redrawstatus")
end

-- The number under the cursor, else the next one on the line, else the last.
local function pick_number(nums, c)
  for _, n in ipairs(nums) do
    if c >= n[1] and c <= n[2] then return n end
  end
  for _, n in ipairs(nums) do
    if n[1] > c then return n end
  end
  return nums[#nums]
end

function M.knob_enter()
  local buf = api.nvim_get_current_buf()
  local row, col = unpack(api.nvim_win_get_cursor(0))
  local line = api.nvim_get_current_line()
  local nums = M.numbers(line)
  if #nums == 0 then return vim.notify("jynx: no number on this line") end
  local pick = pick_number(nums, col + 1)

  if vim.bo[buf].filetype == "jynx" then
    local name, open = enclosing_call(line, pick[1])
    if not name then return vim.notify("jynx: put the cursor on a number inside a method, like .lpf(800)") end
    if name == "slider" then
      -- slider(value, min, max): the knob turns the value, inside the slider's own range
      local args = M.slider_args(line, open)
      local outer = enclosing_call(line, open)
      local r = (outer and M.range_for(outer)) or {}
      pick = args[1]
      local lo = args[2] and args[2][3] or 0
      local hi = args[3] and args[3][3] or math.max(1, pick[3] * 2)
      M.knob = { key = outer or "slider", lo = lo, hi = hi, log = r.log and lo > 0, int = r.int }
    else
      local key = M.ALIASES[name] or name
      local r = M.RANGES[key] or { 0, math.max(1, math.abs(pick[3]) * 2) }
      M.knob = { key = key, lo = r[1], hi = r[2], log = r.log, int = r.int }
    end
  else
    local v = pick[3]
    local key = line:match("^%s*(%a+)") or "value"
    local isint = not line:sub(pick[1], pick[2]):find("%.")
    local hi = math.max(2 * math.abs(v), 1)
    M.knob = { key = key, lo = (v < 0) and -hi or 0, hi = hi, int = isint and hi >= 4 }
  end

  local k = M.knob
  k.buf, k.row, k.s, k.e, k.value, k.edits = buf, row, pick[1], pick[2], pick[3], 0
  if k.value < k.lo then k.lo = k.value end
  if k.value > k.hi then k.hi = k.value end
  if k.log and k.lo <= 0 then k.log = false end
  api.nvim_win_set_cursor(0, { row, k.s - 1 })

  local o = { buffer = buf, nowait = true }
  vim.keymap.set("n", "h", function() nudge(-0.01) end, o)
  vim.keymap.set("n", "l", function() nudge(0.01) end, o)
  vim.keymap.set("n", "j", function() nudge(-0.1) end, o)
  vim.keymap.set("n", "k", function() nudge(0.1) end, o)
  vim.keymap.set("n", "H", function() nudge(-0.25) end, o)
  vim.keymap.set("n", "L", function() nudge(0.25) end, o)
  vim.keymap.set("n", "0", function() set_value(k.lo) end, o)
  vim.keymap.set("n", "$", function() set_value(k.hi) end, o)
  for _, key in ipairs({ "<Esc>", "q", "<CR>", "gs" }) do
    vim.keymap.set("n", key, M.knob_exit, o)
  end
  draw_knob()
end

return M
