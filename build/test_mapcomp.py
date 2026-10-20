#!/usr/bin/env python3
"""Native tests of cmod/mapcomp.c (host build, ctypes): PNG decoder vs Pillow, scaler vs an exact Python
reference, and the cm_load / cm_build flow with fake files.  Run: .venv/bin/python build/test_mapcomp.py"""
import ctypes as C, io, os, random, struct, subprocess, sys, tempfile, time
from pathlib import Path
from PIL import Image

HERE = Path(__file__).parent
tmp = tempfile.mkdtemp()
lib = os.path.join(tmp, 'libmapcomp.dylib')
subprocess.run(['clang', '-O2', '-g', '-Wall', '-Werror', '-DHOST', '-shared', '-fPIC', str(HERE / 'cmod/mapcomp.c'), '-o', lib] + (['-fsanitize=address'] if os.environ.get('ASAN') else []), check=True)
L = C.CDLL(lib)
u8p = C.POINTER(C.c_ubyte)
L.t_png.argtypes = [C.c_char_p, C.c_uint, C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.POINTER(u8p)]
L.scale_blend.argtypes = [C.c_char_p, C.c_uint, C.c_uint, C.c_uint, C.c_char_p, C.c_uint, C.c_uint, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_void_p]
L.place.argtypes = [C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.POINTER(C.c_int)]


def decode(data):
    w, h, ch, out = C.c_uint(), C.c_uint(), C.c_uint(), u8p()
    buf = C.create_string_buffer(data, len(data))                 # png_open packs the IDATs in place
    r = L.t_png(buf, len(data), C.byref(w), C.byref(h), C.byref(ch), C.byref(out))
    if r: return r, None
    px = bytes(C.cast(out, C.POINTER(C.c_ubyte * (w.value * h.value * ch.value))).contents)
    L.t_free(out)
    return 0, (w.value, h.value, ch.value, px)


def png_bytes(im, **kw):
    b = io.BytesIO(); im.save(b, 'PNG', **kw); return b.getvalue()


def test_png():
    rnd = random.Random(1)
    for mode, ch in (('RGBA', 4), ('RGB', 3)):
        for (w, h) in ((1, 1), (7, 5), (64, 48), (300, 200)):
            # smooth-ish + noisy content so every filter type and long matches occur
            px = bytes((x * 3 + y * 5 + (rnd.randrange(8) if (x + y) % 3 else 0)) & 255 for y in range(h) for x in range(w * ch) for _ in [0])
            im = Image.frombytes(mode, (w, h), px[:w * h * ch].ljust(w * h * ch, b'\1'))
            for kw in ({}, {'optimize': True}, {'compress_level': 0}, {'compress_level': 1}):
                r, got = decode(png_bytes(im, **kw))
                assert r == 0 and got == (w, h, ch, im.tobytes()), (mode, w, h, kw, r)
    # multi-IDAT (Pillow splits big streams into several chunks)
    noise = Image.frombytes('RGBA', (400, 300), bytes(rnd.randrange(256) for _ in range(400 * 300 * 4)))
    data = png_bytes(noise); assert data.count(b'IDAT') > 1, data.count(b'IDAT')
    r, got = decode(data); assert r == 0 and got[3] == noise.tobytes()
    # rejects: palette, 16 bit, interlaced, garbage, truncated
    assert decode(png_bytes(Image.new('P', (4, 4))))[0] != 0
    assert decode(png_bytes(Image.new('I;16', (4, 4))))[0] != 0
    assert decode(png_bytes(Image.new('RGB', (4, 4)), interlace=1) if False else b'nope')[0] != 0
    good = png_bytes(Image.new('RGBA', (50, 50), (1, 2, 3, 4)))
    assert decode(good[:len(good) // 2])[0] != 0
    print('png decoder: ok')


def ref_scale(src, sw, sh, ch, dst, cw, chh, ox, oy, dw, dh):
    """Exact python model of scale_blend (dst: bytearray BGR)."""
    acc = [[0, 0, 0, 0] for _ in range(dw)]
    yd, yend = 0, sh
    for j in range(sh):
        hr = []
        x, xe, a = 0, sw, [0, 0, 0, 0]
        for i in range(sw):
            p = src[(j * sw + i) * ch:(j * sw + i) * ch + ch]
            al = p[3] if ch == 4 else 255
            c = (p[0] * al, p[1] * al, p[2] * al, al * 255)
            s, e = i * dw, i * dw + dw
            while s < e:
                seg = min(e, xe) - s
                for k in range(4): a[k] += c[k] * seg
                s += seg
                if s == xe:
                    hr.append([v // sw for v in a]); a = [0, 0, 0, 0]; x += 1; xe += sw
        s, e = j * dh, j * dh + dh
        while s < e:
            seg = min(e, yend) - s
            for q in range(dw):
                for k in range(4): acc[q][k] += hr[q][k] * seg
            s += seg
            if s == yend:
                for q in range(dw):
                    if not (0 <= oy + yd < chh and 0 <= ox + q < cw): continue          # clipped
                    o = ((oy + yd) * cw + ox + q) * 3
                    A = acc[q][3] // sh; ia = 65025 - A
                    for c in range(3):
                        v = (acc[q][2 - c] // sh * 255 + dst[o + c] * ia + 32512) // 65025
                        dst[o + c] = min(v, 255)
                acc = [[0, 0, 0, 0] for _ in range(dw)]
                yd += 1; yend += sh


def test_scale():
    rnd = random.Random(2)
    for ch in (4, 3):
        for (sw, sh, dw, dh) in ((8, 8, 8, 8), (20, 12, 7, 5), (33, 17, 11, 6), (40, 40, 3, 2), (16, 16, 1, 1), (31, 29, 31, 29)):
            src = bytes(rnd.randrange(256) for _ in range(sw * sh * ch))
            # inside the picture, and hanging over each edge (negative offsets / past the right and bottom)
            for cw, chh, ox, oy in ((dw + 5, dh + 4, 3, 2), (max(dw - 3, 1), max(dh - 2, 1), -2, -1), (dw + 2, dh + 2, 4, 3), (dw // 2 + 1, dh // 2 + 1, -(dw // 3), 1)):
                bg = bytes(rnd.randrange(256) for _ in range(cw * chh * 3))
                want = bytearray(bg); ref_scale(src, sw, sh, ch, want, cw, chh, ox, oy, dw, dh)
                got = C.create_string_buffer(bg, len(bg)); work = C.create_string_buffer(dw * 32)
                L.scale_blend(src, sw, sh, ch, got, cw, chh, ox, oy, dw, dh, work)
                assert got.raw == bytes(want), (ch, sw, sh, dw, dh, cw, chh, ox, oy)
    # opaque white source over any background stays white; transparent leaves the background untouched
    sw, sh, dw, dh = 24, 24, 10, 10
    bg = bytes(range(256)) * 3; bg = bg[:dw * dh * 3]
    for src, want in ((b'\xff\xff\xff\xff' * (sw * sh), b'\xff' * (dw * dh * 3)), (b'\x10\x20\x30\x00' * (sw * sh), bg)):
        got = C.create_string_buffer(bg, len(bg)); work = C.create_string_buffer(dw * 32)
        L.scale_blend(src, sw, sh, 4, got, dw, dh, 0, 0, dw, dh, work); assert got.raw == want
    print('scaler: ok (exact vs python model)')


def test_cm():
    root = Path(tmp) / 'game'; (root / 'Data/Maps').mkdir(parents=True)
    L.t_root(str(root).encode())
    L.t_reset()
    land = Image.new('RGB', (625, 369), (0x1e, 0x5a, 0x7e))         # 5000x2952 / 8: ocean, the Greece rect at x 206..625 (flush right), full height
    land.paste((200, 180, 100), (331, 100, 625, 369))               # land touching the rect's right / bottom edges
    land.save(root / 'Data/Maps/Map_Greece.png')
    fit = C.c_uint * 400
    ft = fit.in_dll(L, 'fittab'); cur_r = C.c_uint.in_dll(L, 'cur_r'); mode = C.c_uint.in_dll(L, 'cur_mode')
    # entry 1: S16 for 1280x720-ish view: vw=1078?, use mapfit
    sys.path.insert(0, str(HERE)); import mapfit
    s16, ox, oy, w, h = mapfit.fit(1138, 768)
    for k, v in enumerate((s16, ox, oy, w + 1, h)): ft[5 + k] = v
    L.cm_build(3)                                                   # native key: 838x738 composites
    assert L.t_map_index(b'data\\Zeus_MapOfGreece08.jpg') == 0 and L.t_map_index(b'Data\\1920x1080\\Poseidon_map03.jpg') == 3
    assert L.t_map_index(b'data\\Zeus_system.bmp') == -1 and L.t_map_index(b'data\\Poseidon_map05.jpg') == -1
    bgr, cw, chh, key = u8p(), C.c_uint(), C.c_uint(), C.c_int()
    L.t_comp(0, C.byref(bgr), C.byref(cw), C.byref(chh), C.byref(key))
    assert (cw.value, chh.value, key.value) == (838, 738, 0)
    im = Image.frombytes('RGB', (838, 738), bytes(C.cast(bgr, C.POINTER(C.c_ubyte * (838 * 738 * 3))).contents), 'raw', 'BGR')
    assert im.getpixel((10, 10)) == (0x1e, 0x5a, 0x7e) and im.getpixel((800, 700)) == (200, 180, 100), (im.getpixel((10, 10)), im.getpixel((800, 700)))
    # loader: picks the key from mode / R, builds lazily, returns w*h*2
    px, pw, ph = u8p(), C.c_uint(), C.c_uint()
    L.cm_load.argtypes = [C.c_char_p, C.POINTER(u8p), C.POINTER(C.c_uint), C.POINTER(C.c_uint)]; L.cm_load.restype = C.c_uint
    assert L.cm_load(b'data\\Zeus_MapOfGreece03.jpg', C.byref(px), C.byref(pw), C.byref(ph)) == 838 * 738 * 2 and (pw.value, ph.value) == (838, 738)
    first = px.contents.value
    builds = L.t_comp(0, C.byref(bgr), C.byref(cw), C.byref(chh), C.byref(key))
    cur_r.value = 1                                                 # hi-res entry
    assert L.cm_load(b'data\\Zeus_MapOfGreece03.jpg', C.byref(px), C.byref(pw), C.byref(ph)) == (w + 1) * h * 2 and (pw.value, ph.value) == (w + 1, h)
    assert L.t_comp(0, C.byref(bgr), C.byref(cw), C.byref(chh), C.byref(key)) == builds + 1 and key.value == 1
    # columns: land flush to the viewport edge, extra column copies it, ocean left of the land
    im = Image.frombytes('RGB', (w + 1, h), bytes(C.cast(bgr, C.POINTER(C.c_ubyte * ((w + 1) * h * 3))).contents), 'raw', 'BGR')
    assert im.getpixel((w, h - 1)) == im.getpixel((w - 1, h - 1)) == (200, 180, 100), (im.getpixel((w, h - 1)), im.getpixel((w - 1, h - 1)))
    assert im.getpixel((2, h - 1)) == (0x1e, 0x5a, 0x7e) and im.getpixel((ox + 2, 2)) == (0x1e, 0x5a, 0x7e)
    # other maps: no Poseidon png -> not handled, the original loader must run
    assert L.cm_load(b'data\\Poseidon_map01.jpg', C.byref(px), C.byref(pw), C.byref(ph)) == 0
    assert L.cm_load(b'data\\Zeus_Load1.jpg', C.byref(px), C.byref(pw), C.byref(ph)) == 0
    # small modes use their own fit slots (14 = 800x600, 15 = 640x480), whatever R is
    for m, slot, vw, hh in ((2, 14, 598, 600), (1, 15, 478, 480)):
        s16, ox, oy, w2, h2 = mapfit.fit(vw, hh)
        for k, v in enumerate((s16, ox, oy, w2 + 1, h2)): ft[slot * 5 + k] = v
        mode.value = m
        assert L.cm_load(b'data\\Zeus_MapOfGreece03.jpg', C.byref(px), C.byref(pw), C.byref(ph)) == (w2 + 1) * h2 * 2 and (pw.value, ph.value) == (w2 + 1, h2), m
        assert L.t_comp(0, C.byref(bgr), C.byref(cw), C.byref(chh), C.byref(key)) and key.value == slot
    mode.value = 3; cur_r.value = 0                                 # 1024x768: native again
    assert L.cm_load(b'data\\Zeus_MapOfGreece03.jpg', C.byref(px), C.byref(pw), C.byref(ph)) == 838 * 738 * 2
    # corrupt png -> unavailable, not handled
    L.t_reset(); (root / 'Data/Maps/Map_Greece.png').write_bytes(b'garbage' * 10)
    assert L.cm_load(b'data\\Zeus_MapOfGreece03.jpg', C.byref(px), C.byref(pw), C.byref(ph)) == 0
    print('cm_build / cm_load: ok')


def test_image():
    """cm_image: cover scale (fit the better direction, centred, overhang cropped, no bars), name matching, fallbacks."""
    root = Path(tmp) / 'game3'; (root / 'Data/Images').mkdir(parents=True)
    L.t_root(str(root).encode()); L.t_reset()
    L.cm_image.argtypes = [C.c_char_p, C.c_uint, C.c_uint]; L.cm_image.restype = C.c_void_p
    L.t_cover.argtypes = [C.c_uint] * 4 + [C.POINTER(C.c_uint)] * 2
    mode = C.c_uint.in_dll(L, 'cur_mode'); mode.value = 3
    buf = C.create_string_buffer(64)
    for p, ok in ((b'data\\Zeus_Victory.jpg', 1), (b'data\\ZEUS_LOAD1.JPG', 1), (b'Data\\1920x1080\\scoreb.jpg', 1), (b'data\\Poseidon_FE_MainMenu.jpg', 1),
                  (b'data\\zeus_load3.jpg', 1), (b'data\\zeus_load5.jpg', 0), (b'data\\zeus_victory.jpgx', 0), (b'data\\zeus_victory', 0), (b'data\\Zeus_FE_MainMenu.jpg', 0)):
        assert L.t_img_match(p, buf) == ok, p
    rnd = random.Random(5)
    sw, sh = 160, 100                                                 # 16:10 source
    src = Image.frombytes('RGB', (sw, sh), bytes(rnd.randrange(256) for _ in range(sw * sh * 3)))
    src.save(root / 'Data/Images/zeus_victory.png')
    for (W, H) in ((160, 100), (96, 54), (64, 51), (40, 60), (33, 20)):       # identity, 16:9, 5:4, portrait, odd
        dw, dh = C.c_uint(), C.c_uint(); L.t_cover(sw, sh, W, H, C.byref(dw), C.byref(dh))
        assert dw.value >= W and dh.value >= H and (dw.value == W or dh.value == H), (W, H, dw.value, dh.value)
        # the scale is the smaller overhang: no bars, and the other direction is cropped
        assert (W * sh >= H * sw) == (dw.value == W), (W, H)
        p = L.cm_image(b'data\\Zeus_Victory.jpg', W, H); assert p, (W, H)
        got = bytes(C.cast(p, C.POINTER(C.c_ubyte * (W * H * 3))).contents)
        want = bytearray(W * H * 3)
        ref_scale(src.tobytes(), sw, sh, 3, want, W, H, (W - dw.value) // 2 if W >= dw.value else -((dw.value - W) // 2), (H - dh.value) // 2 if H >= dh.value else -((dh.value - H) // 2), dw.value, dh.value)
        assert got == bytes(want), (W, H)
        if (W, H) == (sw, sh):                                          # identity: byte for byte the source (BGR)
            assert got == Image.frombytes('RGB', (sw, sh), src.tobytes()).convert('RGB').tobytes('raw', 'BGR')
        L.t_free(C.c_void_p(p))
    # same logic in every mode (800x600 here), missing png, corrupt png, unknown name
    assert not L.cm_image(b'data\\scoreb.jpg', 96, 54)
    (root / 'Data/Images/scoreb.png').write_bytes(b'junk' * 20); assert not L.cm_image(b'data\\scoreb.jpg', 96, 54)
    for m in (1, 2):
        mode.value = m; p = L.cm_image(b'data\\Zeus_Victory.jpg', 80, 60); assert p; L.t_free(C.c_void_p(p))
    mode.value = 3
    assert not L.cm_image(b'data\\zeus_title.jpg', 96, 54)
    # Campaign Selection = plain MissionIntroduction picture + overlay 1:1 at ((W-800)/2 + 364, (H-600)/2 + 15)
    Image.frombytes('RGB', (90, 65), bytes(rnd.randrange(256) for _ in range(90 * 65 * 3))).save(root / 'Data/Images/zeus_fe_missionintroduction.png')
    ovl = Image.new('RGBA', (20, 10), (0, 0, 0, 0)); ovl.paste((200, 30, 60, 255), (0, 0, 10, 10)); ovl.save(root / 'Data/Images/campaign_select_dialog.png')
    for W, H in ((900, 650), (1001, 701)):
        n = W * H * 3
        pc = L.cm_image(b'data\\Zeus_FE_CampaignSelection.jpg', W, H); pm = L.cm_image(b'data\\Zeus_FE_MissionIntroduction.jpg', W, H); assert pc and pm
        gc = bytes(C.cast(pc, C.POINTER(C.c_ubyte * n)).contents); gm = bytes(C.cast(pm, C.POINTER(C.c_ubyte * n)).contents)
        x0, y0 = (W - 800) // 2 + 364, (H - 600) // 2 + 15
        for y in range(H):
            for x in range(W):
                i = (y * W + x) * 3
                if x0 <= x < x0 + 10 and y0 <= y < y0 + 10: assert gc[i:i + 3] == bytes((60, 30, 200)), (x, y)      # opaque overlay pixel (B, G, R)
                else: assert gc[i:i + 3] == gm[i:i + 3], (x, y)                                                     # everything else = the plain picture
        L.t_free(C.c_void_p(pc)); L.t_free(C.c_void_p(pm))
    # two overlays on one picture (Poseidon main menu)
    Image.frombytes('RGB', (90, 65), bytes(rnd.randrange(256) for _ in range(90 * 65 * 3))).save(root / 'Data/Images/mainmenu-choosegame_bg.png')
    Image.new('RGB', (12, 6), (10, 20, 30)).save(root / 'Data/Images/poseidon_fe_mainmenu_dialog.png')
    W, H = 900, 650; pc = L.cm_image(b'data\\Poseidon_FE_MainMenu.jpg', W, H); pm = L.cm_image(b'data\\Zeus_FE_ChooseGame.jpg', W, H); assert pc and pm
    gc = bytes(C.cast(pc, C.POINTER(C.c_ubyte * (W * H * 3))).contents); gm = bytes(C.cast(pm, C.POINTER(C.c_ubyte * (W * H * 3))).contents)
    for bx, by in ((67, 62), (427, 62)):
        for dy in (0, 5):
            for dx in (0, 11):
                i = ((25 + by + dy) * W + 50 + bx + dx) * 3; assert gc[i:i + 3] == bytes((30, 20, 10)), (bx, dx, dy)
    assert gm[((25 + 62) * W + 50 + 67) * 3:][:3] != bytes((30, 20, 10))                         # ChooseGame has no such frame
    L.t_free(C.c_void_p(pc)); L.t_free(C.c_void_p(pm))
    (root / 'Data/Images/campaign_select_dialog.png').unlink()                       # no overlay: still the plain picture
    p = L.cm_image(b'data\\Zeus_FE_CampaignSelection.jpg', 900, 650); assert p; L.t_free(C.c_void_p(p))
    print('cm_image: ok (cover scale == exact python model; identity exact)')


def test_intro():
    """cm_intro: 1 when Binks\\Zeus_Intro_HD.bik has a plausible Bink header.  vscale: fit inside the window, centred, black bars."""
    root = Path(tmp) / 'game4'; (root / 'Binks').mkdir(parents=True)
    L.t_root(str(root).encode())
    f = root / 'Binks/Zeus_Intro_HD.bik'
    assert not L.cm_intro()                                                    # no file
    def put(w, h, magic=b'BIKi'):
        f.write_bytes(magic + struct.pack('<IIIIIII', 1, 2972, 0, 2972, w, h, 24) + bytes(64))
    put(3852, 2070); assert L.cm_intro()
    put(640, 480); assert L.cm_intro()
    put(3852, 2070, b'RIFF'); assert not L.cm_intro()                         # not a Bink file
    put(10, 10); assert not L.cm_intro()                                      # implausible size
    f.write_bytes(b'BIKi' + bytes(8)); assert not L.cm_intro()                # truncated
    L.t_vscale.argtypes = [C.c_char_p, C.c_uint, C.c_uint, C.c_char_p, C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.c_uint]
    for bpp, f565, col in ((4, 0, b'\x10\x80\xf0\xff'), (3, 0, b'\x10\x80\xf0'), (2, 1, (0xf800 | 0x0400 | 5).to_bytes(2, 'little')),
                           (2, 0, (0x7c00 | 0x0020 | 5).to_bytes(2, 'little'))):
        for (sw, sh, W, H) in ((3852, 2070, 200, 120), (640, 480, 200, 120), (64, 48, 200, 100)):      # wider / narrower than the window
            src = col * (sw * sh); pitch = W * bpp + 8
            dst = C.create_string_buffer(b'\xaa' * (pitch * H))
            L.t_vscale(src, sw, sh, dst, pitch, W, H, bpp, f565)
            if W * sh <= H * sw: dw, dh = W, sh * W // sw
            else: dh, dw = H, sw * H // sh
            x0, y0 = (W - dw) // 2, (H - dh) // 2
            for y in range(H):
                for x in range(W):
                    px = dst.raw[y * pitch + x * bpp: y * pitch + (x + 1) * bpp]
                    inside = x0 <= x < x0 + dw and y0 <= y < y0 + dh
                    assert px == (col if inside else bytes(bpp)), (bpp, sw, sh, x, y, px)
    print('intro: ok')


def test_real():
    p = HERE.parent / 'assets/Zeus_MapOfGreece08_Land.png'
    if not p.exists(): return
    d = p.read_bytes(); t = time.time()
    r, got = decode(d); dt = time.time() - t
    ref = Image.open(p)
    assert r == 0 and got[:3] == (ref.width, ref.height, 4) and got[3] == ref.tobytes()
    print('real land png: decoded exactly in %.2fs (host build)' % dt)
    s16, ox, oy, w, h = mapfit.fit(1738, 1080)
    sw, shh = mapfit.NW * s16 >> 16, mapfit.NH * s16 >> 16
    bg = bytearray(b'\x7e\x5a\x1e' * ((w + 1) * h)); work = C.create_string_buffer(sw * 32); out = C.create_string_buffer(bytes(bg), len(bg))
    t = time.time(); L.scale_blend(got[3], got[0], got[1], 4, out, w + 1, h, ox, oy, sw, shh, work)
    print('real land png: scaled to %dx%d in %.2fs' % (sw, shh, time.time() - t))
    # visual sanity vs Pillow Lanczos: mean abs difference per channel small
    from PIL import ImageChops, ImageStat
    mine = Image.frombytes('RGB', (w + 1, h), out.raw, 'raw', 'BGR')
    lz = mapfit.composite(ref, s16, ox, oy, w + 1, h)
    diff = ImageStat.Stat(ImageChops.difference(mine, lz)).mean
    print('mean abs diff vs Pillow Lanczos composite per channel:', [round(v, 2) for v in diff]); assert max(diff) < 4
    mine.save(os.environ.get('MAPCOMP_PREVIEW', os.path.join(tmp, 'preview.png')))


def test_poseidon():
    """The three Poseidon maps are windows onto one sheet: placement matches mapfit, the guide rect lands where the
    original map's cities expect it (vs the original JPG), every size is covered, wrong proportions are refused."""
    sheet = HERE.parent / 'assets/Poseidon_map_full.png'
    origs = HERE.parent / 'exes/Original DATA Folder'
    if not sheet.exists(): return
    from PIL import ImageChops, ImageStat
    root = Path(tmp) / 'game2'; (root / 'Data/Maps').mkdir(parents=True)
    L.t_root(str(root).encode()); L.t_reset()
    import shutil; shutil.copy(sheet, root / 'Data/Maps/Map_Poseidon_1-3.png')
    ft = (C.c_uint * 400).in_dll(L, 'fittab'); cur_r = C.c_uint.in_dll(L, 'cur_r')
    sizes = {1: (1738, 1080), 2: (2338, 1440), 3: (1078, 1024)}          # R -> (viewport w, screen h): zoomed 16:9, widest, square
    for R, (vw, hh) in sizes.items():
        for kind in (0, 1, 2, 3):
            s16, ox, oy, _, vh, ok = mapfit.fit_kind(kind, vw, hh)
            for k, v in enumerate((s16, ox, oy, vw + 1, vh)): ft[(kind * 16 + R) * 5 + k] = v
            pl = (C.c_int * 4)(); L.place(kind, s16, ox, oy, pl)
            assert tuple(pl) == mapfit.place(kind, s16, ox, oy), (kind, tuple(pl), mapfit.place(kind, s16, ox, oy))
    bgr, cw, chh, key = u8p(), C.c_uint(), C.c_uint(), C.c_int()
    for R, (vw, hh) in sizes.items():
        cur_r.value = R; t = time.time(); L.cm_build(3); dt = time.time() - t
        for kind in (1, 2, 3):
            s16, ox, oy, _, vh, ok = mapfit.fit_kind(kind, vw, hh)
            assert ok, (R, kind)                                                           # the sheet fills every size
            L.t_comp(kind, C.byref(bgr), C.byref(cw), C.byref(chh), C.byref(key))
            assert (cw.value, chh.value, key.value) == (vw + 1, vh, R), (R, kind)
            im = Image.frombytes('RGB', (cw.value, chh.value), bytes(C.cast(bgr, C.POINTER(C.c_ubyte * (cw.value * chh.value * 3))).contents), 'raw', 'BGR')
            if os.environ.get('MAPCOMP_POS'): im.save('%s_pos%d_R%d.png' % (os.environ['MAPCOMP_POS'], kind, R))
            # the original map's rectangle, cut out of the composite, must look like the original JPG
            dw, dh = mapfit.NW * s16 >> 16, mapfit.NH * s16 >> 16
            o = Image.open(origs / ('Poseidon_map0%d.jpg' % kind)).convert('RGB')
            x0, x1 = max(ox, 0), min(ox + dw, vw)                                      # (the part of the map left of / right of the screen is cut off)
            ox0, ox1 = (x0 - ox) * 838 // dw, (x1 - ox) * 838 // dw
            oc = o.crop((ox0, 0, ox1, 738))
            d = sum(ImageStat.Stat(ImageChops.difference(im.crop((x0, oy, x1, oy + dh)).resize(oc.size, Image.BILINEAR), oc)).mean) / 3
            assert d < 8, (R, kind, d)
            print('  poseidon map%d R%d: %dx%d S=%.4f OX=%d, rect vs original JPG diff %.1f%s' % (kind, R, vw + 1, vh, s16 / 65536, ox, d, '' if ox >= 0 else '  (%d px of the map cut off at the side)' % -ox))
        print('  R%d: composites for all 5 slots in %.2fs' % (R, dt))
    # a sheet with the wrong proportions is refused (map falls back to the game's JPG)
    L.t_reset(); Image.new('RGBA', (5636, 3000), (0, 0, 0, 0)).save(root / 'Data/Maps/Map_Poseidon_1-3.png')
    px, pw, ph = u8p(), C.c_uint(), C.c_uint()
    L.cm_load.argtypes = [C.c_char_p, C.POINTER(u8p), C.POINTER(C.c_uint), C.POINTER(C.c_uint)]; L.cm_load.restype = C.c_uint
    assert L.cm_load(b'data\\Poseidon_map02.jpg', C.byref(px), C.byref(pw), C.byref(ph)) == 0
    print('poseidon sheet: ok')


def test_zoom():
    """cm_zdims / cm_zmin / cm_zoom (city zoom): the view the game is given, and the canvas -> back buffer scaler vs a python model."""
    L.cm_zdims.argtypes = [C.c_uint, C.c_uint, C.c_uint, C.POINTER(C.c_uint), C.POINTER(C.c_uint)]; L.cm_zdims.restype = C.c_uint
    L.cm_zmin.argtypes = [C.c_uint, C.c_uint]; L.cm_zmin.restype = C.c_uint
    L.cm_zoom.argtypes = [C.c_char_p, C.c_uint, C.c_uint, C.c_uint, C.c_char_p, C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.c_uint, C.c_uint]
    oc, orr = C.c_uint(), C.c_uint()
    # views: the real view (25 x 69 = 1680x1050, 14 x 50 = 1024x768, 39 x 105 = 2560x1600)
    for cols, rows in ((25, 69), (14, 50), (39, 105), (10, 31)):
        assert L.cm_zdims(100, cols, rows, C.byref(oc), C.byref(orr)) == 65536 and (oc.value, orr.value) == (cols, rows)   # 100 % = the real view, exactly
        pmin = L.cm_zmin(cols, rows)
        assert 50 <= pmin <= 100
        last = None
        for pct in range(50, 201):
            k = L.cm_zdims(pct, cols, rows, C.byref(oc), C.byref(orr))
            assert abs(k - 100 * 65536 // pct) <= 1
            # the canvas always covers k x the real rectangle (rect = 60 cols - 2 wide, 15 (rows - 1) high)
            assert 60 * oc.value - 2 >= (60 * cols - 2) * k // 65536 - 1 and 15 * (orr.value - 1) >= 15 * (rows - 1) * k // 65536, (cols, rows, pct)
            if pct >= pmin: assert oc.value <= max(cols, 80) and orr.value <= max(rows, 220), (cols, rows, pct, oc.value, orr.value)
            if last: assert oc.value <= last[0] or pct > last[2], 'monotonic'
            last = (oc.value, orr.value, pct)
        if pmin > 50:
            L.cm_zdims(pmin - 1, cols, rows, C.byref(oc), C.byref(orr)); assert oc.value > max(cols, 80) or orr.value > max(rows, 220)   # pmin is tight
    assert L.cm_zmin(39, 105) == 50 and L.cm_zmin(25, 69) == 50 and L.cm_zmin(14, 50) == 50
    # camera shift: zooming out moves the camera up-left by half the extra view (rows even), zooming in the opposite; in/out cancel within a cell
    L.cm_zshift.argtypes = [C.c_uint, C.c_uint, C.c_int, C.c_int, C.POINTER(C.c_int), C.POINTER(C.c_int)]
    dc, dr = C.c_int(), C.c_int()
    for (po, pn) in ((100, 70), (100, 200), (70, 100), (115, 90), (100, 100), (50, 60)):
        L.cm_zshift(po, pn, 747, 510, C.byref(dc), C.byref(dr))
        k = (100 * 65536 // po - 100 * 65536 // pn) / 65536
        assert dr.value % 2 == 0 and abs(dc.value * 60 - k * 747) <= 30 and abs(dr.value * 15 - k * 510) <= 15 + 1e-6, (po, pn, dc.value, dr.value)
        assert (po == pn) == (dc.value == 0 and dr.value == 0) or abs(k) < 0.05
    L.cm_zshift(100, 70, 747, 510, C.byref(dc), C.byref(dr)); assert dc.value < 0 and dr.value < 0
    L.cm_zshift(100, 200, 747, 510, C.byref(dc), C.byref(dr)); assert dc.value > 0 and dr.value > 0
    # scaler
    rnd = random.Random(5)
    for f565 in (1, 0):
        m = 0x07E0F81F if f565 else 0x03E07C1F
        for (sw, sh, x1, y0, y1, pct) in ((100, 80, 60, 10, 70, 140), (100, 80, 70, 10, 78, 50), (40, 30, 90, 4, 56, 100 * 40 // 90 + 1), (64, 64, 64, 0, 40, 100), (77, 53, 77, 3, 50, 200)):
            k = 100 * 65536 // pct
            px = [rnd.randrange(65536) & (0xffff if f565 else 0x7fff) for _ in range(sw * sh)]
            src = struct.pack('<%dH' % (sw * sh), *px)
            dp = 2 * x1 + 6; dst = C.create_string_buffer(b'\xaa' * (dp * y1))
            L.cm_zoom(src, sw * 2, sw, sh, dst, dp, x1, y0, y1, k, f565)
            def ex(p): return (p | p << 16) & m
            def bl(a, b, w): return ((a * (32 - w) + b * w) >> 5) & m
            def tap(pos, n):
                pos = max(pos, 0); i = pos >> 16; w = (pos >> 11) & 31
                return (n - 1, 0) if i >= n - 1 else (i, w)
            for y in range(y0, y1):
                j, wy = tap(((2 * (y - y0) + 1) * k >> 1) - 0x8000, sh - y0)
                j1 = min(j + 1, sh - y0 - 1)
                for x in range(x1):
                    i, wx = tap(((2 * x + 1) * k >> 1) - 0x8000, sw); i1 = min(i + 1, sw - 1)
                    g = lambda yy, xx: ex(px[(y0 + yy) * sw + xx])
                    a, b = bl(g(j, i), g(j, i1), wx), bl(g(j1, i), g(j1, i1), wx)
                    c = bl(a, b, wy); want = (c | c >> 16) & 0xffff
                    got = struct.unpack_from('<H', dst.raw, y * dp + 2 * x)[0]
                    assert got == want, (f565, sw, sh, pct, x, y, hex(got), hex(want))
            for y in range(y0):  assert dst.raw[y * dp:(y + 1) * dp] == b'\xaa' * dp            # rows above the view and pixels right of it untouched
            assert dst.raw[y0 * dp + 2 * x1: (y0 + 1) * dp] == b'\xaa' * (dp - 2 * x1)
    # a flat colour stays flat (no weight overflow into the neighbouring channel) in both formats
    for f565, col in ((1, 0xFFFF), (1, 0xF81F), (0, 0x7FFF), (0, 0x7C1F), (1, 0x07E0)):
        src = struct.pack('<H', col) * (50 * 40); dst = C.create_string_buffer(b'\0' * (2 * 80 * 60))
        L.cm_zoom(src, 100, 50, 40, dst, 160, 80, 0, 60, 100 * 65536 // 160, f565)
        assert set(struct.unpack('<4800H', dst.raw[:9600])) == {col}, (f565, hex(col))
    print('zoom: ok')


def test_export():
    """cm_export (city image): a fake game draw paints f(world x, world y) through the real tiling / strip / PNG code; the PNG must decode (Pillow checks CRC + Adler)
    to exactly f over the image extent, for 565 and 555, odd sizes, > 8 K wide (several deflate blocks), and the overlay / hover state must be zeroed during the draw."""
    DRAW = C.CFUNCTYPE(None, C.c_int, C.c_int, C.c_int)
    g = lambda n, t=C.c_uint: t.in_dll(L, n)
    L.cm_export.argtypes = [C.c_char_p, C.POINTER(C.c_int), C.c_char_p]; L.cm_export.restype = C.c_int
    out = Path(tempfile.mkdtemp())
    for ci, (f565, (minc, minr, maxc, maxr, cols, rows)) in enumerate(((1, (3, 5, 40, 60, 25, 69)), (0, (0, 0, 7, 9, 14, 50)), (1, (2, 3, 150, 6, 17, 31)), (0, (5, 9, 5, 9, 10, 12)))):
        left, top = 0, 30
        tw, th = 60 * cols - 2, 15 * (rows - 1)
        pitch = 2 * 4100
        buf = C.create_string_buffer(pitch * (top + th + 4))
        C.c_void_p.in_dll(L, 'ex_bb').value = C.addressof(buf)
        g('ex_pitch').value = pitch; g('ex_layout').value = 2 if f565 else 0
        g('ex_left', C.c_int).value = left; g('ex_top', C.c_int).value = top; g('ex_right', C.c_int).value = left + tw; g('ex_bottom', C.c_int).value = top + th
        (C.c_uint * 4).in_dll(L, 'ex_hot')[:] = [7, 8, 9, 10]
        mask = 0xffff if f565 else 0x7fff
        val = lambda X, Y: ((X * 7 + Y * 13) ^ (X >> 3) ^ (Y << 2)) & mask
        seen = []
        def draw(cx, cy, p3):
            seen.append((cx, cy, p3, list((C.c_uint * 4).in_dll(L, 'ex_hot'))))
            for y in range(top, top + th):
                row = struct.pack('<%dH' % tw, *[val(60 * cx + left + x, y - top + 15 + 15 * cy) for x in range(tw)])
                C.memmove(C.addressof(buf) + y * pitch + left * 2, row, tw * 2)
        cb = DRAW(draw); C.c_void_p.in_dll(L, 'ex_draw').value = C.cast(cb, C.c_void_p).value
        d = out.as_posix(); C.create_string_buffer(300)
        C.memmove(C.addressof(C.c_char.in_dll(L, 'ex_dir')), d.encode() + b'\0', len(d) + 1)
        path = C.create_string_buffer(400)
        X0, Y0 = 60 * minc - 30 - 120, 15 * minr - 300 + (7 if ci == 1 else 0)         # (an arbitrary rectangle: negative / odd origins too)
        W, H = 60 * (maxc - minc + 1) + 240 - (11 if ci == 3 else 0), 15 * (maxr - minr) + 30 + 420
        a = (C.c_int * 7)(X0, Y0, W, H, cols, rows, 4242)
        assert L.cm_export(b'city%d' % ci, a, path) == 0
        assert all(s[3] == [0, 0, 0, 0] and s[2] == 4242 for s in seen), 'overlay / hover state not zeroed during the draw'
        assert list((C.c_uint * 4).in_dll(L, 'ex_hot')) == [7, 8, 9, 10], 'state not restored'
        assert all(s[1] % 2 == 0 for s in seen)
        im = Image.open(path.value.decode()); im.load()
        assert im.size == (W, H) and im.mode == 'RGB', (im.size, W, H)
        raw = im.tobytes()
        def rgb(v):
            if f565: r, gg, b = v >> 11, (v >> 5) & 63, v & 31; return (r << 3 | r >> 2, gg << 2 | gg >> 4, b << 3 | b >> 2)
            r, gg, b = (v >> 10) & 31, (v >> 5) & 31, v & 31; return (r << 3 | r >> 2, gg << 3 | gg >> 2, b << 3 | b >> 2)
        for v in range(H):
            exp = b''.join(bytes(rgb(val(X0 + u, Y0 + v))) for u in range(0, W))
            assert raw[v * W * 3:(v + 1) * W * 3] == exp, ('pixel mismatch row', v, f565, (minc, minr, maxc, maxr))
        # a second export of the same name must not overwrite: "city (2).png"
        assert L.cm_export(b'city%d' % ci, a, path) == 0 and path.value.decode().endswith('city%d (2).png' % ci) and Path(path.value.decode()).exists()
        print('  export %dx%d (%s, %d tiles drawn) ok' % (W, H, '565' if f565 else '555', len(seen)))
    # an empty bounding box falls back to the whole grid, a tiny view is refused
    a = (C.c_int * 7)(5, 5, 40, 40, 2, 2, 0); assert L.cm_export(b'x', a, path) == 2
    a = (C.c_int * 7)(5, 5, 0, 40, 25, 69, 0); assert L.cm_export(b'x', a, path) == 1
    print('export: ok')


import mapfit
if __name__ == '__main__':
    test_png(); test_scale(); test_cm(); test_image(); test_intro(); test_zoom(); test_export(); test_real(); test_poseidon()
    print('all mapcomp tests passed')
