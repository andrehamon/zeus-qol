# Building and checking Zeus QOL

This repo holds the source of the two programs a player runs:

- **`Zeus.exe` (patched).** It is not stored here. `build/build_exe.py` generates it from the original GOG `Zeus.exe`: it adds a code section (`.wpat`) and redirects about 40 places in the original code into it.
- **`ZeusPatcher.exe`.** The installer, a small Win32 program in `build/patcher/`.

The artwork, maps, HD intro video and the binary deltas that are shipped in the release `payload` folder are not in this repo. Download the release ZIP to get them.

You can rebuild both programs and compare them with the release byte for byte. Both builds are deterministic.

## Requirements

- macOS. Everything here was built and tested on macOS; Linux with clang should work but is untested.
- Python 3 (developed with 3.12), with `pip install -r requirements.txt`
- `clang` that can target i386 (Apple clang works). It compiles the game-side code; there is no libc and no linker besides `build/clink.py`.
- `mingw-w64` with the 32-bit compiler `i686-w64-mingw32-gcc` (Homebrew: `brew install mingw-w64`). Only needed for the installer.

## Rebuild the patched game exe

The exe is built from three kinds of input that are not part of this repo. Put them under `exes/` in the repo root:

```
exes/Zeus_original_game.exe                          your GOG Zeus.exe, version 2.1.4.0
exes/Widescreen Patches/<resolution>/Zeus.exe        the per-resolution exes of the widescreen patch (see Credits in the README)
exes/Widescreen Patches/1360x768/Version 1/Zeus.exe  (1360x768 has two versions)
exes/Widescreen Patches/1360x768/Version 2/Zeus.exe
exes/zeus_poseidon_widescreen_animation_patch/patch_zeus.exe
```

The resolutions are 1280x720, 1280x800, 1280x1024, 1440x900, 1600x900, 1680x1050, 1920x1080, 1920x1200, 2048x1152, 2560x1440 and 2560x1600. Only the bytes that differ from the original in these files (the screen width and height values, and the 70 animation bytes) are taken from them; their added code is not used.

SHA-256 of the inputs that were used for the release:

| File | SHA-256 |
|---|---|
| `Zeus_original_game.exe` | `382bcd286192a06eb8fdc45f5c37390a19999f5db9c940c3ce2dfcd775e90a57` |
| `1280x720/Zeus.exe` | `96925491ba47e4d11bf1b0156acc884feab86913351267091e46c326739e0baa` |
| `1280x800/Zeus.exe` | `b6051a5e46e747ccfc655041648551a65eab2965a0f5bb2ae0712a6ebc9827d0` |
| `1280x1024/Zeus.exe` | `6ae87548748a5db76c4376bcb1724892adac0d31cca557207271d4d8fe6e901c` |
| `1360x768/Version 1/Zeus.exe` | `23a880967a27982700cc221ebcdd7fc4f50368fef7b91e7003caff28bff540a9` |
| `1360x768/Version 2/Zeus.exe` | `066657d4c4d8685303e3646b81403e3bd6ef84b6489d114b50f581fb43512076` |
| `1440x900/Zeus.exe` | `e488669658f7f745412231e4b973937f9a1c919f43ee82c6434f11b1ca2f2c8a` |
| `1600x900/Zeus.exe` | `3b629f8a51a6df8e80aa246f286f41f17231fc7de2495e10e60848f23096a2a2` |
| `1680x1050/Zeus.exe` | `776663acfb905166d46e275cc91b124c0b699d4e0822c8a633cac4c05193d77e` |
| `1920x1080/Zeus.exe` | `873501dfeb17318ea23e0c4d1a3bf80d7171cedb1578676c49f92ba31f1f444d` |
| `1920x1200/Zeus.exe` | `ac529df749caf5d69bfc28b9d5d1681ce922df0ff6b36f8a99c9f8f161676c4b` |
| `2048x1152/Zeus.exe` | `bcec2bce34082eaf4b1fbea07c8455187d6b22fbbba8143130de219ed8f7ecde` |
| `2560x1440/Zeus.exe` | `3f4aa81866105246270c362da63df8b5a18dda863bee5db6dd27c227f010cc4d` |
| `2560x1600/Zeus.exe` | `002f2f8b142f043e43de91323fa319fd7d39fb2ff86a3aefcf52dfa56a7df320` |
| `patch_zeus.exe` | `58536620bcb03a9b259d860e5db493d735b3297c53756411622bff359153b483` |

Then run, from the repo root:

```
python3 build/build_exe.py
```

This writes `dist/Zeus.exe` and checks every patched location against the original bytes. The expected result is 2,584,576 bytes with SHA-256:

```
b380509eb29ada0b6d101c01c3165fa761d0dfa5699108baeaa9a7465e2a734e
```

## Rebuild the installer

```
python3 build/patcher/build_patcher.py dist/ZeusPatcher.exe
```

With GCC 16.2.0 and mingw-w64 14.0.0 (Homebrew) the result is 416,823 bytes with SHA-256:

```
cc54f993bb195e938441fb52e2c0d862391b5578f16dec3ca0c99b9d233d01f8
```

A different compiler version gives a different but equivalent file. The installer links only against Windows system DLLs (`KERNEL32`, `USER32`, `GDI32`, `SHELL32`, `OLE32`, `COMCTL32` and `msvcrt.dll`), so it runs on any Windows without a runtime installer.

## Check what a release ZIP contains

Build `zcli` and apply the shipped exe delta to your original `Zeus.exe`. The result must be identical to the exe you built from source:

```
clang -O2 -o zcli build/patcher/zcore.c build/patcher/zcli.c
./zcli apply exes/Zeus_original_game.exe payload/deltas/Zeus.exe.zd from_delta.exe
shasum -a 256 from_delta.exe dist/Zeus.exe
```

`zcli sha <file>` prints a SHA-256. `payload/manifest.txt` lists every file the installer writes with its SHA-256. The installer refuses to continue if anything does not match.

## Reading the source

**The installer** is `build/patcher/ZeusPatcher.c` (about 440 lines) plus `zcore.c` (SHA-256 and the delta format, 86 lines). The delta format is described in `zcore.h`. What it does, in order:

1. Hashes your `Zeus.exe` and the six sprite packs. Only the original GOG 2.1.4.0 files are accepted; anything else is refused with the name of the file.
2. Builds every new file next to its target as `<name>.zpt` and checks its SHA-256. Nothing in the game is changed yet.
3. Copies every file it is about to replace, including files of yours that are already there, into `ZeusPatch_backup`.
4. Moves the new files into place (the exe last) and hashes everything again. A failure while moving the files in, or while checking them afterwards, puts the backed-up files back.
5. If the patch is already installed it offers Reinstall (restore the backup, then install again) or Remove patch (restore the backup).

It only writes inside the game folder and writes `ZeusPatcher.log` next to itself. It contains no network code. Run it without a window with `ZeusPatcher.exe /auto <game folder> check|install|restore|reinstall`; the result is in the log and the exit code.

**The game exe patch** is `build/build_exe.py` (the hook table `HOOKS`, the per-resolution tables, the section layout), `build/stubs.s.tmpl` (the hook code, i386 assembly) and `build/cmod/mapcomp.c` (the picture, map, zoom, video and city export code, freestanding C). Every hook is asserted against the original bytes when the exe is built.

Facts about the result that you can check yourself:

- The patched exe imports exactly the same DLLs and functions as the original. To check, compare `pefile.PE(path).DIRECTORY_ENTRY_IMPORT` of both files (`pip install pefile`).
- The new code calls Windows only through the original exe's own import slots. Scanning the built `.wpat` section for references into the original import table finds exactly these 16: `CreateFileA`, `ReadFile`, `WriteFile`, `CloseHandle`, `GetFileSize`, `GetFileAttributesA`, `VirtualAlloc`, `VirtualFree`, `GetEnvironmentVariableA`, `LoadLibraryA` and `GetProcAddress` (used to find the Desktop folder through `SHGetFolderPathA`), `SendMessageA`, `PostMessageA`, `GetAsyncKeyState`, `GetSystemMetrics` and the Bink `BinkCopyToBuffer`. There is no networking and no process creation.
- It reads PNG files from `Data\Images` and `Data\Maps`, the video `Binks\Zeus_Intro_HD.bik`, and its two settings files `Zeus.res` (resolution) and `Zeus.keys` (key bindings) next to `Zeus.exe`. Export City Image writes a PNG to your Desktop.

## Tests

```
python3 build/test_mapcomp.py
```

Builds the C module for the host and checks the PNG decoder, the scaler, the map composite, the full-screen picture scaling, the video scaler, the zoom code and the city export against exact reference models. Two tests that need the author's original artwork are skipped when it is not present.
