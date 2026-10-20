"""Compile ZeusPatcher.exe (32-bit Windows GUI) with mingw-w64: build_patcher.py OUT.exe"""
import os, subprocess, sys, tempfile
H = os.path.dirname(os.path.abspath(__file__))
out = sys.argv[1]
os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
with tempfile.TemporaryDirectory() as t:
    subprocess.check_call(['i686-w64-mingw32-windres', '-O', 'coff', f'{H}/ZeusPatcher.rc', '-o', f'{t}/r.o'], cwd=H)
    subprocess.check_call(['i686-w64-mingw32-gcc', '-O2', '-Wall', '-Wextra', '-Werror', '-municode', '-mwindows', '-static', '-mcrtdll=msvcrt-os', '-Wl,--no-insert-timestamp',
                           '-o', out, f'{H}/ZeusPatcher.c', f'{H}/zcore.c', f'{t}/r.o', '-lcomctl32', '-lshell32', '-lole32', '-luuid'])
print('built', out, os.path.getsize(out), 'bytes')
