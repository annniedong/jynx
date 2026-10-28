if exists("b:current_syntax") | finish | endif

syn keyword visSource   osc noise shape rings grid solid
syn keyword visGeometry kaleid rotate scale repeat scroll warp pixelate
syn keyword visColor    color invert bright contrast hue thresh posterize feedback
syn keyword visBlend    add mult diff
syn keyword visVar      t cycle sin cos abs pi
syn match   visNumber   "\v(\w)@<!-?\d*\.?\d+"
syn match   visOp       "[*+/-]"
syn match   visPipe     "|"
syn keyword visPort     vis screen
syn match   visComment  "--.*$"

hi def link visSource   Function
hi def link visGeometry Identifier
hi def link visColor    Type
hi def link visBlend    Keyword
hi def link visVar      Special
hi def link visNumber   Number
hi def link visOp       Operator
hi def link visPipe     Operator
hi def link visPort     Special
hi def link visComment  Comment

let b:current_syntax = "jynxvis"
