#!/usr/bin/env python3
"""Build the combined Zeus + Poseidon exe.

  original exe (2.1.4.0)
  + runtime-switchable hi-res table made from the 13 widescreen exes   (.wpat section + hooks)
  + animation fix (70 bytes, from patch_zeus.exe)
  -> dist/Zeus.exe

Every step is verified against the shipped files (see verify()).
"""
import os, struct, sys, zlib
from pathlib import Path

import capstone

sys.path.insert(0, str(Path(__file__).parent))
from asm import assemble
import clink
import mapfit

ROOT = Path(__file__).resolve().parent.parent
EXES = ROOT / 'exes'
WS = EXES / 'Widescreen Patches'
ORIG = EXES / 'Zeus_original_game.exe'
ANIM = EXES / 'zeus_poseidon_widescreen_animation_patch' / 'patch_zeus.exe'
DIST = ROOT / 'dist'

IMAGE_BASE = 0x400000
TEXT_FILE, TEXT_END = 0x1000, 0x1d8000          # .text raw range (VA 0x401000..)
va2off = lambda va: va - IMAGE_BASE - 0x1000 + TEXT_FILE     # .text only

# (folder name, exe path). Entry 0 must be the original 1024x768 (original bytes).
VARIANTS = [
    ('1024x768', None),
    ('1280x720',  WS / '1280x720/Zeus.exe'),
    ('1280x800',  WS / '1280x800/Zeus.exe'),
    ('1280x1024', WS / '1280x1024/Zeus.exe'),
    ('1360x768',  WS / '1360x768/Version 2/Zeus.exe'),   # same size as v1; v2 behaves like the 1440+ builds
    ('1360x768v1', WS / '1360x768/Version 1/Zeus.exe'),   # own art folder: its viewport (and so the map fit) differs from v2
    ('1440x900',  WS / '1440x900/Zeus.exe'),
    ('1600x900',  WS / '1600x900/Zeus.exe'),
    ('1680x1050', WS / '1680x1050/Zeus.exe'),
    ('1920x1080', WS / '1920x1080/Zeus.exe'),
    ('1920x1200', WS / '1920x1200/Zeus.exe'),
    ('2048x1152', WS / '2048x1152/Zeus.exe'),
    ('2560x1440', WS / '2560x1440/Zeus.exe'),
    ('2560x1600', WS / '2560x1600/Zeus.exe'),
]
FILTER = False   # True: F7 skips resolutions larger than the desktop (Wine may report a small desktop)
# VAs of the width / height immediates in the display-init code (same in every build)
W_VA, H_VA = 0x5051fa, 0x505212
PIN_VA, PIN_END_VA = 0x5051f8, 0x505228    # the loader's mode-3-only `pin a W x H image to (0,0)` code, replaced by ImgPin (every mode)
MODE_VW = {14: (0x58e2ae, 600), 15: (0x58e292, 480)}   # fit-table slots after the 14 resolutions: 800x600 (mode 2), 640x480 (mode 1); imm32 of the viewport width, screen height

# the 'R' polls (rotate building): (call [GetAsyncKeyState], the `push 0x52` feeding it; only mov/xor in between)
KPOLL_PAIRS = [(0x41bcb2, 0x41bcb0), (0x451c89, 0x451c87), (0x4532ba, 0x4532b6), (0x456e7c, 0x456e72),
               (0x4e51ab, 0x4e51a1), (0x53dea3, 0x53dea1), (0x56e682, 0x56e680), (0x5afd85, 0x5afd83)]
KPOLL_SITES = [c for c, _ in KPOLL_PAIRS]

# --- hooks into the original code: (va, expected original bytes, replacement builder)
HOOKS = {
    'init':    (0x5c3932, bytes.fromhex('e849 80f4 ff'.replace(' ', ''))),       # call 0x50b980
    'setmode': (0x50bd29, bytes.fromhex('e892ffffff')),                          # call 0x50bcc0
    'revert':  (0x50c05d, bytes.fromhex('e85efcffff')),                          # call 0x50bcc0
    'good':    (0x50c0d3, bytes.fromhex('890d40c1f900')),                        # mov [0xf9c140], ecx
    'maploader': (0x5acd06, b'\xe8' + struct.pack('<i', 0x504de0 - (0x5acd06 + 5))),                  # call JPG loader (world map)
    'f7':      (0x5c542d, bytes.fromhex('6a03e8fcf5ffff')),                      # push 3 / call ChangeMode
    'sdm':     (0x434029, bytes.fromhex('85c00f8497000000')),                  # test eax,eax / je 0x4340c8
    'dlgdraw': (0x59af60, bytes.fromhex('a120c82b01')),                          # Display-options dialog draw
    'gettext': (0x507bf0, bytes.fromhex('8b4424048b1564 9ff200'.replace(' ', ''))),  # GetText(group, idx) prologue
    # world-map entry wrappers (scale cities/routes around each map draw / hit-test): va -> WRAPS[name]
    'wrapdraw':  (0x599da0, bytes.fromhex('83ec54e858fcf6ff')),
    'wrapclick': (0x5c1d90, bytes.fromhex('83ec2c5355')),
    'wraphit1':  (0x407360, bytes.fromhex('83ec10a138a7f200')),
    'wraphit2':  (0x407140, bytes.fromhex('a008a7f200')),
    'wraphit3':  (0x406ca0, bytes.fromhex('a008a7f200')),
    'wraphit4':  (0x421af0, bytes.fromhex('a008a7f200')),
    'wraphit5':  (0x421c70, bytes.fromhex('0fbe05b0a7f200')),
    'wraphit6':  (0x408a60, bytes.fromhex('a1882fda00')),
    'wrapwrite': (0x5cfde3, bytes.fromhex('558bec81ec14040000')),     # CRT _write: restore scaled data first
    # keyboard controls
    'keydown': (0x5c4e2c, bytes.fromhex('8d4bf883f96f')),                        # lea ecx,[ebx-8] / cmp ecx,0x6f  (WM_KEYDOWN dispatch)
    'opendlg': (0x401db0, bytes.fromhex('a194187800')),                          # mov eax,[0x781894]  (opens screen 0x0a)
    'intro':   (0x5c3b66, bytes.fromhex('8b15b0a6f200')),                        # intro video: mov edx,[0xf2a6b0] (after the hiq/low test)
    'blit':    (0x417ad6, bytes.fromhex('ff1538825d00')),                        # call [BinkCopyToBuffer] in the video draw 0x4179e0
    'logo':    (0x5acf50, bytes.fromhex('81ec14010000')),                        # draw_image prologue: sub esp,0x114
    'imgpin':  (PIN_VA, bytes.fromhex('81fe00040000751083f803750bc78424a80000000000000081fb00030000750983f803750433edeb078bac24ac000000')),   # cmp esi,1024 ... mov ebp,[esp+0xac]
    'imghook': (0x505127, bytes.fromhex('8b0d24abf200')),                        # mov ecx,[0xf2ab24] in the image loader (after the JPG decode)
    'gwidth':  (0x512870, bytes.fromhex('8b4424048b048550d9f900')),              # text group width getter
    **{'kpoll%d' % i: (va, bytes.fromhex('ff15d4815d00')) for i, va in enumerate(KPOLL_SITES)},   # call [GetAsyncKeyState] after push 0x52
    # city zoom
    'zset':    (0x51cc44, b'\xe8' + struct.pack('<i', 0x449e50 - (0x51cc44 + 5))),   # call view setter (the city view, in 0x51cc00)
    'zdraw':   (0x51cac4, b'\xe8' + struct.pack('<i', 0x44a830 - (0x51cac4 + 5))),   # call city draw (in 0x51cab0)
    'zmouse':  (0x50b5de, bytes.fromhex('893d38a7f200893534a7f200')),                # mov [0xf2a738],edi / mov [0xf2a734],esi (mouse x / y)
    'zwheel':  (0x5c593f, bytes.fromhex('a0dda6f200')),                              # WM_MOUSEWHEEL: mov al,[0xf2a6dd]
    'zwidth':  (0x58f5a8, b'\xe8' + struct.pack('<i', 0x44a7c0 - (0x58f5a8 + 5))),   # call view pixel width (banner centring, in 0x58f130)
    'charhook': (0x5c560f, bytes.fromhex('a0dda6f200')),                             # WM_CHAR handler: mov al,[0xf2a6dd]
    'zcost':   (0x5140c3, b'\xe8' + struct.pack('<i', 0x4542b0 - (0x5140c3 + 5))),   # call build-cost text (in the city frame 0x514020)
}
WRAPS = {'wrapdraw': 'WrapDraw', 'wrapclick': 'WrapClick', 'wraphit1': 'WrapHit1', 'wraphit2': 'WrapHit2',
         'wraphit3': 'WrapHit3', 'wraphit4': 'WrapHit4', 'wraphit5': 'WrapHit5', 'wraphit6': 'WrapHit6', 'wrapwrite': 'WrapWrite'}
VW_VA = 0x58e2ca       # imm32 of  mov [0x10dff34], vw   (world-map viewport width) in the mode-3 branch
CSRC = Path(__file__).parent / 'cmod/mapcomp.c'
PATH_CAP = 0x80000     # route points the fit can shadow (BSS after the stub blob, 4 bytes each)


# Display-options dialog: (button index = widget index = draw order, 1-based)
DLG_TABLE = 0x5ed528          # original 4-record click table (28 bytes each)
DLG_TABLE_PUSHES = [0x5154d3, 0x5154f1, 0x51550f]     # push 4 / push 0x5ed528  (7 bytes each)
H_F5, H_F67, H_CANCEL, H_NOP = 0x401de0, 0x401e10, 0x401f70, 0x401000


def dialog_layout(entries):
    """rows: (sx, sy, len, label_idx, handler, param, name).  800x600 + every table entry in two columns."""
    rows = [(0xa0, 0x88, 0xe, -1, H_F5, 1, None)]
    items = [('800 x 600', H_F67, 2)]
    for r, (name, w, h, _) in enumerate(entries):
        label = '%d x %d' % (w, h)
        if name != '%dx%d' % (w, h):
            label += ' alt'
        items.append((label, 'RES', 100 + r))
    for k, (label, hnd, param) in enumerate(items):
        row, col = 1 + k // 2, k % 2
        rows.append((0x70 + 0xa0 * col, 0x88 + 24 * row, 0xa, 100 + k, hnd, param, label))
    rows.append((0xa0, 0x88 + 24 * (1 + (len(items) + 1) // 2), 0xe, 5, H_CANCEL, 1, None))
    return rows, [it[0] for it in items]


def dialog_asm(rows, labels):
    dr, wd = [], []
    for sx, sy, ln, idx, hnd, param, name in rows:
        dr.append('.long %d, %d, %d, %d, %d, %d, %d, 0\n' % (sx, sy, ln, idx, sx, sy + 4, ln * 16))
        h = 'BASE + (ResClick - start)' if hnd == 'RES' else hnd
        wd.append('.short %d, %d, %d, %d\n.long %s, %d, 0x01000000, %d, 0\n' % (
            sx + 0x10, sy, sx + ln * 16 - 0x10, sy + 0x14, h, H_NOP, param))
    st = ''.join('.set STR%d, BASE + (str%d - start)\n' % (i, i) for i in range(len(labels)))
    ptrs = '.long ' + ', '.join('STR%d' % i for i in range(len(labels))) + '\n'
    strs = ''.join('str%d: .asciz "%s"\n' % (i, l) for i, l in enumerate(labels))
    return ''.join(dr), ''.join(wd), st + ptrs, strs


# Options > Keyboard Controls: the Display dialog (screen 0x0a) shows a second page with one button per action.
ACTIONS = [('Map scrolling', 0),                                    # row 0: not a key: "< WASD >" / "< Arrow keys >" (ScrollToggle)
           ('Rotate building', 0x52),
           ('Map anti-clockwise', 0x21), ('Map clockwise', 0x22),     # PgUp / PgDn: WM_KEYDOWN handlers 0x5c50e3 / 0x5c518d
           ('Pause', 0x50), ('Overlay', 0x20), ('Overview', 0xbd),    # P / Space / '-': WM_CHAR (0x5c560f..)
           ('Population', 0x31), ('Husbandry', 0x32), ('Industry', 0x33), ('Distribution', 0x34), ('Health and safety', 0x35),
           ('Administration', 0x36), ('Culture', 0x37), ('Mythology', 0x38), ('Military', 0x39), ('Aesthetics', 0x30)]   # control-panel tabs: 1..9, 0 (WM_CHAR)
KROWS_COL = 9                          # rows per column of the keyboard page
KSCREENS = (0x01, 0x15, 0x20, 0x48, 0x58)     # screens whose WM_KEYDOWN codes go through the key map (city + world map; tune after testing)
KSCR_N = 0x59                           # screen ids are < 0x59
KNAMES = ['Page Up', 'Page Down', 'End', 'Home', 'Left', 'Up', 'Right', 'Down', '?', '?', '?', '?', 'Insert', 'Delete']   # VK 0x21..0x2e
KFH = 0x16                              # keyboard page frame height (blocks)
KFW = 0x30                              # keyboard page frame width (blocks; the Display page has 0x16): twice as wide, centred on the same point
KFX = 0x60 - (KFW - 0x16) * 8           # frame x offset
KCOLW, KLBLW, KBTN_LEN = 360, 216, 8             # keyboard page: column pitch, label width (button starts after it), key button length (blocks)
KHINTX, KHINTY = KFX + 24, 0x194        # hint line under the buttons
OPT_TABLE = 0x5efd20                    # the Options pull-down's 7 original item records (16 bytes: y, text idx, handler, 0, 0)
OPT_DESCS = {0x5eff10: 7, 0x5eff40: 3, 0x5eff80: 2, 0x5f0010: 2}     # Options menu descriptors (VA) -> original item count
# Export City Image: page 2 of the Display dialog (screen 0x0a): file-name field, Save, Cancel
EFW, EFH = 0x28, 0x0f                  # frame width / height (blocks)
EFX = 0x60 - (EFW - 0x16) * 8          # frame x offset (centred like the other pages)
ELABX, EBARX, EBAR_LEN, EHINTY = EFX + 32, EFX + 32 + 112, 0x18, 0x100    # "File name:" x, field bar x / length (blocks), hint line y
KFILE_AT = 5                            # File menu: the new item goes in front of the last one (Exit); the original table is at 0x5efcc0 (6 records)
FILE_DESC, FILE_TABLE, FILE_N = 0x5eff00, 0x5efcc0, 6
# map-picking reads of the mouse (mouse -> cell 0x44a270, hover rectangle 0x405d40, footprint test 0x456ce0): their absolute operands [0xf2a738] (x) / [0xf2a734] (y)
# are repointed at the zoom-mapped copies zvx / zvy; the raw [0xf2a738] / [0xf2a734] stay for all UI code (sidebar buttons, menus, dialogs, tooltips)
ZV_READS = {0x405d40: 'x', 0x405d61: 'y', 0x44a274: 'x', 0x44a2b0: 'y', 0x456da9: 'y', 0x456db1: 'x'}
MOUSE_X, MOUSE_Y = 0xf2a738, 0xf2a734
KOPT_AT = 2                             # index of the new Options item (Display, Sound, KEYBOARD CONTROLS, Speed ...)
OPT_WRITERS = [0x402673, 0x40268c, 0x51174e, 0x569307, 0x5c468a, 0x5c469b]   # code that rewrites the text idx words of records 4 / 6
KLBLSZ = 48


ARROW_DX, ARROW_UX, ARROW_TX, ARROW_TW = 200, 320, 224, 96   # "Map scrolling" row: down arrow, up arrow, mode text (x from the label's x, width)


def keys_layout(nl):
    """rows: (sx, sy, len, label_idx, handler, param, label_x, text) - draw records; text = None or (tx, ty, tw) when the label is not on the button.
    Label idx: 100+nl = title/menu text, +1 reset, +2 close, +3.. one per action.  Actions fill columns of KROWS_COL rows; the action name is drawn at
    label_x.  Row 0 ("Map scrolling") has no button: its mode text sits between two arrow sprites (the Speed options' red arrows); its button sprite is
    drawn off screen (sy = -1000) and its click record is dead; the arrows are `extra` click records after the page's own rows.
    Returns (rows, extra widget rows, arrow sprite table asm)."""
    rows, extra, arrows = [], [], ''
    for i in range(len(ACTIONS)):
        lx = KFX + 24 + (i // KROWS_COL) * KCOLW
        sy = 0x88 + 24 * (i % KROWS_COL)
        if i == 0:
            rows.append((lx + ARROW_TX, -1000, 1, 100 + nl + 3, None, 0, lx, (lx + ARROW_TX, sy + 4, ARROW_TW)))
            ay = sy - 2
            for dx, grp, par in ((ARROW_DX, 0x21f, 1), (ARROW_UX, 0x220, 0)):
                arrows += '.short %d, %d\n.long %d, 0x18, %d, 0x04000000, %d, 0\n' % (lx + dx, ay, grp, H_NOP, par)
                extra.append((lx + dx, ay, 'ScrollToggle', par))
        else:
            rows.append((lx + KLBLW, sy, KBTN_LEN, 100 + nl + 3 + i, 'KeyRowClick', 10 + i, lx, None))
    rows.append((0xa0, 0x88 + 24 * KROWS_COL, 0xe, 100 + nl + 1, 'KeyReset', 1, 0, None))
    rows.append((0xa0, 0x88 + 24 * (KROWS_COL + 1), 0xe, 100 + nl + 2, 'KeysClose', 1, 0, None))
    return rows, extra, arrows


def export_layout(nl):
    """draw / click rows of page 2.  Label idx = 100 + STRPTRS index of (title, Save, Cancel, empty) = nl + 3 + len(ACTIONS) + 0..3."""
    e = nl + 3 + len(ACTIONS)
    return [(EBARX, 0x88, EBAR_LEN, 100 + e + 3, None, 0, 0, None),
            (0xa0, 0xb8, 0xe, 100 + e + 1, 'ExpSave', 1, 0, None),
            (0xa0, 0xd8, 0xe, 100 + e + 2, 'ExpCancel', 1, 0, None)]


def rows_asm(rows, pad_to, extra=()):
    dr, wd = [], []
    sym = lambda h: 'BASE + (%s - start)' % h if isinstance(h, str) else str(h)
    for sx, sy, ln, idx, hnd, param, lx, text in rows:
        tx, ty, tw = text if text else (sx, sy + 4, ln * 16)
        dr.append('.long %d, %d, %d, %d, %d, %d, %d, %d\n' % (sx, sy, ln, idx, tx, ty, tw, lx))
        if hnd is None:                                  # no click area
            wd.append('.short 1, 1, 0, 0\n.long %d, %d, 0x01000000, 0, 0\n' % (H_NOP, H_NOP))
        else:
            wd.append('.short %d, %d, %d, %d\n.long %s, %d, 0x01000000, %d, 0\n' % (
                sx + 0x10, sy, sx + ln * 16 - 0x10, sy + 0x14, sym(hnd), H_NOP, param))
    for x, y, hnd, param in extra:                       # click areas without a drawn button (the arrows are sprites)
        wd.append('.short %d, %d, %d, %d\n.long %s, %d, 0x01000000, %d, 0\n' % (x, y, x + 24, y + 24, sym(hnd), H_NOP, param))
    for _ in range(pad_to - len(rows) - len(extra)):     # unreachable records (x1 > x2) keep the table at the Display page's length
        wd.append('.short 1, 1, 0, 0\n.long %d, %d, 0x01000000, 0, 0\n' % (H_NOP, H_NOP))
    return ''.join(dr), ''.join(wd)


def keys_data(nl, krows, karrows, orig_data):
    """Data blob for the keyboard feature: key map, strings, tables, and one Options item table per menu descriptor.
    orig_data(va, n) reads the original exe's bytes.  Returns (asm text, strptr tail, [(descriptor va, table label, count)])."""
    o = ['.balign 4', 'kmap: .space 256', 'kinv: .space 256', 'ktmpinv: .space 256', 'kcap: .long -1', 'kpage: .long 0', 'kwant: .long 0',
         'draw_rows: .long 0', 'draw_n: .long 0', 'draw_fh: .long 0', 'draw_fw: .long 0', 'draw_fx: .long 0', 'draw_title: .long 0', 'draw_tdx: .long 0', 'draw_tdy: .long 0', 'kbuf: .space 1024',
         'kname: .asciz "Zeus.keys"', 'kch: .space 4', 'str_qm: .asciz "?"', 'str_press: .asciz "press a key"', 'str_none: .asciz "none"', 'str_wasd: .asciz "WASD"', 'str_arrows: .asciz "Arrow keys"', 'scrl: .byte 0x57, 0x53, 0x41, 0x44', 'scra: .byte 0x26, 0x28, 0x25, 0x27', 'str_space: .asciz "Space"', 'str_minus: .asciz "-"',
         'str_khint: .asciz "Click a button, then press a key.  Backspace = none, Esc = cancel."', 'str_dash: .asciz " - "',
         'str_kmenu: .asciz "Keyboard Controls"', 'str_kreset: .asciz "Reset to defaults"', 'str_kclose: .asciz "Close"',
         'str_expmenu: .asciz "Export City Image"', 'str_etitle: .asciz "Export City Image"', 'str_esave: .asciz "Save"', 'str_ecancel: .asciz "Cancel"', 'str_eempty: .asciz ""',
         'str_efile: .asciz "File name:"', 'str_ehint: .asciz "Saved to your Desktop as "', 'str_epng: .asciz ".png"', 'str_expok: .asciz "City image saved: %s"', 'str_experr: .asciz "Could not save the city image (error %d)"',
         'str_ebad: .asciz "\\\\/:*?\\"<>|"', 'expname: .asciz "City"', '.space 44', 'explen: .long 4', 'exppend: .long 0', 'expres: .long 0', 'zexold: .long 100', 'expdisp: .space 64', 'exphint: .space 96',
         'exppath: .space 340', 'expargs: .space 32']
    for i, (name, _) in enumerate(ACTIONS): o.append('ka%d: .asciz "%s"' % (i, name))
    for i, name in enumerate(KNAMES): o.append('kn%d: .asciz "%s"' % (i, name))
    o.append('kscreens: .byte ' + ','.join('1' if i in KSCREENS else '0' for i in range(KSCR_N)))
    o.append('actvk: .byte ' + ','.join(str(vk) for _, vk in ACTIONS))
    o.append('.balign 4')
    o.append('actptrs: .long ' + ', '.join('BASE + (ka%d - start)' % i for i in range(len(ACTIONS))))
    o.append('knames: .long ' + ', '.join('BASE + (kn%d - start)' % i for i in range(len(KNAMES))))
    o.append('klbl: .space %d' % (KLBLSZ * len(ACTIONS)))
    o.append('krows:\n' + krows)
    o.append('karrows:\n' + karrows)
    tables = []
    for n, (dva, cnt) in enumerate(OPT_DESCS.items()):         # the original records with the new item in KOPT_AT (after Sound options); the ones after it move down 20 px
        o.append('.balign 4\nkopt%d:' % n)
        new = '.short %d, 100\n.long BASE + (KeysOpen - start), 0, 0' % (20 * KOPT_AT)
        for r in range(cnt):
            if r == KOPT_AT: o.append(new)
            w = list(struct.unpack('<4I', orig_data(OPT_TABLE + 16 * r, 16)))
            if r >= KOPT_AT: w[0] += 20                          # y is the low word
            o.append('.long ' + ', '.join(str(x) for x in w))
        if cnt <= KOPT_AT: o.append(new)
        tables.append((dva, 'kopt%d' % n, cnt))
    o.append('.balign 4\nkfile:')                               # File menu: the original records, "Export City Image" in front of the last one (y 20 px lower)
    for r in range(FILE_N):
        if r == KFILE_AT: o.append('.short %d, 100\n.long BASE + (ExpOpen - start), 0, 0' % (20 * KFILE_AT))
        w = list(struct.unpack('<4I', orig_data(FILE_TABLE + 16 * r, 16)))
        if r >= KFILE_AT: w[0] += 20
        o.append('.long ' + ', '.join(str(x) for x in w))
    tail = '.long BASE + (str_kmenu - start), BASE + (str_kreset - start), BASE + (str_kclose - start), ' + ', '.join(
        'BASE + (klbl - start) + %d' % (KLBLSZ * i) for i in range(len(ACTIONS))) + ', BASE + (str_etitle - start), BASE + (str_esave - start), BASE + (str_ecancel - start), BASE + (str_eempty - start)\n'
    return '\n'.join(o) + '\n', tail, tables


def raw_off(blob, va):
    """File offset of a VA in any section of the PE image."""
    pe = struct.unpack_from('<I', blob, 0x3c)[0]
    nsec = struct.unpack_from('<H', blob, pe + 6)[0]
    sec = pe + 24 + struct.unpack_from('<H', blob, pe + 20)[0]
    for i in range(nsec):
        vsz, rva, rsz, rptr = struct.unpack_from('<IIII', blob, sec + 40 * i + 8)
        if rva <= va - IMAGE_BASE < rva + min(vsz, rsz):
            return rptr + va - IMAGE_BASE - rva
    raise ValueError(hex(va))


def rel32(frm, to):
    return struct.pack('<i', to - (frm + 5))


def read_anim_offsets():
    """Decode the 70 (offset, value) records embedded in patch_zeus.exe (CodeFusion patcher)."""
    d = ANIM.read_bytes()
    e = struct.unpack_from('<I', d, 0x3c)[0]
    nsec = struct.unpack_from('<H', d, e + 6)[0]
    optsz = struct.unpack_from('<H', d, e + 20)[0]
    end = 0
    for i in range(nsec):
        o = e + 24 + optsz + 40 * i
        end = max(end, struct.unpack_from('<I', d, o + 20)[0] + struct.unpack_from('<I', d, o + 16)[0])
    ov = d[end:]
    k = ov.index(b'|Zeus.exe') + len(b'|Zeus.exe')
    crc = struct.unpack_from('<I', ov, k)[0]
    rec = ov[k + 4:]
    count = struct.unpack_from('<I', rec, 5)[0]
    assert len(rec) == 9 + 7 * count, (len(rec), count)
    out = []
    for i in range(count):
        typ, off, ln, val = struct.unpack_from('<BIBB', rec, 9 + 7 * i)
        assert typ == 0 and ln == 1
        out.append((off, val))
    return crc, out


TOPBAR_X_VA = 0x59ed3b            # imm32 of `push x` in the top-bar draw (FUN_0059ebe0), W-matching branch
TOPBAR_CMP_VA = 0x59ebe9          # imm32 of that branch's `cmp eax, W`
TOPBAR_W = 2560                   # width of the top-bar sprite (Zeus_Interface 1231) in the UI art pack
BLIT_HOOK_VA, CAVE_VA, CAVE_END_VA = 0x509c58, 0x5d7351, 0x5d73a8


def topbar_x(vw):
    return struct.pack('<i', vw - TOPBAR_W)


def strip_topbar_hack(orig, x):
    """The widescreen patches draw the 838 px top bar at a negative x and fix it up in a blitter hook (+ cave) that reads 838+837 px of
    a single row.  The UI art pack has a TOPBAR_W px bar instead: draw it right-aligned to the viewport edge (x = vw - TOPBAR_W, so it never
    covers the sidebar, whose top 30 rows are drawn first) with the stock blitter (no hook, no cave)."""
    x = bytearray(x)
    x[va2off(BLIT_HOOK_VA):va2off(BLIT_HOOK_VA) + 5] = orig[va2off(BLIT_HOOK_VA):va2off(BLIT_HOOK_VA) + 5]
    x[va2off(CAVE_VA):va2off(CAVE_END_VA)] = orig[va2off(CAVE_VA):va2off(CAVE_END_VA)]
    assert x[va2off(TOPBAR_X_VA) - 1] == 0x68               # push imm32
    vw = struct.unpack_from('<I', x, va2off(VW_VA))[0]
    x[va2off(TOPBAR_X_VA):va2off(TOPBAR_X_VA) + 4] = topbar_x(vw)
    return bytes(x)


def strip_pin(orig, x):
    """The loader's pin code holds the per-resolution W / H immediates; ImgPin replaces it for every build, so the bytes must not differ (no range)."""
    x = bytearray(x)
    x[va2off(PIN_VA):va2off(PIN_END_VA)] = orig[va2off(PIN_VA):va2off(PIN_END_VA)]
    return bytes(x)


def topbar_entry0(orig):
    """Entry 0 (1024x768): the stock code takes the 1024 branch (x = 0, no clip); make it take the same branch as the widescreen builds."""
    x = bytearray(orig)
    x[va2off(TOPBAR_CMP_VA):va2off(TOPBAR_CMP_VA) + 4] = struct.pack('<I', 1024)
    x[va2off(TOPBAR_X_VA):va2off(TOPBAR_X_VA) + 4] = topbar_x(struct.unpack_from('<I', orig, va2off(VW_VA))[0])
    return bytes(x)


def compute_ranges(orig, variants):
    pos = set()
    for v in variants:
        assert len(v) == len(orig)
        for i in range(TEXT_FILE, TEXT_END):
            if v[i] != orig[i]:
                pos.add(i)
    pos = sorted(pos)
    ranges = []
    s = e = pos[0]
    for p in pos[1:]:
        if p - e <= 3:
            e = p
        else:
            ranges.append((s, e + 1)); s = e = p
    ranges.append((s, e + 1))
    return ranges


def build():
    orig = ORIG.read_bytes()
    assert zlib.crc32(orig) == 0x90b9cf84
    raw = [topbar_entry0(orig) if p is None else strip_topbar_hack(orig, p.read_bytes()) for _, p in VARIANTS]
    exes = [strip_pin(orig, x) for x in raw]
    ranges = compute_ranges(orig, exes[1:])
    total = sum(e - s for s, e in ranges)

    # entries: W, H, name[16], bytes
    entries = []
    for (name, _), x in zip(VARIANTS, raw):
        w = struct.unpack_from('<H', x, va2off(W_VA))[0]
        h = struct.unpack_from('<H', x, va2off(H_VA))[0]
        assert name.startswith('%dx%d' % (w, h)), (name, w, h)
        entries.append((name, w, h, b''.join(x[s:e] for s, e in ranges)))
    NUM = len(entries)
    ENTSZ = 24 + total

    # world-map fit table: per entry S16, OX, OY, composite W (= viewport width + 1), composite H
    fits = []
    for (name, _), x, (_, w, h, _) in zip(VARIANTS, exes, entries):
        vw = struct.unpack_from('<I', x, va2off(VW_VA))[0]
        s16, ox, oy, _, vh = mapfit.fit(vw, h)
        fits.append((name, vw, s16, ox, oy, vw + 1, vh))
    assert fits[0][2:5] == (65536, 0, 0), fits[0]      # native view = identity
    # Poseidon sheet windows (kinds 1..3) and map04 centred (kind 4): own scale / anchor per map, same composite size
    kinds = [[f[2:] for f in fits]]
    for kind in (1, 2, 3, 4):
        rows = []
        for (name, _), x, (_, w, h, _), f in zip(VARIANTS, exes, entries, fits):
            s16, ox, oy, _, vh, ok = mapfit.fit_kind(kind, f[1], h)
            if not ok: print('  note: Poseidon map %d at %s does not fill the screen (gap of ocean)' % (kind, name))
            rows.append((s16, ox, oy, f[5], f[6]))
        assert rows[0] == kinds[0][0], rows[0]
        kinds.append(rows)
    for kind in (0, 4):                    # Greece / map 4: the picture must cover the whole map screen at every size
        for (name, *_), f, row in zip(VARIANTS, fits, kinds[kind]):
            assert mapfit.covers(kind, row[0], row[1], row[2], f[1], f[6]), (kind, name, row)
    for a in fits:
        for b in fits:
            if a[0] == b[0]: assert a[1:] == b[1:], (a, b)        # same art folder => same composite

    # ---- animation fix: must not touch any table range, and each target must hold 0x64
    crc, anim = read_anim_offsets()
    for off, val in anim:
        assert val == 0 and orig[off] == 0x64, hex(off)
        assert not any(s <= off < e for s, e in ranges), hex(off)
    assert len(anim) == 70

    # ---- section layout
    pe = struct.unpack_from('<I', orig, 0x3c)[0]
    nsec = struct.unpack_from('<H', orig, pe + 6)[0]
    optsz = struct.unpack_from('<H', orig, pe + 20)[0]
    sec_tab = pe + 24 + optsz
    last = sec_tab + 40 * (nsec - 1)
    last_vs, last_va, last_rsz, last_rptr = struct.unpack_from('<IIII', orig, last + 8)
    align = 0x1000
    new_rva = (last_va + max(last_vs, last_rsz) + align - 1) & ~(align - 1)
    new_raw = (last_rptr + last_rsz + align - 1) & ~(align - 1)
    assert new_raw == len(orig), (hex(new_raw), hex(len(orig)))
    base_va = IMAGE_BASE + new_rva

    def rng_src():
        return '.long %d\n' % len(ranges) + ''.join('.long 0x%x, %d\n' % (va2va(s), e - s) for s, e in ranges)

    def va2va(off):
        return off - TEXT_FILE + IMAGE_BASE + 0x1000

    def ent_src():
        out = []
        for name, w, h, data in entries:
            out.append('.long %d, %d\n.asciz "%s"\n.space %d\n.byte %s\n' % (
                w, h, name, 16 - len(name) - 1, ','.join(str(b) for b in data)))
        return ''.join(out)

    rows, labels = dialog_layout(entries)
    dlgrows, widgets, strptrs, strings = dialog_asm(rows, labels)
    assert NUM == 14
    for slot, (vva, h) in MODE_VW.items():                    # the game's own viewport of the small modes, fitted exactly like the resolutions
        assert orig[va2off(vva) - 6:va2off(vva) - 4] == b'\xc7\x05'
        vw = struct.unpack_from('<I', orig, va2off(vva))[0]
        s16, ox, oy, _, vh = mapfit.fit(vw, h)
        kinds[0].append((s16, ox, oy, vw + 1, vh))
        for kind in (1, 2, 3, 4):
            s16, ox, oy, _, vh, ok = mapfit.fit_kind(kind, vw, h)
            kinds[kind].append((s16, ox, oy, vw + 1, vh))
    nl = len(labels)
    erows_l = export_layout(nl)
    krows_l, kextra, karrows = keys_layout(nl)
    nb = max(len(rows), len(krows_l) + len(kextra))                       # records of both click tables
    for _ in range(nb - len(rows)):                          # the Display page's table is padded with unreachable records
        widgets += '.short 1, 1, 0, 0\n.long %d, %d, 0x01000000, 0, 0\n' % (H_NOP, H_NOP)
    krows, kwidgets = rows_asm(krows_l, nb, kextra)
    erows, ewidgets = rows_asm(erows_l, nb)
    kdata, kptrs, kopts = keys_data(nl, krows, karrows, lambda va, n: orig[raw_off(orig, va):raw_off(orig, va) + n])
    strptrs += kptrs
    kdata += 'erows:\n' + erows
    src = (Path(__file__).parent / 'stubs.s.tmpl').read_text()
    src = (src.replace('@@BASE@@', hex(base_va)).replace('@@NUM@@', str(NUM))
              .replace('@@ENTSZ@@', str(ENTSZ)).replace('@@FILTER@@', str(int(FILTER))).replace('@@RANGES@@', rng_src())
              .replace('@@ENTRIES@@', ent_src()).replace('@@CAP@@', str(PATH_CAP))
              .replace('@@FITTAB@@', ''.join('.long %d, %d, %d, %d, %d\n' % (tuple(rows[r]) if r < len(rows) else (0,) * 5)
                                                for rows in kinds for r in range(16)))
              .replace('@@NBTN@@', str(nb)).replace('@@NDLG@@', str(len(rows))).replace('@@NSTR@@', str(nl + 3 + len(ACTIONS) + 4))
              .replace('@@NACT@@', str(len(ACTIONS))).replace('@@NKBTN@@', str(len(krows_l))).replace('@@KFH@@', str(KFH)).replace('@@KFW@@', str(KFW)).replace('@@KFX@@', str(KFX)).replace('@@KHINTX@@', str(KHINTX)).replace('@@KHINTY@@', str(KHINTY))
              .replace('@@KSCR_N@@', str(KSCR_N)).replace('@@KTITLE@@', str(nl)).replace('@@KWIDGETS@@', kwidgets).replace('@@EWIDGETS@@', ewidgets).replace('@@EFW@@', str(EFW)).replace('@@EFH@@', str(EFH)).replace('@@EFX@@', str(EFX)).replace('@@ELABX@@', str(ELABX)).replace('@@EBARX@@', str(EBARX)).replace('@@EHINTY@@', str(EHINTY)).replace('@@ETITLE@@', str(nl + 3 + len(ACTIONS))).replace('@@KDATA@@', kdata)
              .replace('@@DLGROWS@@', dlgrows).replace('@@WIDGETS@@', widgets)
              .replace('@@STRPTRS@@', strptrs).replace('@@STRINGS@@', strings))
    # C module (PNG decode + map composite): linked behind the stubs at a fixed VA; it needs the stub data
    # addresses and the stubs need its entry points, so assemble twice (sizes do not depend on the addresses)
    cobj = clink.compile_c(CSRC, ['-DCMLOG'] if os.environ.get('ZEUS_CMLOG') else [])
    clen = len(clink.link(cobj, 0, {'fittab': 0, 'cur_r': 0}, bss_base=0)[0])
    pass1 = src.replace('@@CMOD@@', '.space %d' % clen).replace('@@C_BUILD@@', '0').replace('@@C_LOAD@@', '0').replace('@@C_IMAGE@@', '0').replace('@@C_INTRO@@', '0').replace('@@C_VBLIT@@', '0').replace('@@C_ZBUF@@', '0').replace('@@C_ZOOM@@', '0').replace('@@C_ZDIMS@@', '0').replace('@@C_ZMIN@@', '0').replace('@@C_ZSHIFT@@', '0').replace('@@C_EXPORT@@', '0')
    blob, syms = assemble(pass1)
    cbss = (base_va + len(blob) + PATH_CAP * 4 + 0xffff) & ~0xffff     # the C module's .bss: own pages after the route-point shadow buffer
    cblob, csyms = clink.link(cobj, base_va + syms['cmod'], {'fittab': base_va + syms['fittab'], 'cur_r': base_va + syms['cur_r']}, bss_base=cbss)
    assert len(cblob) == clen
    src = (src.replace('@@CMOD@@', '.byte ' + ','.join(str(b) for b in cblob))
              .replace('@@C_BUILD@@', hex(csyms['cm_build'])).replace('@@C_LOAD@@', hex(csyms['cm_load']))
              .replace('@@C_IMAGE@@', hex(csyms['cm_image'])).replace('@@C_INTRO@@', hex(csyms['cm_intro'])).replace('@@C_VBLIT@@', hex(csyms['cm_vblit']))
              .replace('@@C_ZBUF@@', hex(csyms['cm_zbuf'])).replace('@@C_ZOOM@@', hex(csyms['cm_zoom']))
              .replace('@@C_ZDIMS@@', hex(csyms['cm_zdims'])).replace('@@C_ZMIN@@', hex(csyms['cm_zmin'])).replace('@@C_ZSHIFT@@', hex(csyms['cm_zshift'])).replace('@@C_EXPORT@@', hex(csyms['cm_export'])))
    if os.environ.get('ZEUS_NOC'):       # bisect build: hooks stay, but the C module is never entered
        off = hex(base_va + syms['cm_off'])
        src = src.replace(hex(csyms['cm_build']), off).replace(hex(csyms['cm_load']), off).replace(hex(csyms['cm_image']), off).replace(hex(csyms['cm_intro']), off).replace(hex(csyms['cm_vblit']), off).replace(hex(csyms['cm_zbuf']), off).replace(hex(csyms['cm_zoom']), off).replace(hex(csyms['cm_zdims']), off).replace(hex(csyms['cm_zmin']), off).replace(hex(csyms['cm_zshift']), off).replace(hex(csyms['cm_export']), off)
    blob, syms2 = assemble(src)
    assert syms2 == syms, 'stub layout changed between passes'
    assert syms['entries'] + NUM * ENTSZ <= syms['ApplyMode'], 'table overlaps code'

    # ---- patch the exe
    out = bytearray(orig)
    assert syms['end'] == len(blob), (syms['end'], len(blob))
    vsize = cbss + 0x100000 - base_va                  # BSS tail = route-point shadow buffer, then the C module's .bss on its own pages
    raw = (len(blob) + align - 1) & ~(align - 1)
    out += blob + b'\0' * (raw - len(blob))

    # new section header (header room check)
    assert sec_tab + 40 * (nsec + 1) <= struct.unpack_from('<I', orig, pe + 24 + 60)[0]
    hdr = struct.pack('<8sIIIIIIHHI', b'.wpat', vsize, new_rva, raw, new_raw, 0, 0, 0, 0, 0xE0000060)
    out[sec_tab + 40 * nsec:sec_tab + 40 * (nsec + 1)] = hdr
    struct.pack_into('<H', out, pe + 6, nsec + 1)
    struct.pack_into('<I', out, pe + 24 + 56, new_rva + ((vsize + align - 1) & ~(align - 1)))   # SizeOfImage
    struct.pack_into('<I', out, pe + 24 + 64, 0)                                             # CheckSum
    # .text writable (table is copied over code at run time)
    text_hdr = sec_tab
    assert orig[text_hdr:text_hdr + 5] == b'.text'
    ch = struct.unpack_from('<I', out, text_hdr + 36)[0]
    struct.pack_into('<I', out, text_hdr + 36, ch | 0x80000000)

    # hooks
    tgt = {k: base_va + syms[v] for k, v in
           dict(init='StubInit', setmode='StubSetMode', revert='StubRevert', good='StubGood',
                f7='StubF7', maploader='MapLoad', sdm='StubSDM', dlgdraw='MyDisplayDraw', gettext='StubGetText',
                keydown='KeyDown', opendlg='OpenHook', imghook='ImgHook', imgpin='ImgPin', logo='LogoHook', intro='IntroHook', blit='BlitHook', gwidth='GroupWidth', zset='ZoomSetter', zdraw='ZoomDraw', zmouse='ZoomMouse', zwheel='WheelHook', zcost='ZoomCost', zwidth='ZoomWidth', charhook='CharHook', **{'kpoll%d' % i: 'KeyPoll' for i in range(len(KPOLL_SITES))},
                **WRAPS).items()}
    for name, (va, expect) in HOOKS.items():
        o = va2off(va)
        assert orig[o:o + len(expect)] == expect, name
        assert not any(s <= o < e for s, e in ranges)
        if name in ('init', 'setmode', 'revert', 'maploader'):
            patch = b'\xe8' + rel32(va, tgt[name])
        elif name == 'good':
            patch = b'\xe8' + rel32(va, tgt[name]) + b'\x90'
        elif name == 'f7':
            patch = b'\xe9' + rel32(va, tgt[name]) + b'\x90\x90'
        elif name == 'gettext':
            patch = b'\xe9' + rel32(va, tgt[name]) + b'\x90' * 5
        elif name == 'dlgdraw':
            patch = b'\xe9' + rel32(va, tgt[name])
        elif name == 'sdm':
            patch = b'\xe9' + rel32(va, tgt[name]) + b'\x90\x90\x90'
        elif name in WRAPS:
            patch = b'\xe9' + rel32(va, tgt[name]) + b'\x90' * (len(expect) - 5)
        elif name == 'keydown' or name.startswith('kpoll'):
            if name.startswith('kpoll'):
                pv = dict(KPOLL_PAIRS)[va]
                assert orig[va2off(pv):va2off(pv) + 2] == b'\x6a\x52', name
                mid = list(capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32).disasm(orig[va2off(pv) + 2:o], pv + 2))
                assert sum(i.size for i in mid) == va - pv - 2 and all(i.mnemonic in ('mov', 'xor') for i in mid), name
            patch = b'\xe8' + rel32(va, tgt[name]) + b'\x90'
        elif name == 'imghook':
            patch = b'\xe9' + rel32(va, tgt[name]) + b'\x90'
        elif name == 'intro':
            patch = b'\xe9' + rel32(va, tgt[name]) + b'\x90'
        elif name == 'blit':                                  # a call: BlitHook returns with ret 28 like the API it replaces
            patch = b'\xe8' + rel32(va, tgt[name]) + b'\x90'
        elif name == 'logo':
            patch = b'\xe9' + rel32(va, tgt[name]) + b'\x90'
        elif name == 'imgpin':
            assert va + len(expect) == PIN_END_VA and (W_VA, H_VA) == (va + 2, va + 0x1a)
            patch = b'\xe9' + rel32(va, tgt[name]) + b'\x90' * (len(expect) - 5)
        elif name == 'opendlg':
            patch = b'\xe9' + rel32(va, tgt[name])
        elif name in ('zset', 'zdraw', 'zcost', 'zwidth'):                      # calls: the stub returns to the original code or tail-jumps into the original function
            patch = b'\xe8' + rel32(va, tgt[name])
        elif name in ('zmouse', 'zwheel', 'charhook'):
            patch = b'\xe9' + rel32(va, tgt[name]) + b'\x90' * (len(expect) - 5)
        elif name == 'gwidth':
            patch = b'\xe9' + rel32(va, tgt[name]) + b'\x90' * (len(expect) - 5)
        assert len(patch) == len(expect), name
        out[o:o + len(patch)] = patch

    # Display-options click table: relocate to the new 17-record table
    tbl_va = base_va + syms['widgets']
    for va in DLG_TABLE_PUSHES:
        o = va2off(va)
        assert orig[o:o + 7] == bytes.fromhex('6a0468') + struct.pack('<I', DLG_TABLE), hex(va)
        out[o:o + 7] = b'\x6a' + bytes([nb]) + b'\x68' + struct.pack('<I', tbl_va)
    assert nb < 0x80

    # Options pull-down: each menu descriptor gets its own item table (original records + Keyboard Controls); the code that
    # rewrites the text idx words of the 7-item table writes to the copy
    new_tabs = {}
    for dva, lbl, cnt in kopts:
        d = raw_off(orig, dva)
        assert struct.unpack_from('<IIII', orig, d) [2:] == (OPT_TABLE, cnt), hex(dva)       # {x, id<<16|6, items, count}
        assert struct.unpack_from('<H', orig, d + 6)[0] == 2                               # text group 2 = Options
        new_tabs[dva] = base_va + syms[lbl]
        struct.pack_into('<II', out, d + 8, new_tabs[dva], cnt + 1)
    dfile = raw_off(orig, FILE_DESC)                          # File menu descriptor {x, id<<16|6, items, count}
    assert struct.unpack_from('<IIII', orig, dfile)[2:] == (FILE_TABLE, FILE_N) and struct.unpack_from('<H', orig, dfile + 6)[0] == 1
    new_tabs[FILE_DESC] = base_va + syms['kfile']
    struct.pack_into('<II', out, dfile + 8, new_tabs[FILE_DESC], FILE_N + 1)
    wr_patch = []
    for va in OPT_WRITERS:
        o = va2off(va)
        ins = next(capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32).disasm(orig[o:o + 15], va))
        old = [a for a in (OPT_TABLE + 0x42, OPT_TABLE + 0x52) if struct.pack('<I', a) in ins.bytes]
        assert len(old) == 1 and ins.bytes.count(struct.pack('<I', old[0])) == 1, hex(va)
        at = o + ins.bytes.index(struct.pack('<I', old[0]))
        assert not any(s <= at < e for s, e in ranges), hex(va)      # a resolution-table range would copy the old address back
        out[at:at + 4] = struct.pack('<I', new_tabs[0x5eff10] + old[0] - OPT_TABLE + 16)
        wr_patch.append((va, ins.size))

    zv_patch = []
    for va, axis in ZV_READS.items():
        o = va2off(va)
        ins = next(capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32).disasm(orig[o:o + 15], va))
        old = MOUSE_X if axis == 'x' else MOUSE_Y
        assert ins.bytes.count(struct.pack('<I', old)) == 1 and ('[0x%x]' % old) in ins.op_str, hex(va)
        assert not any(s_ <= o < e_ for s_, e_ in ranges), hex(va)
        at = o + ins.bytes.index(struct.pack('<I', old))
        out[at:at + 4] = struct.pack('<I', base_va + syms['zvx' if axis == 'x' else 'zvy'])
        zv_patch.append((va, ins.size, axis, at - o))
    # animation fix
    for off, val in anim:
        out[off] = val

    # the exe starts in 1024x768 (entry 0 = original bytes), so .text outside hooks == original
    return bytes(out), dict(orig=orig, exes=exes, ranges=ranges, entries=entries, anim=anim, rows=rows, labels=labels, widgets_src=widgets, dlg_pushes=DLG_TABLE_PUSHES,
                            syms=syms, csyms=csyms, fits=fits, new_tabs=new_tabs, wr_patch=wr_patch, kopts=kopts, kinds=kinds, nb=nb, zv_patch=zv_patch, base_va=base_va, hooks=HOOKS, tgt=tgt, NUM=NUM, ENTSZ=ENTSZ)


def verify(out, info):
    orig, exes, ranges, entries = info['orig'], info['exes'], info['ranges'], info['entries']
    # 1. table[R] applied to original .text reproduces each shipped widescreen exe, byte for byte
    for (name, _), x, (_, _, _, data) in zip(VARIANTS, exes, entries):
        t = bytearray(orig[TEXT_FILE:TEXT_END])
        k = 0
        for s, e in ranges:
            t[s - TEXT_FILE:e - TEXT_FILE] = data[k:k + e - s]; k += e - s
        assert bytes(t) == x[TEXT_FILE:TEXT_END], name
    # 2. built exe: .text equals original except hooks + animation bytes
    hook_spans = [(va2off(va), va2off(va) + len(b)) for va, b in info['hooks'].values()]
    hook_spans += [(va2off(va), va2off(va) + 7) for va in info['dlg_pushes']]
    hook_spans += [(va2off(va), va2off(va) + n) for va, n in info['wr_patch']]
    hook_spans += [(va2off(va) + d, va2off(va) + d + 4) for va, n, _, d in info['zv_patch']]
    anim = [(o, o + 1) for o, _ in info['anim']]
    skip = hook_spans + anim
    for i in range(TEXT_FILE, TEXT_END):
        if out[i] != orig[i] and not any(s <= i < e for s, e in skip):
            if i == TEXT_FILE + 0x1d7000: pass
            raise AssertionError('unexpected change at %#x' % i)
    # 3. disassemble every hook and the whole stub blob
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    for name, (va, b) in info['hooks'].items():
        o = va2off(va)
        ins = list(md.disasm(bytes(out[o:o + 7]), va))
        first = ins[0]
        print('  hook %-8s %08x  %s %s' % (name, va, first.mnemonic, first.op_str))
        assert first.mnemonic in ('call', 'jmp') and int(first.op_str, 16) == info['tgt'][name], name
    base = info['base_va']; syms = info['syms']
    blob = bytes(out[len(orig):])
    # 3b. each entry wrapper re-executes exactly the instructions its hook displaced, then continues after them
    for name, sym in WRAPS.items():
        va, expect = info['hooks'][name]
        nxt = min(v for v in syms.values() if v > syms[sym])        # next label after this stub
        ins = list(md.disasm(blob[syms[sym]:nxt], base + syms[sym]))
        while ins[-1].mnemonic == 'nop': ins.pop()          # .balign padding after the last stub
        want = list(md.disasm(expect, va))
        got = ins[-(len(want) + 1):]                       # displaced instructions + final jmp
        assert got[-1].mnemonic == 'jmp' and int(got[-1].op_str, 16) == va + len(expect), (name, got[-1])
        for g, w in zip(got, want):
            assert (g.mnemonic, g.op_str) == (w.mnemonic, w.op_str), (name, g, w)
    code_start = syms['ApplyMode']
    n = 0
    for ins in md.disasm(blob[code_start:syms['cmod']], base + code_start):
        n += 1
        assert ins.mnemonic != '(bad)'
    print('  stub code: %d instructions decoded, %d bytes; C module %d bytes' % (n, syms['cmod'] - code_start, syms['end'] - syms['cmod']))
    # 3c. Options menu: descriptors point at the new tables (original records + Keyboard Controls), nothing else in .data changed
    kd = {dva: (lbl, cnt) for dva, lbl, cnt in info['kopts']}
    for dva, (lbl, cnt) in kd.items():
        d = raw_off(orig, dva)
        assert struct.unpack_from('<II', out, d + 8) == (info['new_tabs'][dva], cnt + 1), hex(dva)
        t = syms[lbl]
        want = b''
        for r in range(cnt):
            rec = bytearray(orig[raw_off(orig, OPT_TABLE + 16 * r):raw_off(orig, OPT_TABLE + 16 * r) + 16])
            if r >= KOPT_AT:
                struct.pack_into('<H', rec, 0, struct.unpack_from('<H', rec, 0)[0] + 20)
            want += bytes(rec)
        t_new = t + 16 * KOPT_AT                                  # the new item sits at index KOPT_AT, the originals after it moved down 20 px
        got = blob[t:t + 16 * (cnt + 1)]
        assert got[:16 * KOPT_AT] == want[:16 * KOPT_AT] and got[16 * (KOPT_AT + 1):] == want[16 * KOPT_AT:], lbl
        y, idx, hnd = struct.unpack_from('<HHI', blob, t_new)
        assert (y, idx, hnd) == (20 * KOPT_AT, 100, base + syms['KeysOpen']), lbl
    t = syms['kfile']                                         # File menu table: originals, the new item at KFILE_AT, the later ones 20 px lower
    want = b''
    for r in range(FILE_N):
        rec = bytearray(orig[raw_off(orig, FILE_TABLE + 16 * r):raw_off(orig, FILE_TABLE + 16 * r) + 16])
        if r >= KFILE_AT: struct.pack_into('<H', rec, 0, struct.unpack_from('<H', rec, 0)[0] + 20)
        want += bytes(rec)
    got = blob[t:t + 16 * (FILE_N + 1)]
    assert got[:16 * KFILE_AT] == want[:16 * KFILE_AT] and got[16 * (KFILE_AT + 1):] == want[16 * KFILE_AT:]
    assert struct.unpack_from('<HHI', got, 16 * KFILE_AT) == (20 * KFILE_AT, 100, base + syms['ExpOpen'])
    assert struct.unpack_from('<II', out, raw_off(orig, FILE_DESC) + 8) == (base + t, FILE_N + 1)
    for va, n, axis, _ in info['zv_patch']:
        ins = next(md.disasm(bytes(out[va2off(va):va2off(va) + n]), va))
        assert ins.size == n and ('[0x%x]' % (base + syms['zvx' if axis == 'x' else 'zvy'])) in ins.op_str, (hex(va), ins.op_str)
    for va, n in info['wr_patch']:
        ins = next(md.disasm(bytes(out[va2off(va):va2off(va) + n]), va))
        t0 = info['new_tabs'][0x5eff10]
        assert ins.size == n and ins.mnemonic == 'mov' and ('word ptr [0x%x]' % (t0 + 0x52) in ins.op_str
                                                              or 'word ptr [0x%x]' % (t0 + 0x62) in ins.op_str), (hex(va), ins.op_str)
    n_dd = sum(1 for i in range(len(orig)) if out[i] != orig[i]) - sum(e - s for s, e in hook_spans) - len(info['anim'])
    print('  options menu: %d descriptors, %d writer sites repointed; %d other bytes changed outside .text hooks (descriptors)' % (len(kd), len(info['wr_patch']), n_dd))
    # 3d. keyboard page: both click tables have the Display page's length (the game's 3 pushes keep their record count); labels line up
    nb = info['nb'] * 28
    assert syms['dwidgets'] - syms['widgets'] == nb == syms['kwidgets'] - syms['dwidgets'] == syms['ewidgets'] - syms['kwidgets'] == syms['strptrs'] - syms['ewidgets']
    assert blob[syms['widgets']:syms['widgets'] + nb] == blob[syms['dwidgets']:syms['dwidgets'] + nb]
    # 4. PE sanity
    pe = struct.unpack_from('<I', out, 0x3c)[0]
    assert struct.unpack_from('<H', out, pe + 6)[0] == 5


if __name__ == '__main__':
    out, info = build()
    verify(out, info)
    DIST.mkdir(exist_ok=True)
    if os.environ.get('ZEUS_CMLOG'):
        (DIST.parent / 'work' / 'Zeus_cmlog.exe').write_bytes(out); print('wrote work/Zeus_cmlog.exe (C module logs to Zeus.cm.log)'); sys.exit(0)
    if os.environ.get('ZEUS_NOC'):
        (DIST.parent / 'work' / 'Zeus_noC.exe').write_bytes(out); print('wrote work/Zeus_noC.exe (C module disabled)'); sys.exit(0)
    (DIST / 'Zeus.exe').write_bytes(out)
    print('ranges: %d (%d bytes/entry), entries: %d, exe: %d bytes, crc32 %08x' % (
        len(info['ranges']), sum(e - s for s, e in info['ranges']), info['NUM'], len(out), zlib.crc32(out)))
    for n, w, h, _ in info['entries']:
        print('  %-10s %dx%d' % (n, w, h))
