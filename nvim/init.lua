-- Standalone config used by the `jynx` launcher (your own ~/.config/nvim is untouched).
local root = vim.fn.fnamemodify(debug.getinfo(1, "S").source:sub(2), ":p:h:h")
vim.opt.rtp:prepend(root .. "/nvim")

vim.g.mapleader = " "
vim.opt.termguicolors = true
vim.opt.number = true
vim.opt.cursorline = true
vim.opt.signcolumn = "yes"
vim.opt.expandtab = true
vim.opt.shiftwidth = 2
vim.opt.tabstop = 2
vim.opt.clipboard = "unnamedplus"
vim.opt.laststatus = 2
vim.opt.showmode = false
vim.opt.splitright = true
vim.opt.splitbelow = true
vim.opt.swapfile = false
vim.opt.updatetime = 200

require("jynx").setup({ root = root })
