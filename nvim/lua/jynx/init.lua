-- jynx: a vim-native livecoding language. This file is the editor half:
-- it talks to the engine, lights up playing steps, and adds jynx's commands.
local M = {}
local api = vim.api
local uv = vim.uv or vim.loop
local edit = require("jynx.edit")

local S = {
  root = nil,
  port = tonumber(vim.env.JYNX_PORT) or 7400,
  sock = nil,
  connected = false,
  job = nil,
  cycle = 0,
  every = nil, -- { n = 4, reg = "q" }
  hushed = false,
  dirty = {}, -- buf -> true while insert-mode edits are unsent
  errors = {}, -- kind -> count
  scenes = {},
  bufs = {}, -- kind -> buf
}
M.state = S

local ns_hit = api.nvim_create_namespace("jynx_hits")
local ns_flash = api.nvim_create_namespace("jynx_flash")
local ns_slider = api.nvim_create_namespace("jynx_sliders")
local ns_diag = api.nvim_create_namespace("jynx_diag")

local function kind_of(buf)
  local ft = vim.bo[buf].filetype
  if ft == "jynx" then return "music" elseif ft == "jynxvis" then return "vis" end
end

local function win_of(buf)
  for _, w in ipairs(api.nvim_tabpage_list_wins(0)) do
    if api.nvim_win_get_buf(w) == buf then return w end
  end
end

---------------------------------------------------------------------- engine link

local function send(msg)
  if not (S.connected and S.sock) then return false end
  S.sock:write(vim.json.encode(msg) .. "\n")
  return true
end

local function flash(buf)
  api.nvim_buf_clear_namespace(buf, ns_flash, 0, -1)
  for i = 0, api.nvim_buf_line_count(buf) - 1 do
    api.nvim_buf_set_extmark(buf, ns_flash, i, 0, { line_hl_group = "JynxFlash", priority = 1 })
  end
  vim.defer_fn(function()
    if api.nvim_buf_is_valid(buf) then api.nvim_buf_clear_namespace(buf, ns_flash, 0, -1) end
  end, 140)
end

function M.eval(buf, opts)
  opts = opts or {}
  buf = (buf == nil or buf == 0) and api.nvim_get_current_buf() or buf
  local kind = kind_of(buf)
  if not kind then return end
  S.bufs[kind] = buf
  local text = table.concat(api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
  if send({ type = kind, text = text }) then
    S.dirty[buf] = nil
    if not opts.quiet then flash(buf) end
  elseif not opts.quiet then
    vim.notify("jynx: engine not connected (run :JynxStart)", vim.log.levels.WARN)
  end
  vim.cmd("redrawstatus")
end

local function show_hits(hits)
  local buf = S.bufs.music
  if not (buf and api.nvim_buf_is_valid(buf)) then return end
  local n = api.nvim_buf_line_count(buf)
  for _, h in ipairs(hits) do
    local line, c0, c1, dur = h[1], h[2], h[3], h[4]
    if line <= n then
      local ok, id = pcall(api.nvim_buf_set_extmark, buf, ns_hit, line - 1, c0,
        { end_col = c1, hl_group = "JynxHit", priority = 200, strict = false })
      if ok then
        local ms = math.floor(math.min(math.max(dur * 700, 70), 350))
        vim.defer_fn(function() pcall(api.nvim_buf_del_extmark, buf, ns_hit, id) end, ms)
      end
    end
  end
end

local function run_every(n)
  local ev = S.every
  if not ev or n % ev.n ~= 0 or edit.knob then return end
  if api.nvim_get_mode().mode ~= "n" then return end
  local buf = S.bufs.music
  local win = buf and win_of(buf)
  if not win then return end
  api.nvim_win_call(win, function()
    local ok, err = pcall(vim.cmd, "normal @" .. ev.reg)
    if not ok then vim.notify("jynx: @" .. ev.reg .. " failed: " .. err, vim.log.levels.WARN) end
  end)
  ev.runs = (ev.runs or 0) + 1
  M.eval(buf, { quiet = true })
end

function M.on_message(msg)
  local t = msg.type
  if t == "hits" then
    show_hits(msg.hits)
  elseif t == "tracks" then
    S.track_lines = msg.lines or {}
  elseif t == "cycle" then
    S.cycle = msg.n
    run_every(msg.n)
    vim.cmd("redrawstatus!")
  elseif t == "ok" or t == "error" then
    local buf = S.bufs[msg.buffer]
    if not (buf and api.nvim_buf_is_valid(buf)) then return end
    local diags = {}
    for _, e in ipairs(msg.errors or {}) do
      diags[#diags + 1] = { lnum = e.line - 1, col = e.col, message = e.msg,
        severity = vim.diagnostic.severity.ERROR, source = "jynx" }
    end
    vim.diagnostic.set(ns_diag, buf, diags)
    S.errors[msg.buffer] = #diags
    if t == "error" then  -- the exact error text, in the message area at the bottom
      local chunks = {}
      for i, e in ipairs(msg.errors or {}) do
        chunks[#chunks + 1] = { string.format("%sjynx %s line %d:%d  %s", i > 1 and "\n" or "",
          msg.buffer, e.line, e.col + 1, e.msg), "ErrorMsg" }
      end
      api.nvim_echo(chunks, true, {})
    end
    vim.cmd("redrawstatus!")
  end
end

local function connect(tries)
  local sock = uv.new_tcp()
  sock:connect("127.0.0.1", S.port, function(err)
    if err then
      sock:close()
      if tries > 0 then
        vim.defer_fn(function() connect(tries - 1) end, 250)
      else
        vim.schedule(function() vim.notify("jynx: could not reach the engine", vim.log.levels.ERROR) end)
      end
      return
    end
    S.sock, S.connected = sock, true
    local pending = ""
    sock:read_start(function(rerr, chunk)
      if rerr or not chunk then
        S.connected, S.sock = false, nil
        pcall(sock.close, sock)
        vim.schedule(function()
          vim.cmd("redrawstatus!")
          -- the engine runs in another terminal: when it restarts, pick it back up
          if vim.env.JYNX_ENGINE == "external" then vim.defer_fn(function() connect(math.huge) end, 1000) end
        end)
        return
      end
      pending = pending .. chunk
      local lines = {}
      while true do
        local i = pending:find("\n", 1, true)
        if not i then break end
        lines[#lines + 1] = pending:sub(1, i - 1)
        pending = pending:sub(i + 1)
      end
      if #lines > 0 then
        vim.schedule(function()
          for _, l in ipairs(lines) do
            local ok, msg = pcall(vim.json.decode, l)
            if ok then M.on_message(msg) end
          end
        end)
      end
    end)
    vim.schedule(function()
      for _, b in ipairs(api.nvim_list_bufs()) do
        if api.nvim_buf_is_loaded(b) and kind_of(b) then M.eval(b, { quiet = true }) end
      end
    end)
  end)
end

---------------------------------------------------------------------- sliders

function M.draw_sliders(buf)
  if kind_of(buf) ~= "music" then return end
  api.nvim_buf_clear_namespace(buf, ns_slider, 0, -1)
  if edit.knob and edit.knob.buf == buf then return end
  for i, line in ipairs(api.nvim_buf_get_lines(buf, 0, -1, false)) do
    local chunks = {}
    for open, name in line:gmatch("()([%a_][%w_]*)%(") do
      local paren = open + #name
      local r, v
      if name == "slider" then
        local args = edit.slider_args(line, paren)
        if #args >= 3 then
          local outer = edit.range_for(line:sub(1, open - 1):match("([%a_][%w_]*)%($") or "")
          r = { args[2][3], args[3][3], log = outer and outer.log and args[2][3] > 0, slider = true }
          v = args[1][3]
        end
      else
        local num = line:match("^%(%s*(%-?%d*%.?%d+)%s*%)", paren)
        r = num and edit.range_for(name)
        v = tonumber(num)
      end
      if r and v then
        local k = { lo = r[1], hi = r[2], log = r.log }
        chunks[#chunks + 1] = { "  " .. edit.bar(edit.pos_of(k, v), 10), r.slider and "JynxKnob" or "JynxSlider" }
      end
    end
    if #chunks > 0 then
      api.nvim_buf_set_extmark(buf, ns_slider, i - 1, 0, { virt_text = chunks, virt_text_pos = "eol" })
    end
  end
end

---------------------------------------------------------------------- commands

function M.hush()
  S.hushed = not S.hushed
  send({ type = "hush", on = S.hushed })
  vim.cmd("redrawstatus!")
end

function M.every(args)
  local n, reg = args[1], args[2]
  if n == "off" or n == nil then
    S.every = nil
    vim.notify("jynx: every off")
  else
    reg = (reg or "@q"):gsub("^@", "")
    S.every = { n = math.max(tonumber(n) or 4, 1), reg = reg, runs = 0 }
  end
  vim.cmd("redrawstatus!")
end

local function scene_bufs()
  local out = {}
  for kind, b in pairs(S.bufs) do
    if api.nvim_buf_is_valid(b) then out[kind] = b end
  end
  if not next(out) then
    local b = api.nvim_get_current_buf()
    if kind_of(b) then out[kind_of(b)] = b end
  end
  return out
end

function M.scene_save(name)
  local snap = {}
  for kind, b in pairs(scene_bufs()) do snap[kind] = api.nvim_buf_get_lines(b, 0, -1, false) end
  S.scenes[name] = snap
  vim.notify("jynx: saved scene " .. name)
end

function M.scene_load(name)
  local snap = S.scenes[name]
  if not snap then return vim.notify("jynx: no scene " .. name, vim.log.levels.WARN) end
  for kind, b in pairs(scene_bufs()) do
    if snap[kind] then
      api.nvim_buf_set_lines(b, 0, -1, false, snap[kind])
      M.eval(b)
    end
  end
end

function M.toggle_window()
  local cur = api.nvim_get_current_buf()
  local target = kind_of(cur) == "music" and S.bufs.vis or S.bufs.music
  local w = target and win_of(target)
  if w then api.nvim_set_current_win(w) end
end

-- The label of the pattern block around the cursor: (row, muted, name) or nil.
local function block_label(buf)
  local row = api.nvim_win_get_cursor(0)[1]
  local lines = api.nvim_buf_get_lines(buf, 0, row, false)
  for r = row, 1, -1 do
    local mute, name = lines[r]:match("^(_?)([%w_$]+)%s*:")
    if name then return r, mute == "_", name end
    if r < row and lines[r]:match("^%S") and not lines[r]:match("^//") and not lines[r]:match("^%.") then
      return nil
    end
  end
end

-- Strudel mutes a pattern with a leading _ (drums: -> _drums:)
function M.toggle_mute()
  local buf = api.nvim_get_current_buf()
  local row, muted = block_label(buf)
  if not row then return vim.notify("jynx: no `name:` pattern here") end
  if muted then
    api.nvim_buf_set_text(buf, row - 1, 0, row - 1, 1, {})
  else
    api.nvim_buf_set_text(buf, row - 1, 0, row - 1, 0, { "_" })
  end
end

-- Open the laptop-keyboard instrument in a split, playing the pattern under the cursor.
function M.keyboard()
  local buf = api.nvim_get_current_buf()
  local row, muted, name = block_label(buf)
  local track = (name and not muted and name ~= "$") and name or nil
  if row and not muted and not track then  -- a $: pattern: the engine knows it as p1, p2, ...
    for n, line in pairs(S.track_lines or {}) do
      if line == row then track = n end
    end
  end
  track = track or "keys"
  local py = S.root .. "/.venv/bin/python"
  if vim.fn.executable(py) == 0 then py = "python3" end
  local prev = api.nvim_get_current_win()
  vim.cmd("botright 12split | enew")
  local win = api.nvim_get_current_win()
  vim.fn.jobstart({ py, "-m", "engine.keys", track }, {
    term = true,
    cwd = S.root,
    env = { JYNX_PORT = tostring(S.port) },
    on_exit = function()
      vim.schedule(function()
        if api.nvim_win_is_valid(win) then api.nvim_win_close(win, true) end
        if api.nvim_win_is_valid(prev) then api.nvim_set_current_win(prev) end
      end)
    end,
  })
  local wo = vim.wo[win]
  wo.number, wo.relativenumber, wo.signcolumn = false, false, "no"
  wo.statusline = "%#JynxMode# KEYS %* playing " .. track .. " · esc to close"
  vim.cmd("startinsert")
end

-- Put the phrase captured by the keyboard (on the clipboard) into the pattern string here.
function M.paste_phrase()
  local text = vim.fn.getreg("+"):gsub("%s+$", "")
  if text == "" then return vim.notify("jynx: nothing captured yet (press Enter in the keyboard)") end
  if not edit.replace_string(text) then vim.notify("jynx: put the cursor on a pattern string") end
end

M.connect = function(tries) connect(tries or 20) end

function M.start()
  local music = api.nvim_get_current_buf()
  local kind = kind_of(music)
  if not kind then
    return vim.notify("jynx: open a .jynx or .vis file first", vim.log.levels.ERROR)
  end
  S.bufs[kind] = music
  local music_win = api.nvim_get_current_win()

  if vim.env.JYNX_ENGINE == "external" then
    vim.notify("jynx: using the engine in another terminal (start it with ./jynx engine)")
  elseif not S.job then
    vim.cmd("botright vnew")
    local term_win = api.nvim_get_current_win()
    api.nvim_win_set_width(term_win, math.floor(vim.o.columns * 0.5))
    local py = S.root .. "/.venv/bin/python"
    if vim.fn.executable(py) == 0 then py = "python3" end
    S.job = vim.fn.jobstart({ py, "-m", "engine" }, {
      term = true,
      cwd = S.root,
      env = { JYNX_PORT = tostring(S.port) },
      on_exit = function() S.job = nil end,
    })
    local wo = vim.wo[term_win]
    wo.number, wo.relativenumber, wo.signcolumn, wo.cursorline = false, false, "no", false
    wo.statusline = "%#JynxMode# ENGINE %* audio + visuals"
    api.nvim_set_current_win(music_win)
  end

  -- `jynx music` / `jynx vis` open one file per terminal: no split
  local name = api.nvim_buf_get_name(music)
  local visfile = name:gsub("%.jynx$", ".vis")
  if kind == "music" and not vim.env.JYNX_ONLY and visfile ~= name and not win_of(vim.fn.bufnr(visfile)) then
    vim.cmd("belowright split " .. vim.fn.fnameescape(visfile))
    api.nvim_win_set_height(0, math.max(math.floor(vim.o.lines * 0.3), 8))
    S.bufs.vis = api.nvim_get_current_buf()
    api.nvim_set_current_win(music_win)
  end
  connect(vim.env.JYNX_ENGINE == "external" and math.huge or 60)
end

---------------------------------------------------------------------- statusline

function M.statusline()
  local win = vim.g.statusline_winid or api.nvim_get_current_win()
  local buf = api.nvim_win_get_buf(win)
  local kind = kind_of(buf) or "?"
  local focused = win == api.nvim_get_current_win()
  local parts = {}

  local label = kind == "music" and " MUSIC " or " VIS "
  parts[#parts + 1] = (focused and "%#JynxMode#" or "%#JynxModeNC#") .. label .. "%*"

  if focused then
    if edit.knob then
      local k = edit.knob
      parts[#parts + 1] = string.format("%%#JynxKnob# KNOB %s %s %%*%%#JynxDim# h/l fine · j/k coarse · 0/$ min/max · esc done%%*",
        k.key, tostring(k.value))
    else
      local m = api.nvim_get_mode().mode
      if m:sub(1, 1) == "i" then
        parts[#parts + 1] = "%#JynxDirty# INSERT · silent until <CR> %*"
      elseif m:match("^[vV\22]") then
        parts[#parts + 1] = "%#JynxDim# VISUAL %*"
      end
    end
  end

  if not S.connected then
    parts[#parts + 1] = "%#JynxOff# ○ offline %*"
  elseif (S.errors[kind] or 0) > 0 then
    parts[#parts + 1] = "%#JynxOff# ✗ error — old code still playing %*"
  elseif S.dirty[buf] then
    parts[#parts + 1] = "%#JynxDirty# ◐ rehearsing · <CR> to play %*"
  else
    parts[#parts + 1] = "%#JynxLive# ● live %*"
  end
  if S.hushed then parts[#parts + 1] = "%#JynxOff# HUSH %*" end

  parts[#parts + 1] = "%="
  if S.every then
    local left = S.every.n - (S.cycle % S.every.n)
    parts[#parts + 1] = string.format("%%#JynxKnob# every %d @%s (in %d) %%*", S.every.n, S.every.reg, left)
  end
  if vim.fn.reg_recording() ~= "" then
    parts[#parts + 1] = "%#JynxOff# recording @" .. vim.fn.reg_recording() .. " %*"
  end
  parts[#parts + 1] = string.format("%%#JynxDim# cycle %d %%*", S.cycle)
  return table.concat(parts, " ")
end

---------------------------------------------------------------------- per-buffer setup

function M.buffer_maps(buf)
  local o = function(desc, extra)
    return vim.tbl_extend("force", { buffer = buf, silent = true, desc = desc }, extra or {})
  end
  local map = vim.keymap.set
  map("n", "<CR>", function() M.eval(buf) end, o("jynx: evaluate"))
  map("n", "<leader>v", M.toggle_window, o("jynx: switch music/visuals"))
  map("n", "<leader>h", M.hush, o("jynx: hush"))
  map("n", "gs", edit.knob_enter, o("jynx: KNOB mode"))
  map("n", "<leader>k", M.keyboard, o("jynx: laptop keyboard instrument"))
  map("n", "<leader>m", function()
    local c = vim.fn.getcharstr()
    if c:match("^%w$") then M.scene_save(c) end
  end, o("jynx: save scene"))
  map("n", "<leader>'", function()
    local c = vim.fn.getcharstr()
    if c:match("^%w$") then M.scene_load(c) end
  end, o("jynx: load scene"))

  if kind_of(buf) ~= "music" then return end
  map("n", "<leader>_", M.toggle_mute, o("jynx: mute/unmute pattern (_ prefix)"))
  map("n", "<leader>p", M.paste_phrase, o("jynx: paste played phrase into pattern"))
  local ops = { [">"] = "rotr", ["<"] = "rotl", R = "rev" }
  for key, kind in pairs(ops) do
    map("n", "g" .. key, function() return edit.operator(kind) end, o("jynx: " .. kind, { expr = true }))
    map("n", "g" .. key .. key, function() edit.line(kind) end, o("jynx: " .. kind .. " line"))
    map("x", "g" .. key, function() edit.visual(kind) end, o("jynx: " .. kind))
  end
  map("n", "g*", edit.double, o("jynx: double step"))
  map("n", "g/", edit.halve, o("jynx: halve step"))
  map("n", "g?", edit.maybe, o("jynx: toggle ? on step"))
  map("n", "g~", edit.alternate, o("jynx: alternate step with rest"))
  map({ "x", "o" }, "is", ":<C-u>lua require'jynx.edit'.select_step(false)<CR>", o("inside step"))
  map({ "x", "o" }, "as", ":<C-u>lua require'jynx.edit'.select_step(true)<CR>", o("around step"))
  map({ "n", "x", "o" }, "]s", function() edit.jump_step(1) end, o("next step"))
  map({ "n", "x", "o" }, "[s", function() edit.jump_step(-1) end, o("previous step"))
end

local function attach(buf)
  local kind = kind_of(buf)
  S.bufs[kind] = S.bufs[kind] or buf
  vim.bo[buf].commentstring = kind == "music" and "// %s" or "-- %s"
  vim.wo.statusline = "%!v:lua.require'jynx'.statusline()"
  M.buffer_maps(buf)
  local g = api.nvim_create_augroup("jynx_buf_" .. buf, { clear = true })
  -- edits are silent until <CR> (KNOB, :Every and scenes evaluate on their own)
  api.nvim_create_autocmd({ "TextChanged", "TextChangedI" }, { group = g, buffer = buf, callback = function()
    if not edit.knob then S.dirty[buf] = true end
    M.draw_sliders(buf)
    vim.cmd("redrawstatus")
  end })
  api.nvim_create_autocmd({ "BufLeave", "InsertEnter" }, { group = g, buffer = buf, callback = function()
    if edit.knob and edit.knob.buf == buf then edit.knob_exit() end
  end })
  api.nvim_create_autocmd({ "BufWinEnter", "WinEnter" }, { group = g, buffer = buf, callback = function()
    vim.wo.statusline = "%!v:lua.require'jynx'.statusline()"
  end })
  api.nvim_create_autocmd({ "ModeChanged", "RecordingEnter", "RecordingLeave" }, { group = g, buffer = buf,
    callback = function() vim.schedule(function() vim.cmd("redrawstatus") end) end })
  M.draw_sliders(buf)
end

local function highlights()
  local hl = function(name, spec) spec.default = true; api.nvim_set_hl(0, name, spec) end
  hl("JynxHit", { bg = "#f5c2e7", fg = "#11111b", bold = true })
  hl("JynxFlash", { bg = "#3b3552" })
  hl("JynxKnob", { fg = "#f9e2af", bold = true })
  hl("JynxKnobLine", { bg = "#2e2a1f" })
  hl("JynxSlider", { fg = "#6c7086" })
  hl("JynxMode", { bg = "#cba6f7", fg = "#11111b", bold = true })
  hl("JynxModeNC", { bg = "#45475a", fg = "#cdd6f4" })
  hl("JynxLive", { fg = "#a6e3a1", bold = true })
  hl("JynxDirty", { fg = "#f9e2af", bold = true })
  hl("JynxOff", { fg = "#f38ba8", bold = true })
  hl("JynxDim", { fg = "#7f849c" })
end

function M.setup(opts)
  S.root = opts.root
  vim.filetype.add({ extension = { jynx = "jynx", vis = "jynxvis" } })
  highlights()
  local g = api.nvim_create_augroup("jynx", { clear = true })
  api.nvim_create_autocmd("ColorScheme", { group = g, callback = highlights })
  api.nvim_create_autocmd("FileType", { group = g, pattern = { "jynx", "jynxvis" },
    callback = function(ev) attach(ev.buf) end })
  api.nvim_create_autocmd("VimLeavePre", { group = g, callback = function()
    if S.job then pcall(vim.fn.jobstop, S.job) end
  end })

  local cmd = api.nvim_create_user_command
  cmd("JynxStart", M.start, { desc = "start the jynx engine and layout" })
  cmd("JynxEval", function() M.eval(0) end, {})
  cmd("Hush", M.hush, { desc = "toggle silence" })
  cmd("Every", function(a) M.every(a.fargs) end, { nargs = "*", desc = ":Every 4 @q  |  :Every off" })
  cmd("Scene", function(a)
    local verb, name = a.fargs[1], a.fargs[2]
    if verb == "save" and name then M.scene_save(name)
    elseif name == nil and verb then M.scene_load(verb)
    elseif verb == "load" and name then M.scene_load(name)
    else vim.notify(":Scene save <name>  |  :Scene <name>") end
  end, { nargs = "+" })
end

return M
