"""Tiny C -> raw-code linker for the .wpat patch section (no lld / objcopy on this Mac).

clang compiles one freestanding C file to an i386 ELF object; this places .text, .rodata*, .data*, .bss at
`base` and applies R_386_32 / PC32 / PLT32.  Undefined symbols must be in `externs` (name -> absolute VA),
anything else fails the build (e.g. __udivdi3, memcpy: the C source must not need a runtime library)."""
import os, struct, subprocess, tempfile

CFLAGS = ['-target', 'i386-pc-linux-gnu', '-O2', '-ffreestanding', '-fno-builtin', '-fno-pic', '-fno-pie',
          '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-fno-unwind-tables', '-mno-sse', '-mno-mmx',
          '-fno-jump-tables', '-Wall', '-Werror', '-c']


def compile_c(path, extra=()):
    with tempfile.TemporaryDirectory() as d:
        o = os.path.join(d, 'a.o')
        r = subprocess.run(['clang', *CFLAGS, *extra, str(path), '-o', o], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(r.stderr)
        return open(o, 'rb').read()


def link(obj, base, externs=None, bss_base=None, bss_cap=0x100000):
    """-> (bytes, {global symbol: va}).  Layout: .text, .rodata*, .data*, .bss (zero-filled in the blob)."""
    externs = externs or {}
    shoff = struct.unpack_from('<I', obj, 0x20)[0]
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', obj, 0x2e)
    secs = [struct.unpack_from('<IIIIIIIIII', obj, shoff + i * shentsize) for i in range(shnum)]   # name,type,flags,addr,off,size,link,info,align,entsize
    cstr = lambda off: obj[off:obj.index(b'\0', off)].decode()
    names = [cstr(secs[shstrndx][4] + s[0]) for s in secs]
    # allocated sections in a fixed order
    def rank(i):
        n = names[i]
        return 0 if n.startswith('.text') else 1 if n.startswith('.rodata') else 2 if n.startswith('.data') else 3
    alloc = sorted((i for i, s in enumerate(secs) if s[2] & 2 and s[5]), key=lambda i: (rank(i), i))
    place, blob, bplace, bsz = {}, bytearray(), {}, 0
    if bss_base is not None:
        for i in alloc:
            if rank(i) == 2: raise SystemExit('clink: initialised writable data (%s) would sit in the code pages: make it const or zero it' % names[i])
    for i in alloc:
        al = max(secs[i][8], 1)
        if bss_base is not None and secs[i][1] == 8:        # .bss goes to its own pages (a write to a page that holds code is very slow under Rosetta)
            bsz += -bsz % al; bplace[i] = bss_base + bsz; bsz += secs[i][5]
            assert bsz <= bss_cap, 'clink: .bss does not fit'
            continue
        blob += b'\0' * (-len(blob) % al)
        place[i] = len(blob)
        blob += obj[secs[i][4]:secs[i][4] + secs[i][5]] if secs[i][1] != 8 else b'\0' * secs[i][5]   # 8 = NOBITS
    # symbols
    st = next(i for i, s in enumerate(secs) if s[1] == 2)
    stroff = secs[secs[st][6]][4]
    syms = []
    for k in range(secs[st][5] // 16):
        nm, val, size, info, other, shndx = struct.unpack_from('<IIIBBH', obj, secs[st][4] + k * 16)
        syms.append((cstr(stroff + nm) if nm else '', val, info, shndx))

    def sym_va(k):
        if k == 0:                                   # calls to absolute addresses: PC32 against the null symbol (S = 0)
            return 0
        name, val, info, shndx = syms[k]
        if shndx == 0:
            if name not in externs:
                raise SystemExit('clink: undefined symbol %r (the C code needs a runtime helper?)' % name)
            return externs[name]
        if shndx >= 0xff00:
            raise SystemExit('clink: unsupported symbol %r' % name)
        if shndx in bplace:
            return bplace[shndx] + val
        if shndx not in place:
            raise SystemExit('clink: symbol %r in non-allocated section' % name)
        return base + place[shndx] + val
    for i, s in enumerate(secs):
        if s[1] == 9 and s[7] in place:               # SHT_REL for an allocated section
            for r in range(s[5] // 8):
                off, info = struct.unpack_from('<II', obj, s[4] + r * 8)
                typ, k = info & 0xff, info >> 8
                p = place[s[7]] + off
                add = struct.unpack_from('<i', blob, p)[0]
                S = sym_va(k)
                if typ == 1:   v = S + add                       # R_386_32
                elif typ in (2, 4): v = S + add - (base + p)     # R_386_PC32 / PLT32
                else: raise SystemExit('clink: relocation type %d' % typ)
                struct.pack_into('<I', blob, p, v & 0xffffffff)
    out = {n: (bplace[sh] if sh in bplace else base + place[sh]) + v for n, v, info, sh in syms if n and (sh in place or sh in bplace) and info >> 4 == 1}   # STB_GLOBAL
    return bytes(blob), out
