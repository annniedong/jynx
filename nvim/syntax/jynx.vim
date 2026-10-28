if exists("b:current_syntax") | finish | endif

" Strudel-style code: labels, calls, numbers
syn match   jynxLabel    "^[A-Za-z0-9$][A-Za-z0-9_$]*\ze\s*:"
syn match   jynxMuted    "^_[A-Za-z0-9_$]*\ze\s*:"
syn match   jynxHead     "\<\(s\|sound\|note\|n\)\ze("
syn match   jynxMethod   "\.\zs[A-Za-z_]\w*\ze\s*("
syn keyword jynxGlobal   setcpm setcps hush let const var
syn keyword jynxSlider   slider
syn match   jynxGlobal   "\<run\ze("
syn keyword jynxSignal   sine cosine saw isaw tri square rand irand perlin
syn match   jynxArrow    "=>"
syn match   jynxParam    "\<\h\w*\ze\s*=>"
syn match   jynxNumber   "\v(\w)@<!-?\d*\.?\d+"

" mini-notation inside strings
syn region  jynxString   start=+"+ end=+"+ oneline contains=@jynxMini
syn region  jynxString   start=+'+ end=+'+ oneline contains=@jynxMini
syn region  jynxString   start=+`+ end=+`+ oneline contains=@jynxMini
syn cluster jynxMini     contains=jynxMiniOp,jynxMiniBracket,jynxRest,jynxDrum,jynxWave,jynxMiniNum
syn match   jynxMiniBracket contained "[\[\]<>,()]"
syn match   jynxMiniOp   contained "[*/!?@_]"
syn match   jynxRest     contained "[~]\|\s\zs-\ze\(\s\|$\)"
syn match   jynxMiniNum  contained "\v(\w)@<!-?\d*\.?\d+"
syn keyword jynxDrum     contained bd sd hh oh cp rim lt mt ht cb cr
syn keyword jynxWave     contained sawtooth saw square triangle tri sine supersaw bell pluck sub

syn match   jynxComment  "//.*$"

hi def link jynxLabel       Function
hi def link jynxMuted       Comment
hi def link jynxHead        Keyword
hi def link jynxMethod      Identifier
hi def link jynxGlobal      Keyword
hi def link jynxSlider      Special
hi def link jynxSignal      Constant
hi def link jynxArrow       Operator
hi def link jynxParam       Identifier
hi def link jynxNumber      Number
hi def link jynxString      String
hi def link jynxMiniBracket Delimiter
hi def link jynxMiniOp      Operator
hi def link jynxRest        Comment
hi def link jynxMiniNum     Number
hi def link jynxDrum        Type
hi def link jynxWave        Type
hi def link jynxComment     Comment

let b:current_syntax = "jynx"
