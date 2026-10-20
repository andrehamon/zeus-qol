"""World-map fit: one HD land image + ocean -> a map composite per resolution.

Single source of truth for the scale / offset used by BOTH the composite images (build_data.py)
and the exe's coordinate transform (build_exe.py -> stubs).  Map space = the original 838x738 art.
  screen_image_x = OX + ((x + off) * S16 >> 16) - off      (off = 0x3f only for Poseidon_map02, see stubs)
  screen_image_y = OY + (y * S16 >> 16)
"""
from pathlib import Path

NW, NH = 838, 738                 # native size of every world-map JPG
TOP = 30                          # the map starts below the 30px top bar
OCEAN = (0x1e, 0x5a, 0x7e)
ASSETS = Path(__file__).resolve().parent.parent / 'assets'

# map name -> land png (first existing wins).  One Greece land image serves Zeus_MapOfGreece01..10.
def land_candidates(name):
    if name.startswith('Zeus_MapOfGreece'):
        return [ASSETS / 'Zeus_MapOfGreece_Land.png', ASSETS / 'Zeus_MapOfGreece08_Land.png']
    if name in ('Poseidon_map01', 'Poseidon_map02', 'Poseidon_map03'):      # windows onto the one sheet
        return [ASSETS / 'Poseidon_map_full.png']
    return [ASSETS / 'Poseidon_map04_new.png']              # Poseidon_map04: already land over water (7261x3616, guide rect 1193,291)

MAPS = ['Zeus_MapOfGreece%02d' % i for i in range(1, 11)] + ['Poseidon_map%02d' % i for i in range(1, 5)]


def fit(vw, h):
    """viewport (vw x h-TOP) -> (S16, OX, OY, W, H) of the composite.  Identity for the native 1024x768 view."""
    vh = h - TOP
    s16 = -(-vh * 65536 // NH)       # the map's height fills the map screen (rounded up: no row short); a too wide map is cut off at the left
    sw = NW * s16 >> 16
    return s16, vw - sw, 0, vw, vh


# The exe loads three ready-made pictures (land laid over the ocean, see compose_map): Greece (kind 0, every Zeus map;
# land flush right, water left), the Poseidon sheet (kinds 1..3) and Poseidon map 4 (kind 4; land centred, water at both
# sides).  Greece / map 4 are exactly as tall as the land.  RECT = where the original 838x738 map lies in the picture;
# the picture is placed so that rectangle sits exactly where the cities expect it and the rest of the art runs on past it
# (for the Poseidon sheet that is real land).  KIND = row of the fit table.
SHEET = (5636, 3448)
PIC = {0: (5000, 2952), 1: SHEET, 2: SHEET, 3: SHEET, 4: (6100, 3616)}      # picture size per kind
RECT = {0: (1648, 0, 3352, 2952), 1: (887, 255, 3352, 2952), 2: (1751, 0, 3885, 3425), 3: (0, 495, 3352, 2952), 4: (613, 291, 3352, 2952)}
ANCH = {1: 'centre', 2: 'right', 3: 'left'}
MAXZOOM = 1.02                    # fill gaps by zooming at most this much (cities scale along with the picture)


def place(kind, s16, ox, oy):
    """Mirror of place() in cmod/mapcomp.c: (x, y, w, h) of the whole sheet in the composite."""
    dw, dh = NW * s16 >> 16, NH * s16 >> 16
    rx, ry, rw, rh = RECT[kind]
    sw, sh = PIC[kind]
    return (ox - (rx * dw + rw // 2) // rw, oy - (ry * dh + rh // 2) // rh,
            (sw * dw + rw // 2) // rw, (sh * dh + rh // 2) // rh)


def covers(kind, s16, ox, oy, vw, vh):
    x, y, w, h = place(kind, s16, ox, oy)
    return x <= 0 and y <= 0 and x + w >= vw and y + h >= vh      # (the composite's extra column copies its neighbour)


def fit_kind(kind, vw, h):
    """Like fit() but for Poseidon sheet window `kind` (1..3); kind 0 = fit().  The map's height always fills the viewport
    height and the art overflows left / right (OX can be negative: the part of the original map outside the screen is
    cut off).  If the sheet then does not cover the whole viewport the scale is raised a little (<= MAXZOOM).
    Returns (S16, OX, OY, W, H, covered)."""
    s16, ox, oy, w, vh = fit(vw, h)
    if kind == 0 or s16 == 65536 and vw == NW:
        return s16, ox, oy, w, vh, True
    if kind == 4:                                     # Poseidon_map04: the whole picture height fills the map screen, the picture is centred (the original map area is smaller, off-centre)
        rw, rh = RECT[4][2:]
        s = -(-(-(-vh * rh // PIC[4][1])) * 65536 // NH)
        x0, y0, pw, ph = place(4, s, 0, 0)
        return s, (vw - pw) // 2 - x0, (vh - ph) // 2 - y0, w, vh, True
    s0 = vh * 65536 // NH
    for z in range(0, int(s0 * (MAXZOOM - 1)) + 1, 16):
        s = s0 + z
        dw = NW * s >> 16
        ox = {'left': 0, 'right': vw - dw, 'centre': (vw - dw) // 2}[ANCH[kind]]
        if covers(kind, s, ox, 0, vw, vh):
            return s, ox, 0, w, vh, True
    dw = NW * s0 >> 16
    return s0, {'left': 0, 'right': vw - dw, 'centre': (vw - dw) // 2}[ANCH[kind]], 0, w, vh, False


def tx(x, s16, ox, off=0):
    return ox + ((x + off) * s16 >> 16) - off


def ty(y, s16, oy):
    return oy + (y * s16 >> 16)


# published name -> (the land picture, where it lies on the 5636x3448 ocean)
COMPOSED = {'Map_Greece.png': (land_candidates('Zeus_MapOfGreece01'), 0),
            'Map_Poseidon_1-3.png': (land_candidates('Poseidon_map01'), 1),
            'Map_Poseidon_4.png': (land_candidates('Poseidon_map04'), 4)}


def compose_map(cands, kind):
    """assets/ocean.png cut to the picture size of `kind`, with the (RGBA) land picture laid over it at its RECT -> opaque RGB."""
    from PIL import Image
    src = next(p for p in cands if p.exists())
    ocean_im = Image.open(ASSETS / 'ocean.png')
    assert ocean_im.size == SHEET
    pw, ph = PIC[kind]
    if kind == 4:                                          # ready-made picture: cut the unneeded width off both sides (the screen is at most 1.66 x as wide as tall)
        im = Image.open(src).convert('RGB')
        assert im.size == (7261, 3616)
        return im.crop(((7261 - pw) // 2, 0, (7261 - pw) // 2 + pw, ph))
    land = Image.open(src).convert('RGBA')
    im = ocean_im.crop((0, 0, pw, ph)).convert('RGBA')
    assert kind == 1 or land.size == (RECT[kind][2], RECT[kind][3]) and ph == land.height
    im.alpha_composite(land, (0, 0) if kind == 1 else RECT[kind][:2])
    return im.convert('RGB')


def ocean(w, h):
    from PIL import Image
    tile = ASSETS / 'Ocean_Tile.png'            # user-supplied seamless tile (optional)
    if not tile.exists():
        return Image.new('RGB', (w, h), OCEAN)
    t = Image.open(tile).convert('RGB')
    im = Image.new('RGB', (w, h))
    for y in range(0, h, t.height):
        for x in range(0, w, t.width):
            im.paste(t, (x, y))
    return im


def composite(land, s16, ox, oy, w, h):
    """land: RGBA PIL image covering the native NWxNH area (any integer multiple). Returns RGB w x h."""
    from PIL import Image
    assert land.width * NH == land.height * NW, 'land image must have the 838:738 aspect'
    sw, sh = round(NW * s16 / 65536), round(NH * s16 / 65536)
    scaled = land.convert('RGBa').resize((sw, sh), Image.LANCZOS).convert('RGBA')   # premultiplied: no dark fringes
    im = ocean(w, h)
    im.paste(scaled, (ox, oy), scaled)
    return im


if __name__ == '__main__':
    # sanity: 1024x768 view is exactly the native map (identity), every fit stays inside the viewport
    s, ox, oy, w, h = fit(838, 768)
    assert (s, ox, oy, w, h) == (65536, 0, 0, 838, 738), (s, ox, oy)
    for vw, H in ((1078, 720), (1738, 1080), (2338, 1600)):
        s, ox, oy, w, h = fit(vw, H)
        assert oy == 0 and ox + (NW * s >> 16) == w and (NH * s >> 16) >= h
        print(vw, H, 'scale %.4f ox %d oy %d' % (s / 65536, ox, oy))
    print('ok')
