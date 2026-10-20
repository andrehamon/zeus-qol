"""Tiny assembler wrapper: clang (i386, AT&T) -> raw .text bytes + label offsets."""
import os, struct, subprocess, tempfile

def assemble(src):
    with tempfile.TemporaryDirectory() as d:
        s, o = os.path.join(d, 'a.s'), os.path.join(d, 'a.o')
        open(s, 'w').write(src)
        r = subprocess.run(['clang', '-target', 'i386-pc-linux-gnu', '-c', s, '-o', o],
                           capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(r.stderr)
        b = open(o, 'rb').read()
    shoff = struct.unpack_from('<I', b, 0x20)[0]
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', b, 0x2e)
    secs = [struct.unpack_from('<IIIIIIIIII', b, shoff + i * shentsize) for i in range(shnum)]
    stroff = secs[shstrndx][4]
    name = lambda n: b[stroff + n:b.index(b'\0', stroff + n)].decode()
    text = None; symtab = strtab = None
    for i, (n, typ, flags, addr, off, size, link, info, align, ent) in enumerate(secs):
        nm = name(n)
        if nm == '.text': text = b[off:off + size]; text_idx = i
        elif nm == '.symtab': symtab = (off, size, link)
        elif nm.startswith('.rel') and size:
            raise SystemExit('unresolved relocations in ' + nm)
    off, size, link = symtab
    stroff2 = secs[link][4]
    syms = {}
    for k in range(size // 16):
        st_name, st_value, st_size, st_info, st_other, st_shndx = struct.unpack_from('<IIIBBH', b, off + k * 16)
        if st_shndx == text_idx and st_name:
            syms[b[stroff2 + st_name:b.index(b'\0', stroff2 + st_name)].decode()] = st_value
    return text, syms
