/* World-map composite, built inside the game: a ready-made HD map picture (land already laid over the ocean) scaled to fit -> BGR24 per map, cached per resolution.  Hooked into the
 * game's JPG loader so the sprite the game draws is this composite (see NOTES.md "In-game map composite").
 *
 * Freestanding: no libc, no 64-bit division, no big stack frames (Windows only grows the stack one page at a
 * time).  Compiled for the exe by build/clink.py; with -DHOST the same code is built natively for tests. */
typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

#define NW 838                       /* native world-map size */
#define NH 738
#define NLAND 5                      /* map slots: 0 = Greece (all 10 Zeus maps), 1..4 = Poseidon_map01..04 */
#define NSRC 3                       /* decoded pictures: 0 = Greece (5000x2952), 1 = Poseidon sheet (maps 1..3, 5636x3448), 2 = Poseidon map 4 (6100x3616) */
#define OCEAN_B 0x7e
#define OCEAN_G 0x5a
#define OCEAN_R 0x1e

#ifdef HOST
#define LG(a, v) ((void)0)
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static char host_root[256];
static void *os_alloc(u32 n) { return calloc(1, n ? n : 1); }
static void os_free(void *p) { free(p); }
static u8 *os_read(const char *path, u32 *n) {
    char b[600]; u32 i = 0;
    snprintf(b, sizeof b, "%s/", host_root); i = strlen(b);
    for (const char *s = path; *s && i < sizeof b - 1; s++) b[i++] = *s == '\\' ? '/' : *s;
    b[i] = 0;
    FILE *f = fopen(b, "rb"); if (!f) return 0;
    fseek(f, 0, SEEK_END); *n = ftell(f); fseek(f, 0, SEEK_SET);
    u8 *d = malloc(*n); if (fread(d, 1, *n, f) != *n) { fclose(f); free(d); return 0; }
    fclose(f); return d;
}
static u32 os_head(const char *path, u8 *d, u32 n) {                 /* first n bytes of a file (the video is too big to read whole) */
    char b[600]; u32 i = 0;
    snprintf(b, sizeof b, "%s/", host_root); i = strlen(b);
    for (const char *s = path; *s && i < sizeof b - 1; s++) b[i++] = *s == '\\' ? '/' : *s;
    b[i] = 0;
    FILE *f = fopen(b, "rb"); if (!f) return 0;
    u32 got = (u32)fread(d, 1, n, f); fclose(f); return got;
}
u32 scr_w = 1680, scr_h = 1050;
#define SCR_W scr_w
#define SCR_H scr_h
static void *game_malloc(u32 n) { return malloc(n); }
static void game_free(void *p) { free(p); }
/* stand-in for the game's 0x504be0: BGR24 -> 16 bit 565 (what the loader does for [0xf9c0e4] == 0) */
static void game_conv(const u8 *bgr, u8 *out, u32 w, u32 h, u32 stride) {
    (void)stride;
    for (u32 i = 0; i < w * h; i++)
        ((u16 *)out)[i] = (u16)(((bgr[i * 3 + 2] & 0xf8) << 8) | ((bgr[i * 3 + 1] & 0xfc) << 3) | (bgr[i * 3] >> 3));
}
u32 cur_mode = 3;
u32 fittab[5 * 16 * 5], cur_r;
#define G_MODE cur_mode
#else
#define W32 __attribute__((stdcall))
#ifdef CMLOG
/* progress log Zeus.cm.log (build with ZEUS_CMLOG=1): "tag value ticks" lines, written unbuffered so a crash leaves the last step */
static void lg(u32 tag, u32 v) {
    static void *h;
    if (!h) h = ((void *(W32 *)(const char *, u32, u32, void *, u32, u32, void *))(*(u32 *)0x5d80c4))("Zeus.cm.log", 0x40000000, 1, 0, 2, 0, 0);
    if (h == (void *)-1) return;
    char b[32]; u32 n = 0, t = ((u32(W32 *)(void))(*(u32 *)0x5d80d0))();
    for (int k = 0; k < 4; k++) b[n++] = (char)(tag >> (8 * k));
    b[n++] = ' ';
    for (int k = 28; k >= 0; k -= 4) b[n++] = "0123456789abcdef"[(v >> k) & 15];
    b[n++] = ' ';
    for (int k = 28; k >= 0; k -= 4) b[n++] = "0123456789abcdef"[(t >> k) & 15];
    b[n++] = '\n';
    u32 got;
    ((int (W32 *)(void *, const void *, u32, u32 *, void *))(*(u32 *)0x5d8084))(h, b, n, &got, 0);
}
#define LG(a, v) lg(*(const u32 *)a, (u32)(v))
#else
#define LG(a, v) ((void)0)
#endif
static void *os_alloc(u32 n) { return ((void *(W32 *)(void *, u32, u32, u32))(*(u32 *)0x5d804c))(0, n ? n : 1, 0x3000, 4); }
static void os_free(void *p) { if (p) ((int (W32 *)(void *, u32, u32))(*(u32 *)0x5d8050))(p, 0, 0x8000); }
static u8 *os_read(const char *path, u32 *n) {
    void *h = ((void *(W32 *)(const char *, u32, u32, void *, u32, u32, void *))(*(u32 *)0x5d80c4))(path, 0x80000000, 1, 0, 3, 0, 0);
    if (h == (void *)-1) return 0;
    u32 sz = ((u32(W32 *)(void *, u32 *))(*(u32 *)0x5d80f4))(h, 0), got = 0;
    u8 *d = sz && sz < 0x10000000 ? os_alloc(sz) : 0;
    if (d && !((int (W32 *)(void *, void *, u32, u32 *, void *))(*(u32 *)0x5d8088))(h, d, sz, &got, 0)) got = 0;
    ((int (W32 *)(void *))(*(u32 *)0x5d80bc))(h);
    LG("RDsz", sz); LG("RDgt", got);
    if (!d || got != sz) { os_free(d); return 0; }
    *n = sz; return d;
}
static u32 os_head(const char *path, u8 *d, u32 n) {
    void *h = ((void *(W32 *)(const char *, u32, u32, void *, u32, u32, void *))(*(u32 *)0x5d80c4))(path, 0x80000000, 1, 0, 3, 0, 0);
    if (h == (void *)-1) return 0;
    u32 got = 0;
    if (!((int (W32 *)(void *, void *, u32, u32 *, void *))(*(u32 *)0x5d8088))(h, d, n, &got, 0)) got = 0;
    ((int (W32 *)(void *))(*(u32 *)0x5d80bc))(h);
    return got;
}
#define SCR_W (*(u32 *)0xf2a6b4)
#define SCR_H (*(u32 *)0xf2a6b0)
#define game_malloc ((void *(*)(u32))0x5cc7c0)
#define game_free ((void (*)(void *))0x5cc872)
#define game_conv ((void (*)(const u8 *, u8 *, u32, u32, u32))0x504be0)
#define G_MODE (*(u32 *)0xf2a770)
extern u32 fittab[], cur_r;           /* stub data (clink resolves them) */
#endif

/* memory helpers (the compiler must not turn these loops into calls to memcpy/memset) */
#ifdef HOST
static void mcopy(u8 *d, const u8 *s, u32 n) { while (n--) *d++ = *s++; }
static void mzero(void *d, u32 n) { memset(d, 0, n); }
#else
static void mcopy(u8 *d, const u8 *s, u32 n) { __asm__ volatile("rep movsb" : "+D"(d), "+S"(s), "+c"(n) : : "memory"); }
static void mzero(void *d, u32 n) { u8 z = 0; __asm__ volatile("rep stosb" : "+D"(d), "+c"(n) : "a"(z) : "memory"); }
#endif

/* ================================================================ inflate (RFC 1951) */
struct huff { u16 count[16]; u16 sym[288]; };
struct inf {
    const u8 *in, *inend; u32 bb, bn;
    u8 *out; u32 op, olen; int err;
    struct huff lit, dst;
};
static const u16 lbase[29] = {3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258};
static const u16 lext[29] = {0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0};
static const u16 dbase[30] = {1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577};
static const u16 dext[30] = {0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13};
static const u8 clorder[19] = {16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15};

static u32 getbits(struct inf *s, u32 need) {
    u32 v = s->bb;
    while (s->bn < need) {
        if (s->in >= s->inend) { s->err = 1; return 0; }
        v |= (u32)*s->in++ << s->bn; s->bn += 8;
    }
    s->bb = v >> need; s->bn -= need;
    return v & ((1u << need) - 1);
}

static int build(struct huff *h, const u8 *len, u32 n) {
    u16 offs[16]; int left = 1;
    for (u32 i = 0; i < 16; i++) h->count[i] = 0;
    for (u32 i = 0; i < n; i++) h->count[len[i]]++;
    for (u32 l = 1; l < 16; l++) { left <<= 1; left -= h->count[l]; if (left < 0) return -1; }   /* over-subscribed */
    offs[1] = 0;
    for (u32 l = 1; l < 15; l++) offs[l + 1] = offs[l] + h->count[l];
    for (u32 i = 0; i < n; i++) if (len[i]) h->sym[offs[len[i]]++] = (u16)i;
    return 0;
}

static int decode(struct inf *s, const struct huff *h) {
    int code = 0, first = 0, index = 0;
    for (u32 l = 1; l < 16; l++) {
        code |= (int)getbits(s, 1);
        if (s->err) return -1;
        int c = h->count[l];
        if (code - c < first) return h->sym[index + (code - first)];
        index += c; first += c; first <<= 1; code <<= 1;
    }
    return -1;
}

#ifdef CMLOG
static u32 lognext;
#define LGPROG(s) do { if ((s)->op >= lognext) { LG("INFL", (s)->op); lognext = (s)->op + 0x400000; } } while (0)
#else
#define LGPROG(s) ((void)0)
#endif
static int codes(struct inf *s) {
    for (;;) {
        LGPROG(s);
        int sy = decode(s, &s->lit);
        if (sy < 0) return -1;
        if (sy < 256) { if (s->op >= s->olen) return -1; s->out[s->op++] = (u8)sy; continue; }
        if (sy == 256) return 0;
        sy -= 257; if (sy >= 29) return -1;
        u32 len = lbase[sy] + getbits(s, lext[sy]);
        int ds = decode(s, &s->dst); if (ds < 0 || ds >= 30) return -1;
        u32 dist = dbase[ds] + getbits(s, dext[ds]);
        if (s->err || dist > s->op || s->op + len > s->olen) return -1;
        u8 *o = s->out + s->op; const u8 *f = o - dist;
        for (u32 i = 0; i < len; i++) o[i] = f[i];
        s->op += len;
    }
}

static int dynamic(struct inf *s) {
    u8 lens[320]; u32 nlen = getbits(s, 5) + 257, ndist = getbits(s, 5) + 1, ncode = getbits(s, 4) + 4;
    if (nlen > 286 || ndist > 30 || s->err) return -1;
    for (u32 i = 0; i < 19; i++) lens[i] = 0;
    for (u32 i = 0; i < ncode; i++) lens[clorder[i]] = (u8)getbits(s, 3);
    if (build(&s->lit, lens, 19)) return -1;                   /* s->lit is borrowed for the code-length code */
    u32 idx = 0; u8 cl[320];
    while (idx < nlen + ndist) {
        int sy = decode(s, &s->lit); if (sy < 0) return -1;
        if (sy < 16) cl[idx++] = (u8)sy;
        else {
            u8 v = 0; u32 rep;
            if (sy == 16) { if (!idx) return -1; v = cl[idx - 1]; rep = 3 + getbits(s, 2); }
            else if (sy == 17) rep = 3 + getbits(s, 3);
            else rep = 11 + getbits(s, 7);
            if (idx + rep > nlen + ndist) return -1;
            while (rep--) cl[idx++] = v;
        }
    }
    if (s->err || cl[256] == 0) return -1;
    if (build(&s->lit, cl, nlen)) return -1;
    if (build(&s->dst, cl + nlen, ndist)) return -1;
    return 0;
}

/* zlib stream -> out[olen]; returns 0 only if exactly olen bytes came out */
static int inflate_z(const u8 *z, u32 zlen, u8 *out, u32 olen) {
    static struct inf s;                                        /* ~1.3 KB: keep it off the stack */
    if (zlen < 6 || (z[0] & 15) != 8 || ((z[0] << 8) | z[1]) % 31 || (z[1] & 0x20)) return -1;
    mzero(&s, sizeof s);
#ifdef CMLOG
    lognext = 0;
#endif
    s.in = z + 2; s.inend = z + zlen; s.out = out; s.olen = olen;
    int last;
    do {
        last = (int)getbits(&s, 1);
        u32 type = getbits(&s, 2);
        if (s.err) return -1;
        if (type == 0) {
            s.bb = 0; s.bn = 0;
            if (s.inend - s.in < 4) return -1;
            u32 n = s.in[0] | (s.in[1] << 8);
            if ((n ^ (s.in[2] | (s.in[3] << 8))) != 0xffff) return -1;
            s.in += 4;
            if ((u32)(s.inend - s.in) < n || s.op + n > olen) return -1;
            mcopy(out + s.op, s.in, n); s.in += n; s.op += n;
        } else if (type == 1) {
            u8 l[288]; u32 i = 0;
            for (; i < 144; i++) l[i] = 8;
            for (; i < 256; i++) l[i] = 9;
            for (; i < 280; i++) l[i] = 7;
            for (; i < 288; i++) l[i] = 8;
            build(&s.lit, l, 288);
            for (i = 0; i < 30; i++) l[i] = 5;
            build(&s.dst, l, 30);
            if (codes(&s)) return -1;
        } else if (type == 2) {
            if (dynamic(&s) || codes(&s)) return -1;
        } else return -1;
    } while (!last);
    return s.op == olen ? 0 : -1;
}

/* ================================================================ PNG (8 bit, RGB or RGBA, not interlaced) */
struct png { u32 w, h, ch; u8 *z; u32 zlen; };

static u32 be32(const u8 *p) { return ((u32)p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3]; }

/* parses the chunks; the IDAT payloads are packed together in place inside d */
int png_open(u8 *d, u32 n, struct png *p) {
    static const u8 sig[8] = {0x89, 'P', 'N', 'G', 13, 10, 26, 10};
    if (n < 33) return -1;
    for (u32 i = 0; i < 8; i++) if (d[i] != sig[i]) return -1;
    u32 pos = 8, got = 0; p->z = 0; p->zlen = 0;
    while (pos + 12 <= n) {
        u32 len = be32(d + pos);
        if (len > n - pos - 12) return -1;
        const u8 *tag = d + pos + 4, *body = d + pos + 8;
        if (tag[0] == 'I' && tag[1] == 'H' && tag[2] == 'D' && tag[3] == 'R') {
            if (len != 13 || body[8] != 8 || body[10] || body[11] || body[12]) return -1;    /* 8 bit, deflate, no filter variants, no interlace */
            if (body[9] == 2) p->ch = 3; else if (body[9] == 6) p->ch = 4; else return -1;
            p->w = be32(body); p->h = be32(body + 4); got = 1;
            if (!p->w || !p->h || p->w > 16384 || p->h > 16384) return -1;
        } else if (tag[0] == 'I' && tag[1] == 'D' && tag[2] == 'A' && tag[3] == 'T') {
            if (!got) return -1;
            if (!p->z) p->z = d + pos + 8;
            mcopy(p->z + p->zlen, body, len);                  /* dst <= src: forward copy is safe */
            p->zlen += len;
        } else if (tag[0] == 'I' && tag[1] == 'E' && tag[2] == 'N' && tag[3] == 'D') break;
        pos += 12 + len;
    }
    return p->z && p->zlen ? 0 : -1;
}

u32 png_bufsize(const struct png *p) { return p->h * (p->w * p->ch + 1); }

/* inflate + unfilter in place: afterwards rows are packed at buf + y * w * ch */
int png_pixels(const struct png *p, u8 *buf) {
    if (inflate_z(p->z, p->zlen, buf, png_bufsize(p))) return -1;
    u32 bpp = p->ch, stride = p->w * bpp;
    for (u32 y = 0; y < p->h; y++) {
        const u8 *src = buf + y * (stride + 1);
        u8 f = *src++, *dst = buf + y * stride;
        const u8 *up = y ? dst - stride : 0;
        if (f > 4) return -1;
        for (u32 x = 0; x < stride; x++) {
            u32 a = x >= bpp ? dst[x - bpp] : 0, b = up ? up[x] : 0, c = (up && x >= bpp) ? up[x - bpp] : 0, v = src[x];
            switch (f) {
            case 1: v += a; break;
            case 2: v += b; break;
            case 3: v += (a + b) >> 1; break;
            case 4: {
                int pa = (int)b - (int)c, pb = (int)a - (int)c, pc = pa + pb;
                if (pa < 0) pa = -pa;
                if (pb < 0) pb = -pb;
                if (pc < 0) pc = -pc;
                v += (pa <= pb && pa <= pc) ? a : (pb <= pc) ? b : c;
            }
            }
            dst[x] = (u8)v;
        }
    }
    return 0;
}

/* ================================================================ scale + blend */
/* Area-average (box) downscale of the sw x sh image (ch = 3|4 channels, alpha premultiplied) to dw x dh,
 * blended over the BGR24 picture `dst` (width cw) at (ox, oy).  Exact integer arithmetic, fully deterministic:
 * a destination pixel covers sw/dw source pixels horizontally and sh/dh vertically (each source pixel
 * contributes in proportion to its overlap).  work: dw * 8 u32. */
void scale_blend(const u8 *src, u32 sw, u32 sh, u32 ch, u8 *dst, u32 cw, u32 chh, int ox, int oy, u32 dw, u32 dh, u32 *work) {
    u32 *hr = work, *acc = work + dw * 4;
    mzero(acc, dw * 16);
    u32 yd = 0, yend = sh;                         /* current destination row and its end in "dh units" of the source rows */
    for (u32 j = 0; j < sh; j++) {
        const u8 *p = src + j * sw * ch;
        /* horizontal reduction of source row j into hr[dw][4] (values 0..65025, premultiplied) */
        u32 x = 0, xe = sw, a0 = 0, a1 = 0, a2 = 0, a3 = 0;
        for (u32 i = 0; i < sw; i++, p += ch) {
            u32 a = ch == 4 ? p[3] : 255, c0 = p[0] * a, c1 = p[1] * a, c2 = p[2] * a, c3 = a * 255;
            u32 s = i * dw, e = s + dw;
            while (s < e) {
                u32 seg = (e < xe ? e : xe) - s;
                a0 += c0 * seg; a1 += c1 * seg; a2 += c2 * seg; a3 += c3 * seg;
                s += seg;
                if (s == xe) {
                    hr[x * 4] = a0 / sw; hr[x * 4 + 1] = a1 / sw; hr[x * 4 + 2] = a2 / sw; hr[x * 4 + 3] = a3 / sw;
                    a0 = a1 = a2 = a3 = 0; x++; xe += sw;
                }
            }
        }
        /* vertical: source row j covers [j*dh, (j+1)*dh) of the destination rows' [y*sh, (y+1)*sh) spans */
        u32 s = j * dh, e = s + dh;
        while (s < e) {
            u32 seg = (e < yend ? e : yend) - s;
            for (u32 k = 0; k < dw * 4; k++) acc[k] += hr[k] * seg;
            s += seg;
            if (s == yend) {
                int py = oy + (int)yd;
                u32 q0 = ox < 0 ? (u32)-ox : 0, q1 = (int)dw + ox > (int)cw ? cw - ox : dw;   /* clip to the picture */
                u8 *row = dst + ((u32)(py < 0 ? 0 : py) * cw + ox + q0) * 3;
                for (u32 q = q0; py >= 0 && py < (int)chh && q < q1; q++, row += 3) {
                    u32 A = acc[q * 4 + 3] / sh, ia = 65025 - A;
                    for (u32 c = 0; c < 3; c++) {          /* acc order is src order R,G,B; picture is B,G,R */
                        u32 v = (acc[q * 4 + 2 - c] / sh * 255 + row[c] * ia + 32512) / 65025;   /* P = 255 * colour * alpha */
                        row[c] = (u8)(v > 255 ? 255 : v);
                    }
                }
                mzero(acc, dw * 16);
                yd++; yend += sh;
            }
        }
    }
}

/* ocean fill: tile (ch 3|4, tw x th, native size) repeated from (0,0), or the solid colour */
void ocean_fill(u8 *dst, u32 cw, u32 chh, const u8 *tile, u32 tw, u32 th, u32 tch) {
    for (u32 y = 0; y < chh; y++) {
        u8 *r = dst + y * cw * 3;
        if (!tile) { for (u32 x = 0; x < cw; x++) { r[x * 3] = OCEAN_B; r[x * 3 + 1] = OCEAN_G; r[x * 3 + 2] = OCEAN_R; } continue; }
        const u8 *t = tile + (y % th) * tw * tch;
        u32 tx = 0;
        for (u32 x = 0; x < cw; x++) {
            r[x * 3] = t[tx * tch + 2]; r[x * 3 + 1] = t[tx * tch + 1]; r[x * 3 + 2] = t[tx * tch];
            if (++tx == tw) tx = 0;
        }
    }
}

/* ================================================================ cache */
struct land { u8 *pix; u32 w, h, ch; int state; };               /* state 0 = not tried, 1 = loaded, 2 = unavailable */
struct comp { u8 *bgr; u32 w, h; int key; };
static struct land land[NSRC], tile;
static struct comp comp[NLAND];
u32 cm_stat[4];                                                   /* [0] loaded-picture mask  [1] failed-picture mask  [2] builds */

static const char *land_path[NSRC][2] = {
    {"Data\\Maps\\Map_Greece.png", 0},
    {"Data\\Maps\\Map_Poseidon_1-3.png", 0},
    {"Data\\Maps\\Map_Poseidon_4.png", 0},
};
static const u8 slot_src[NLAND] = {0, 1, 1, 1, 2};               /* slots 1..3 are three windows onto the one Poseidon sheet */
/* where the original map (838x738 native) sits inside each picture: x, y, w, h, then the picture size, all in the same units
 * (any whole fraction of the real size also works); row = fit-table "kind" (0 Greece, 1..3 sheet, 4 map04) */
static const u32 rect[5][6] = {{1648, 0, 3352, 2952, 5000, 2952}, {887, 255, 3352, 2952, 5636, 3448}, {1751, 0, 3885, 3425, 5636, 3448},
                               {0, 495, 3352, 2952, 5636, 3448}, {613, 291, 3352, 2952, 6100, 3616}};
static const u8 src_kind[NSRC] = {0, 1, 4};                      /* a picture's row in rect[] (the sheet: any of 1..3 has its size) */

static void load_png(struct land *l, const char *path) {
    u32 n = 0; u8 *f = os_read(path, &n);
    if (!f) return;
    struct png p; u8 *buf = 0;
    int orc = png_open(f, n, &p);
    LG("PNop", orc); LG("PNw ", p.w); LG("PNh ", p.h); LG("PNzl", p.zlen);
    if (!orc && (buf = os_alloc(png_bufsize(&p)))) {
        LG("PNbf", (u32)buf);
        int prc = png_pixels(&p, buf);
        LG("PNpx", prc);
        if (!prc) { l->pix = buf; l->w = p.w; l->h = p.h; l->ch = p.ch; l->state = 1; }
        else os_free(buf);
    }
    os_free(f);
}

static void ensure_land(int i) {
    struct land *l = &land[i];
    if (l->state) return;
    LG("LDbg", i);
    l->state = 2;
    for (int k = 0; k < 2 && l->state == 2 && land_path[i][k]; k++) load_png(l, land_path[i][k]);
    if (l->state == 1 && l->w * rect[src_kind[i]][5] != l->h * rect[src_kind[i]][4]) {   /* wrong proportions: unusable */
        os_free(l->pix); l->pix = 0; l->state = 2;
    }
    LG("LDen", l->state); LG("LDw ", l->w); LG("LDh ", l->h);
    cm_stat[l->state == 1 ? 0 : 1] |= 1u << i;
}

/* key 0 = native 838x738 (1024x768); key R>0 = fit table entry R; 14 = 800x600 (mode 2), 15 = 640x480 (mode 1): same slots as FitParams */
static int cur_key(u32 mode) { return mode == 1 ? 15 : mode == 2 ? 14 : mode == 3 ? (int)cur_r : 0; }

/* Where the picture goes in the composite: the map's own rectangle is NW x NH scaled by s16 at (ox, oy); the Poseidon
 * sheet is placed so its guide rect lands exactly there and the rest of the art runs on past it.  out[] = x, y, w, h
 * (x, y may be negative: the part outside the composite is clipped). */
void place(u32 kind, u32 s16, u32 ox, u32 oy, int *out) {
    u32 dw = (NW * s16) >> 16, dh = (NH * s16) >> 16;
    const u32 *r = rect[kind];
    out[0] = (int)ox - (int)((r[0] * dw + r[2] / 2) / r[2]);
    out[1] = (int)oy - (int)((r[1] * dh + r[3] / 2) / r[3]);
    out[2] = (r[4] * dw + r[2] / 2) / r[2];
    out[3] = (r[5] * dh + r[3] / 2) / r[3];
}

static int build_one(int slot, int key) {
    int si = slot_src[slot];
    LG("B1bg", slot * 100 + key);
    ensure_land(si);
    struct land *l = &land[si];
    if (l->state != 1) return -1;
    if (!tile.state) {
        tile.state = 2;
        load_png(&tile, "Data\\Maps\\Ocean_Tile.png");
    }
    u32 kind = (u32)slot;                                         /* fit-table kind = slot: 1..3 sheet windows, 4 = map04 centred */
    u32 s16 = 65536, ox = 0, oy = 0, w = NW, h = NH;
    if (key) { const u32 *f = fittab + (kind * 16 + key) * 5; s16 = f[0]; ox = f[1]; oy = f[2]; w = f[3]; h = f[4]; }
    int pl[4];
    place(kind, s16, ox, oy, pl);
    if (pl[2] <= 0 || pl[3] <= 0) return -1;
    struct comp *c = &comp[slot];
    os_free(c->bgr); c->bgr = 0; c->key = -1;
    u8 *bgr = os_alloc(w * h * 3); u32 *work = os_alloc(pl[2] * 32);
    if (!bgr || !work) { os_free(bgr); os_free(work); return -1; }
    ocean_fill(bgr, w, h, tile.state == 1 ? tile.pix : 0, tile.w, tile.h, tile.ch);
    scale_blend(l->pix, l->w, l->h, l->ch, bgr, w, h, pl[0], pl[1], pl[2], pl[3], work);
    if (key)                                                    /* the composite is one column wider than the viewport: extend the picture */
        for (u32 y = 0; y < h; y++) mcopy(bgr + (y * w + w - 1) * 3, bgr + (y * w + w - 2) * 3, 3);
    os_free(work);
    c->bgr = bgr; c->w = w; c->h = h; c->key = key;
    cm_stat[2]++;
    LG("B1en", slot);
    return 0;
}

/* called after every mode / resolution change (and at start-up): decode what is missing, rebuild for the new size */
void cm_build(u32 mode) {
    int key = cur_key(mode);
    LG("CBbg", mode * 100 + key);
    for (int i = 0; i < NLAND; i++) if (comp[i].key != key || !comp[i].bgr) build_one(i, key);
    LG("CBen", mode);
}

static u32 lc(u8 c) { return (c >= 'A' && c <= 'Z') ? c + 32u : c; }

/* "<anything>\Zeus_MapOfGreeceNN.jpg" / "...Poseidon_map0N.jpg" -> land index, else -1 */
static int map_index(const char *path) {
    const char *b = path;
    for (const char *s = path; *s; s++) if (*s == '\\' || *s == '/') b = s + 1;
    static const char g[] = "zeus_mapofgreece", p[] = "poseidon_map0";
    u32 i = 0;
    while (g[i] && lc((u8)b[i]) == g[i]) i++;
    if (!g[i]) return 0;
    for (i = 0; p[i] && lc((u8)b[i]) == p[i]; i++) ;
    if (!p[i] && b[i] >= '1' && b[i] <= '4') return b[i] - '0';
    return -1;
}

/* drop-in for the game's JPG loader 0x504de0(path, u8 **pixels, u32 *w, u32 *h): same outputs, returns w*h*2,
 * or 0 = not handled (the stub then runs the original loader) */
u32 cm_load(const char *path, u8 **pixels, u32 *pw, u32 *ph) {
    int i = map_index(path);
    if (i < 0) return 0;
    int key = cur_key(G_MODE);
    if (comp[i].key != key || !comp[i].bgr) if (build_one(i, key)) return 0;
    struct comp *c = &comp[i];
    if (*pixels) { game_free(*pixels); *pixels = 0; }
    u32 sz = c->w * c->h * 2;
    u8 *out = game_malloc(sz);
    if (!out) return 0;
    game_conv(c->bgr, out, c->w, c->h, c->w);
    *pixels = out; *pw = c->w; *ph = c->h;
    return sz;
}

/* ================================================================ full-screen backgrounds
 * Data\\Images\\<name>.png (any size / proportions) is scaled to COVER the W x H screen: fit to the better direction, centred, the overhang is
 * cropped (no bars).  Hooked into the image loader 0x504fa0 right after it decoded the stock JPG (see ImgHook in the stubs). */
struct ovl { const char *png; int x, y; };                        /* PNG blended 1:1 at the game's screen origin ((W-800)/2, (H-600)/2) + (x, y) */
struct imgdef { const char *name, *base; struct ovl ov[2]; };      /* base: another PNG used as the picture instead of <name>.png */
#define P(n) {n, 0, {{0, 0, 0}, {0, 0, 0}}}
/* The painted frames of these screens sit where the game draws its own text / picture ((x, y) measured on the stock 1024x768 art, whose
 * origin is (112, 84), and on the widescreen packs): plain background + the frames as separate overlays, so they line up at every resolution */
static const struct imgdef img_def[] = {
    P("zeus_fe_missionintroduction"),
    {"zeus_fe_choosegame", "mainmenu-choosegame_bg", {{"zeus_fe_choosegame_dialog", 208, 10}, {0, 0, 0}}},
    {"zeus_fe_campaignselection", "zeus_fe_missionintroduction", {{"campaign_select_dialog", 364, 15}, {0, 0, 0}}},
    {"poseidon_fe_mainmenu", "mainmenu-choosegame_bg", {{"poseidon_fe_mainmenu_dialog", 67, 62}, {"poseidon_fe_mainmenu_dialog", 427, 62}}},
    P("zeus_fe_registry"), {"zeus_fe_tutorials", 0, {{"zeus_fe_tutorials_dialog", 48, 36}, {"zeus_fe_tutorials_dialog", 424, 36}}},
    P("zeus_victory"), P("zeus_defeat"), P("scoreb"),
    P("poseidon_fe_registry"), P("zeus_load1"), P("zeus_load2"), P("zeus_load3"), P("zeus_load4"),
    P("poseidon_load1"), P("poseidon_load2"), P("poseidon_load3"), P("poseidon_load4"), P("poseidon_load5"), P("poseidon_load6"),
    P("poseidon_load7"), P("poseidon_load8"), {0, 0, {{0, 0, 0}, {0, 0, 0}}}};

/* "<anything>\\name.jpg" -> the lower-case base name when it is one of ours, else 0 */
static const struct imgdef *img_match(const char *path, char *buf) {
    const char *b = path;
    for (const char *s = path; *s; s++) if (*s == '\\' || *s == '/') b = s + 1;
    u32 n = 0;
    while (b[n] && b[n] != '.' && n < 60) { buf[n] = (char)lc((u8)b[n]); n++; }
    buf[n] = 0;
    if (b[n] != '.' || lc((u8)b[n + 1]) != 'j' || lc((u8)b[n + 2]) != 'p' || lc((u8)b[n + 3]) != 'g' || b[n + 4]) return 0;
    for (int i = 0; img_def[i].name; i++) {
        u32 k = 0;
        while (img_def[i].name[k] && img_def[i].name[k] == buf[k]) k++;
        if (!img_def[i].name[k] && !buf[k]) return &img_def[i];
    }
    return 0;
}

/* destination size of the cover scale: the whole source scaled by the larger of W/sw, H/sh (rounded up so no edge is short) */
void cover(u32 sw, u32 sh, u32 W, u32 H, u32 *dw, u32 *dh) {
    if (W * sh >= H * sw) { *dw = W; *dh = (sh * W + sw - 1) / sw; }
    else { *dh = H; *dw = (sw * H + sh - 1) / sh; }
}

static void load_img(const char *nm, struct land *l) {
    static const char pre[] = "Data\\Images\\";
    char fn[96]; u32 n = 0;
    for (u32 i = 0; pre[i]; i++) fn[n++] = pre[i];
    for (u32 i = 0; nm[i] && n < 88; i++) fn[n++] = nm[i];
    fn[n++] = '.'; fn[n++] = 'p'; fn[n++] = 'n'; fn[n++] = 'g'; fn[n] = 0;
    mzero(l, sizeof *l);
    load_png(l, fn);
}

/* path = the JPG the game is loading.  Returns a game-malloc'd BGR24 buffer of W x H (the loader frees it), or 0 = not ours /
 * no usable PNG -> the loader keeps the JPG it decoded.  Every mode. */
u8 *cm_image(const char *path, u32 W, u32 H) {
    char nm[64];
    const struct imgdef *d;
    if (!W || !H || W > 16384 || H > 16384 || !(d = img_match(path, nm))) return 0;
    struct land l, o;
    load_img(d->base ? d->base : d->name, &l);
    if (!l.pix) return 0;
    u32 dw, dh; cover(l.w, l.h, W, H, &dw, &dh);
    u8 *out = game_malloc(W * H * 3); u32 *work = os_alloc(dw * 32);
    if (out && work) {
        mzero(out, W * H * 3);
        scale_blend(l.pix, l.w, l.h, l.ch, out, W, H, ((int)W - (int)dw) / 2, ((int)H - (int)dh) / 2, dw, dh, work);
        for (int k = 0; k < 2; k++) if (d->ov[k].png) {
            load_img(d->ov[k].png, &o);
            u32 *w2 = o.pix ? os_alloc(o.w * 32) : 0;
            if (w2) scale_blend(o.pix, o.w, o.h, o.ch, out, W, H, ((int)W - 800) / 2 + d->ov[k].x, ((int)H - 600) / 2 + d->ov[k].y, o.w, o.h, w2);
            os_free(w2); os_free(o.pix);
        }
    } else { if (out) game_free(out); out = 0; }
    os_free(work); os_free(l.pix);
    return out;
}

/* The launch video: Binks\Zeus_Intro_HD.bik (any size up to 8192), played by the game's own video code (hook "intro") and scaled to the
 * game window by the hook on its BinkCopyToBuffer call ("blit"): the picture is fitted inside the window (whole width, or whole height
 * when it is taller than the window), centred, the rest black.  The game's blit is 1:1 and does not clip, so a video wider than the stock
 * 640 is decoded into a private buffer here and scaled (2 x 2 box taps per destination pixel) into the locked surface.
 * cm_intro: 1 = that file exists and has a plausible Bink header (+0x14 width, +0x18 height).  The path is also in the stubs (INTRO_PATH). */
u32 cm_intro(void) {
    u8 h[32];
    if (os_head("Binks\\Zeus_Intro_HD.bik", h, 32) < 32 || h[0] != 'B' || h[1] != 'I' || h[2] != 'K') return 0;
    u32 w = h[0x14] | h[0x15] << 8 | h[0x16] << 16 | (u32)h[0x17] << 24, ht = h[0x18] | h[0x19] << 8 | h[0x1a] << 16 | (u32)h[0x1b] << 24;
    return w >= 64 && ht >= 64 && w <= 8192 && ht <= 8192;
}

static u32 vxs[8192];                                             /* source x of the two taps of each destination column (screens up to 4096 wide) */
/* src: sw x sh pixels of bpp bytes (2 = 565 when f565, else 555; 3 / 4 = any channel order) -> dst: a W x H window with row pitch `pitch` */
static void vscale(const u8 *src, u32 sw, u32 sh, u8 *dst, u32 pitch, u32 W, u32 H, u32 bpp, u32 f565) {
    u32 dw, dh;
    if (W * sh <= H * sw) { dw = W; dh = sh * W / sw; } else { dh = H; dw = sw * H / sh; }
    if (!dw) dw = 1;
    if (!dh) dh = 1;
    u32 x0 = (W - dw) / 2, y0 = (H - dh) / 2;
    for (u32 i = 0; i < dw; i++) { vxs[2 * i] = (4 * i + 1) * sw / (4 * dw); vxs[2 * i + 1] = (4 * i + 3) * sw / (4 * dw); }
    for (u32 j = 0; j < H; j++) {
        u8 *row = dst + j * pitch;
        if (j < y0 || j >= y0 + dh) { mzero(row, W * bpp); continue; }
        mzero(row, x0 * bpp); mzero(row + (x0 + dw) * bpp, (W - x0 - dw) * bpp);
        u32 jj = j - y0;
        const u8 *r0 = src + (4 * jj + 1) * sh / (4 * dh) * sw * bpp, *r1 = src + (4 * jj + 3) * sh / (4 * dh) * sw * bpp;
        u8 *o = row + x0 * bpp;
        for (u32 i = 0; i < dw; i++, o += bpp) {
            u32 a = vxs[2 * i] * bpp, b = vxs[2 * i + 1] * bpp;
            if (bpp == 2) {
                u32 p[4] = {*(const u16 *)(r0 + a), *(const u16 *)(r0 + b), *(const u16 *)(r1 + a), *(const u16 *)(r1 + b)}, r = 0, g = 0, bl = 0;
                for (int k = 0; k < 4; k++) {
                    if (f565) { r += p[k] >> 11; g += (p[k] >> 5) & 63; } else { r += (p[k] >> 10) & 31; g += (p[k] >> 5) & 31; }
                    bl += p[k] & 31;
                }
                *(u16 *)o = (u16)(f565 ? ((r >> 2) << 11) | ((g >> 2) << 5) | (bl >> 2) : ((r >> 2) << 10) | ((g >> 2) << 5) | (bl >> 2));
            } else
                for (u32 c = 0; c < bpp; c++) o[c] = (u8)((r0[a + c] + r0[b + c] + r1[a + c] + r1[b + c]) >> 2);
        }
    }
}

/* ---- city zoom (see NOTES.md "City zoom") ----------------------------------------------------------------------------------------------
 * The game draws the city through a "view" of cols x rows cells (60 x 15 px) set by 0x449e50.  Zoom gives the game a bigger / smaller view (cm_zdims),
 * lets it draw that into a private canvas, and scales the canvas into the real back buffer (cm_zoom).  k = canvas px per real px, 16.16. */
#define ZCAP_C 80                                                /* 50 % of 2560x1600 = 78 x 209 cells (the game itself draws at most 39 x 105; the draw / hit code is bounds checked by the 231 x 457 view grid) */
#define ZCAP_R 220
/* in: magnification pct (50..200, 100 = off) and the real view.  out: the view to give the game; returns k (16.16). */
u32 cm_zdims(u32 pct, u32 cols, u32 rows, u32 *oc, u32 *orr) {
    u32 k = (100u << 16) / pct, c = (cols * k + 0xffff) >> 16, r = ((rows - 1) * k + 0xffff) >> 16;
    if (rows < 2) r = 0;
    r += 1;
    if (c < 4) c = 4;
    if (r < 12) r = 12;
    *oc = c; *orr = r;
    return k;
}
/* the smallest magnification (most zoomed out) the view caps allow for this real view */
u32 cm_zmin(u32 cols, u32 rows) {
    u32 mc = cols > ZCAP_C ? cols : ZCAP_C, mr = rows > ZCAP_R ? rows : ZCAP_R, p = 50;
    while (p < 100) {                                            /* smallest pct whose canvas still fits */
        u32 c, r;
        cm_zdims(p, cols, rows, &c, &r);
        if (c <= mc && r <= mr) break;
        p++;
    }
    return p;
}
static int zfdiv(int a, int b) { return a >= 0 ? a / b : -((-a + b - 1) / b); }
/* camera shift in cells (dc cols, dr rows, rows even: the camera lattice is 60 x 30 px) that keeps the view point (ax, ay) - px from the view's
 * top-left - fixed when the magnification goes from pold to pnew (the draw is anchored top-left at the camera: world px = camera + k * screen px) */
void cm_zshift(u32 pold, u32 pnew, int ax, int ay, int *dc, int *dr) {
    int d = (int)((100u << 16) / pold) - (int)((100u << 16) / pnew);
    *dc = zfdiv((d * ax >> 16) + 30, 60);
    *dr = 2 * zfdiv((d * ay >> 16) + 15, 30);
}
static u16 zxi[4200]; static u8 zxw[4200];                       /* source column and 5 bit weight of every destination column */
static inline u32 zex(u32 p, u32 m) { return (p | p << 16) & m; }
static inline u32 zbl(u32 a, u32 b, u32 w, u32 m) { return ((a * (32 - w) + b * w) >> 5) & m; }
/* src: sw x sh 16 bit pixels, row pitch sp bytes, same y origin as dst.  dst: columns [0, x1), rows [y0, y1), pitch dp.  Bilinear at scale k. */
void cm_zoom(const u8 *src, u32 sp, u32 sw, u32 sh, u8 *dst, u32 dp, u32 x1, u32 y0, u32 y1, u32 k, u32 f565) {
    u32 m = f565 ? 0x07E0F81Fu : 0x03E07C1Fu;
    if (x1 > 4200 || sw < 2 || sh < 2 || y1 <= y0) return;
    for (u32 x = 0; x < x1; x++) {
        int pos = (int)(((2 * x + 1) * k >> 1) - 0x8000);                /* (2x+1) * k < 2^32 for k <= 2.0 */
        if (pos < 0) pos = 0;
        u32 i = (u32)pos >> 16, w = ((u32)pos >> 11) & 31;
        if (i >= sw - 1) { i = sw - 1; w = 0; }
        zxi[x] = (u16)i; zxw[x] = (u8)w;
    }
    for (u32 y = y0; y < y1; y++) {
        int pos = (int)(((2 * (y - y0) + 1) * k >> 1) - 0x8000);
        if (pos < 0) pos = 0;
        u32 j = (u32)pos >> 16, wy = ((u32)pos >> 11) & 31;
        if (j >= sh - y0 - 1) { j = sh - y0 - 1; wy = 0; }
        const u8 *r0 = src + (y0 + j) * sp, *r1 = j + 1 < sh - y0 ? r0 + sp : r0;
        u16 *o = (u16 *)(dst + y * dp);
        for (u32 x = 0; x < x1; x++) {
            u32 i = zxi[x], wx = zxw[x], i1 = i + 1 < sw ? i + 1 : i;
            u32 a = zbl(zex(((const u16 *)r0)[i], m), zex(((const u16 *)r0)[i1], m), wx, m);
            u32 b = zbl(zex(((const u16 *)r1)[i], m), zex(((const u16 *)r1)[i1], m), wx, m);
            u32 c = zbl(a, b, wy, m);
            o[x] = (u16)(c | c >> 16);
        }
    }
}
#ifndef HOST
/* the private canvas (one block, grown on demand; zero filled) */
u8 *cm_zbuf(u32 bytes) {
    static u8 *buf; static u32 cap;
    if (cap < bytes) { os_free(buf); buf = os_alloc(bytes); cap = buf ? bytes : 0; }
    return buf;
}
#endif

/* ---- city image export (see NOTES.md "Export City Image") ------------------------------------------------------------------------------
 * The whole playable area is rendered by the game's own city draw 0x44a830(cx, cy, p3), one screenful (tile) at a time with the camera stepped in whole lattice
 * cells (60 px x 30 px, rows even), the tiles of one row are collected in a strip, and the strip's finished rows are PNG-encoded and written (the process is 32 bit:
 * memory is only W x one tile height).  world x of a cell = 60 col - 30 (row & 1), world y = 15 row; a tile at camera (cx, cy) shows world x from 60 cx + left and
 * world y from 15 cy + 15 (left / top = the view's clip rectangle). */
#ifdef HOST
u8 *ex_bb; u32 ex_pitch, ex_layout = 2, ex_hot[4]; int ex_left, ex_right, ex_top, ex_bottom; void (*ex_draw)(int, int, int); char ex_dir[260];
#define EX_BB ex_bb
#define EX_PITCH ex_pitch
#define EX_LAYOUT ex_layout
#define EX_L ex_left
#define EX_R ex_right
#define EX_T ex_top
#define EX_B ex_bottom
#define EX_HOT(i) ex_hot[i]
static void ex_game_draw(int cx, int cy, int p3) { ex_draw(cx, cy, p3); }
static void *ex_open(const char *p) { return fopen(p, "wb"); }
static int ex_write(void *f, const void *b, u32 n) { return fwrite(b, 1, n, f) == n; }
static void ex_close(void *f) { fclose(f); }
static int ex_exists(const char *p) { FILE *f = fopen(p, "rb"); if (f) fclose(f); return f != 0; }
static void ex_dir_get(char *out) { strcpy(out, ex_dir); }
#define EX_SEP '/'
#else
#define EX_SEP '\\'
#define EX_BB (*(u8 **)0xf9c100)
#define EX_PITCH (*(u32 *)0xf2a698)
#define EX_LAYOUT (*(u32 *)0xf9c0e4)
#define EX_L (*(int *)0x7d3b94)
#define EX_R (*(int *)0x7d3b90)
#define EX_T (*(int *)0x7d3b8c)
#define EX_B (*(int *)0x7d3b88)
static const u32 hot_addr[4] = {0xfa05f0, 0xfa05f4, 0xda2f6c, 0xda2fa8};   /* overlay id, previous overlay, hover cell, build-ghost cell */
#define EX_HOT(i) (*(u32 *)hot_addr[i])
static void ex_game_draw(int cx, int cy, int p3) { ((void (*)(int, int, int))0x44a830)(cx, cy, p3); }
static void *ex_open(const char *p) {
    void *h = ((void *(W32 *)(const char *, u32, u32, void *, u32, u32, void *))(*(u32 *)0x5d80c4))(p, 0x40000000, 0, 0, 2, 0x80, 0);
    return h == (void *)-1 ? 0 : h;
}
static int ex_write(void *f, const void *b, u32 n) { u32 got = 0; return ((int (W32 *)(void *, const void *, u32, u32 *, void *))(*(u32 *)0x5d8084))(f, b, n, &got, 0) && got == n; }
static void ex_close(void *f) { ((int (W32 *)(void *))(*(u32 *)0x5d80bc))(f); }
static int ex_exists(const char *p) { return ((u32 (W32 *)(const char *))(*(u32 *)0x5d80e0))(p) != 0xffffffffu; }
static u32 ex_isdir(const char *p) { u32 a = ((u32 (W32 *)(const char *))(*(u32 *)0x5d80e0))(p); return a != 0xffffffffu && (a & 0x10); }
static void ex_cat(char *d, const char *s2) { while (*d) d++; while ((*d++ = *s2++)) ; }
/* the player's Desktop: Wine on a Mac -> Z:\<HOME>\Desktop (the bottle's own Desktop is not the Mac's); else the shell's Desktop folder; else "" (game folder) */
static void ex_dir_get(char *out) {
    char t[300]; u32 n;
    out[0] = 0;
    n = ((u32 (W32 *)(const char *, char *, u32))(*(u32 *)0x5d805c))("HOME", t, 200);
    if (n && n < 200) {
        out[0] = 'Z'; out[1] = ':'; out[2] = 0;
        for (u32 i = 0; i <= n; i++) t[i] = t[i] == '/' ? '\\' : t[i];
        ex_cat(out, t); ex_cat(out, "\\Desktop");
        if (ex_isdir(out)) return;
        out[0] = 0;
    }
    void *sh = ((void *(W32 *)(const char *))(*(u32 *)0x5d8128))("shell32.dll");
    if (sh) {
        void *fn = ((void *(W32 *)(void *, const char *))(*(u32 *)0x5d808c))(sh, "SHGetFolderPathA");
        if (fn && ((int (W32 *)(void *, int, void *, u32, char *))fn)(0, 0x10, 0, 0, t) == 0) { t[259] = 0; ex_cat(out, t); }
    }
}
#endif

static u32 ex_crc_tab[256], ex_crc_ready;
static u32 ex_crc(u32 c, const u8 *p, u32 n) {
    if (!ex_crc_ready) {
        for (u32 i = 0; i < 256; i++) { u32 v = i; for (int k = 0; k < 8; k++) v = v & 1 ? 0xedb88320u ^ (v >> 1) : v >> 1; ex_crc_tab[i] = v; }
        ex_crc_ready = 1;
    }
    for (c = ~c; n--; ) c = ex_crc_tab[(c ^ *p++) & 255] ^ (c >> 8);
    return ~c;
}
#define EX_RAW 131072
#define EX_Z 70000
static u8 ex_rawb[EX_RAW + 8], ex_zb[EX_Z + 8], ex_bit_n;
static u32 ex_zpos, ex_bits, ex_adl_a, ex_adl_b, ex_head[65536];
static void *ex_f; static int ex_err;
static void ex_be(u8 *d, u32 v) { d[0] = (u8)(v >> 24); d[1] = (u8)(v >> 16); d[2] = (u8)(v >> 8); d[3] = (u8)v; }
static void ex_chunk(const char *type, const u8 *data, u32 n) {
    u8 h[8], c[4]; u32 crc;
    ex_be(h, n); mcopy(h + 4, (const u8 *)type, 4);
    crc = ex_crc(0, h + 4, 4); if (n) crc = ex_crc(crc, data, n);
    ex_be(c, crc);
    if (!ex_write(ex_f, h, 8) || (n && !ex_write(ex_f, data, n)) || !ex_write(ex_f, c, 4)) ex_err = 5;
}
static void ex_zflush(void) { if (ex_zpos) { ex_chunk("IDAT", ex_zb, ex_zpos); ex_zpos = 0; } }
static void ex_zbyte(u32 b) { ex_zb[ex_zpos++] = (u8)b; if (ex_zpos >= EX_Z) ex_zflush(); }
static void ex_put(u32 v, u32 n) { ex_bits |= v << ex_bit_n; ex_bit_n += n; while (ex_bit_n >= 8) { ex_zbyte(ex_bits & 255); ex_bits >>= 8; ex_bit_n -= 8; } }
static void ex_huff(u32 code, u32 n) { u32 r = 0; for (u32 i = 0; i < n; i++) r |= ((code >> i) & 1) << (n - 1 - i); ex_put(r, n); }   /* Huffman codes go out MSB first */
static void ex_lit(u32 v) {
    if (v < 144) ex_huff(0x30 + v, 8); else if (v < 256) ex_huff(0x190 + v - 144, 9); else if (v < 280) ex_huff(v - 256, 7); else ex_huff(0xc0 + v - 280, 8);
}
/* one fixed-Huffman deflate block of src[0..n) (LZ77 inside the block, 32 K window) */
static void ex_block(const u8 *src, u32 n, u32 last) {
    ex_put(last, 1); ex_put(1, 2);
    mzero(ex_head, sizeof ex_head);
    for (u32 i = 0; i < n; ) {
        u32 len = 0, dist = 0;
        if (i + 4 <= n) {
            u32 h = (((u32)src[i] << 16 | (u32)src[i + 1] << 8 | src[i + 2]) * 2654435761u) >> 16, c = ex_head[h];
            ex_head[h] = i + 1;
            if (c && i - (c - 1) <= 32768 && src[c - 1] == src[i] && src[c] == src[i + 1] && src[c + 1] == src[i + 2] && src[c + 2] == src[i + 3]) {
                u32 m = n - i > 258 ? 258 : n - i; len = 4; dist = i - (c - 1);
                while (len < m && src[c - 1 + len] == src[i + len]) len++;
            }
        }
        if (len) {
            u32 k = 28; while (lbase[k] > len) k--;
            ex_lit(257 + k); if (lext[k]) ex_put(len - lbase[k], lext[k]);
            u32 d = 29; while (dbase[d] > dist) d--;
            ex_huff(d, 5); if (dext[d]) ex_put(dist - dbase[d], dext[d]);
            i += len;
        } else { ex_lit(src[i]); i++; }
    }
    ex_lit(256);
}
static u32 ex_rawpos;
static void ex_rawflush(u32 last) {
    ex_block(ex_rawb, ex_rawpos, last); ex_rawpos = 0;
}
/* one PNG scanline: filter byte 1 (Sub) + the row's RGB bytes, into the deflate stream; adler32 over the raw bytes */
static void ex_row(const u16 *px, u32 w, u32 f565) {
    if (ex_rawpos + 3 * w + 1 > EX_RAW) ex_rawflush(0);
    u8 *o = ex_rawb + ex_rawpos, prev[3] = {0, 0, 0}; u32 n = 3 * w + 1;
    *o++ = 1;
    for (u32 x = 0; x < w; x++) {
        u32 p = px[x], r, g, b;
        if (f565) { r = p >> 11; g = (p >> 5) & 63; b = p & 31; r = r << 3 | r >> 2; g = g << 2 | g >> 4; }
        else { r = (p >> 10) & 31; g = (p >> 5) & 31; b = p & 31; r = r << 3 | r >> 2; g = g << 3 | g >> 2; }
        b = b << 3 | b >> 2;
        *o++ = (u8)(r - prev[0]); *o++ = (u8)(g - prev[1]); *o++ = (u8)(b - prev[2]);
        prev[0] = (u8)r; prev[1] = (u8)g; prev[2] = (u8)b;
    }
    for (u32 i = 0; i < n; i++) { ex_adl_a += ex_rawb[ex_rawpos + i]; if (ex_adl_a >= 65521) ex_adl_a -= 65521; ex_adl_b += ex_adl_a; if (ex_adl_b >= 65521) ex_adl_b -= 65521; }
    ex_rawpos += n;
}
static int ex_floordiv(int a, int b) { return a >= 0 ? a / b : -((-a + b - 1) / b); }

/* error codes: 0 ok, 1 empty area, 2 view too small / image too big, 3 out of memory, 4 cannot create the file, 5 write error, 6 unsupported pixel layout.
 * a = {X0, Y0, W, H, cols, rows, p3}: the world rectangle to export (the region the player can scroll over: see ZExport), the real view, the map base.
 * name = file name without extension.  path_out receives the file's path (>= 320 bytes). */
int cm_export(const char *name, const int *a, char *path_out) {
    int X0 = a[0], Y0 = a[1], W = a[2], H = a[3], cols = a[4], rows = a[5], p3 = a[6];
    u32 f565 = EX_LAYOUT == 2;
    if (EX_LAYOUT != 2 && EX_LAYOUT != 0) return 6;
    if (W <= 0 || H <= 0) return 1;
    if (cols < 3 || rows < 6 || EX_R <= EX_L || EX_B <= EX_T) return 2;
    int left = EX_L, top = EX_T, tw = EX_R - EX_L, th = EX_B - EX_T;
    if (W > 40000 || H > 40000 || (u32)W * (u32)th * 2u > 120u * 1024 * 1024) return 2;
    int dc = cols - 1, dr = (rows - 2) & ~1, tstep = 15 * dr;
    if (dc < 1 || dr < 2 || tstep > th) return 2;
    /* path: <dir>\<name>.png, " (2)" .. " (99)" when it exists */
    char dir[300], p[330]; u32 i, k;
    ex_dir_get(dir);
    for (k = 1; k < 100; k++) {
        p[0] = 0;
        char *d = p; for (const char *q = dir; *q; ) *d++ = *q++;
        if (d != p) *d++ = EX_SEP;
        for (const char *q = name; *q; ) *d++ = *q++;
        if (k > 1) { *d++ = ' '; *d++ = '('; if (k >= 10) *d++ = (char)('0' + k / 10); *d++ = (char)('0' + k % 10); *d++ = ')'; }
        *d++ = '.'; *d++ = 'p'; *d++ = 'n'; *d++ = 'g'; *d = 0;
        if (!ex_exists(p)) break;
    }
    for (i = 0; p[i]; i++) path_out[i] = p[i];
    path_out[i] = 0;
    u16 *strip = os_alloc((u32)W * (u32)th * 2u);
    if (!strip) return 3;
    ex_f = ex_open(p);
    if (!ex_f) { os_free(strip); return 4; }
    ex_err = 0; ex_zpos = 0; ex_bits = 0; ex_bit_n = 0; ex_rawpos = 0; ex_adl_a = 1; ex_adl_b = 0;
    {   /* signature, IHDR, zlib header */
        static const u8 sig[8] = {0x89, 'P', 'N', 'G', 13, 10, 26, 10};
        u8 ih[13];
        if (!ex_write(ex_f, sig, 8)) ex_err = 5;
        ex_be(ih, (u32)W); ex_be(ih + 4, (u32)H); ih[8] = 8; ih[9] = 2; ih[10] = 0; ih[11] = 0; ih[12] = 0;
        ex_chunk("IHDR", ih, 13);
        ex_zbyte(0x78); ex_zbyte(0x01);
    }
    u32 hot[4];
    for (i = 0; i < 4; i++) { hot[i] = EX_HOT(i); EX_HOT(i) = 0; }       /* no overlay, no hover highlight, no build ghost in the picture */
    int cy = ex_floordiv(Y0 - 15, 15); cy -= cy & 1;                       /* even: the camera lattice */
    int emitted = Y0;                                                      /* next image row to write */
    while (emitted < Y0 + H && !ex_err) {
        int ytile = 15 * cy + 15, cx = ex_floordiv(X0 - left, 60);
        for (; 60 * cx + left < X0 + W; cx += dc) {
            u8 *bb = EX_BB; u32 pitch = EX_PITCH;
            for (int y = top; y < EX_B; y++) mzero(bb + (u32)y * pitch + (u32)left * 2, (u32)tw * 2);   /* cells outside the map are not drawn: start from black */
            ex_game_draw(cx, cy, p3);
            int wx = 60 * cx + left - X0;                                  /* strip column of the tile's first pixel */
            int x0 = wx < 0 ? -wx : 0, x1 = tw; if (wx + x1 > W) x1 = W - wx;
            for (int y = 0; y < th; y++) {
                const u16 *src = (const u16 *)(bb + (u32)(top + y) * pitch + (u32)left * 2);
                u16 *dst = strip + y * W + wx;                                /* (signed: wx is negative for the first tile column) */
                for (int x = x0; x < x1; x++) dst[x] = src[x];
            }
        }
        int upto = (cy + dr) * 15 + 15;                                    /* the next tile row starts here */
        if (upto > Y0 + H) upto = Y0 + H;
        for (int y = emitted; y < upto && y < Y0 + H; y++) { int sy = y - ytile; if (sy >= 0 && sy < th) ex_row(strip + (u32)sy * (u32)W, (u32)W, f565); else { ex_err = 2; break; } }
        if (upto > emitted) emitted = upto;
        cy += dr;
    }
    for (i = 0; i < 4; i++) EX_HOT(i) = hot[i];
    os_free(strip);
    if (!ex_err) {
        ex_rawflush(1);
        if (ex_bit_n) ex_zbyte(ex_bits & 255);
        { u8 ad[4]; ex_be(ad, ex_adl_b << 16 | ex_adl_a); for (i = 0; i < 4; i++) ex_zbyte(ad[i]); }
        ex_zflush();
        ex_chunk("IEND", 0, 0);
    }
    ex_close(ex_f);
    return ex_err;
}

#if !defined(HOST) && defined(CMLOG)
/* diagnostics: write rows of raw pixels to a file in the game folder */
static void dumpf(const char *name, const u8 *src, u32 rows, u32 rowbytes, u32 stride) {
    void *h = ((void *(W32 *)(const char *, u32, u32, void *, u32, u32, void *))(*(u32 *)0x5d80c4))(name, 0x40000000, 1, 0, 2, 0, 0);
    if (h == (void *)-1) return;
    u32 got;
    for (u32 r = 0; r < rows; r++) ((int (W32 *)(void *, const void *, u32, u32 *, void *))(*(u32 *)0x5d8084))(h, src + r * stride, rowbytes, &got, 0);
    ((int (W32 *)(void *))(*(u32 *)0x5d80bc))(h);
}
#endif

#ifndef HOST
/* hook "blit": replaces `call [BinkCopyToBuffer]` in the game's per-frame video draw (0x4179e0) for videos wider than 640.  dest = the window's
 * top-left in the locked surface, pitch = its row pitch, bpp = bytes per pixel, flags = BinkDDSurfaceType.  1 = drawn */
u32 cm_vblit(u32 *bink, u8 *dest, u32 pitch, u32 bpp, u32 flags) {
    static u8 *tmp; static u32 tcap;
    u32 w = bink[0], h = bink[1], fmt = flags & 0xffff, W = SCR_W, H = SCR_H;
    if (w < 64 || h < 64 || w > 8192 || h > 8192 || !W || !H || W > 4096 || H > 4096) return 0;
    if (!((bpp == 2 && (fmt == 9 || fmt == 10)) || ((bpp == 3 || bpp == 4) && fmt >= 1 && fmt <= 6))) return 0;
    u32 need = w * h * bpp;
    if (tcap < need) { os_free(tmp); tmp = os_alloc(need); tcap = tmp ? need : 0; }
    if (!tmp) return 0;
#ifdef CMLOG
    static u32 nv;
    if (nv == 200 || nv == 201) {                                       /* what is on the surface before our blit (nv 201: after the game drew over our frame 200) */
        LG("DSlp", *(u32 *)0x699914); LG("DSw ", *(u32 *)0x6998fc); LG("DSh ", *(u32 *)0x6998f8); LG("DSpi", *(u32 *)0x699900);
        LG("DWx ", *(u32 *)0xf2ab08); LG("DWy ", *(u32 *)0xf2ab0c); LG("DDst", (u32)dest); LG("DNV ", nv);
        dumpf(nv == 200 ? "Zeus_before200.raw" : "Zeus_before201.raw", dest, H, W * bpp, pitch);
    }
#endif
    int rc = ((int (W32 *)(void *, void *, u32, u32, u32, u32, u32))(*(u32 *)0x5d8238))(bink, tmp, w * bpp, h, 0, 0, flags | 0x80000000u);   /* BINKCOPYALL: the whole frame every time */
    (void)rc;
#ifdef CMLOG                                                      /* Zeus.cm.log: the first 6 frames, then every 48th */
    if (nv < 6 || nv % 48 == 0) {
        LG("VBw ", w); LG("VBh ", h); LG("VBbp", bpp); LG("VBfl", flags); LG("VBpi", pitch); LG("VBde", (u32)dest); LG("VBSW", W); LG("VBSH", H);
        LG("VBrc", (u32)rc); LG("VBtm", (u32)tmp);
        for (u32 q = 0; q < 4; q++) LG("VBpx", *(u32 *)(tmp + ((h * (1 + 2 * (q >> 1)) / 4) * w + w * (1 + 2 * (q & 1)) / 4) * bpp));   /* 4 samples: quarter points */
        u32 nz = 0;
        for (u32 gy = 0; gy < 16; gy++) for (u32 gx = 0; gx < 16; gx++) nz += *(u8 *)(tmp + ((h * gy / 16) * w + w * gx / 16) * bpp + 1) != 0;
        LG("VBnz", nz);                                                  /* of 256 grid samples: how many have a non-zero byte */
    }
#endif
    /* the game centres a video itself when it opens it (0x418799: x = (W - w) >> 1, y = (H - h) >> 1, unsigned, so for a video bigger than the
     * window these wrap to huge values) and passes dest = window origin + x * bpp + y * pitch: take that offset off again (mod 2^32) */
    dest = (u8 *)((u32)dest - ((W - w) >> 1) * bpp - ((H - h) >> 1) * pitch);
    vscale(tmp, w, h, dest, pitch, W, H, bpp, fmt == 10);
#ifdef CMLOG
    if (nv == 200) { dumpf("Zeus_after200.raw", dest, H, W * bpp, pitch); dumpf("Zeus_tmp200.raw", tmp, h, w * bpp, w * bpp); }
    nv++;
#endif
    return 1;
}
#endif

#ifdef HOST
/* test entry points */
void t_root(const char *r) { strncpy(host_root, r, sizeof host_root - 1); }
int t_png(u8 *file, u32 n, u32 *w, u32 *h, u32 *ch, u8 **out) {
    struct png p;
    if (png_open(file, n, &p)) return -1;
    u8 *buf = os_alloc(png_bufsize(&p));
    if (png_pixels(&p, buf)) return -2;
    *w = p.w; *h = p.h; *ch = p.ch; *out = buf; return 0;
}
void t_free(void *p) { os_free(p); }
void t_reset(void) {
    for (int i = 0; i < NSRC; i++) os_free(land[i].pix);
    for (int i = 0; i < NLAND; i++) os_free(comp[i].bgr);
    os_free(tile.pix);
    memset(land, 0, sizeof land); memset(comp, 0, sizeof comp); memset(&tile, 0, sizeof tile); memset(cm_stat, 0, sizeof cm_stat);
}
int t_map_index(const char *p) { return map_index(p); }
void t_vscale(const u8 *src, u32 sw, u32 sh, u8 *dst, u32 pitch, u32 W, u32 H, u32 bpp, u32 f565) { vscale(src, sw, sh, dst, pitch, W, H, bpp, f565); }
int t_img_match(const char *p, char *buf) { return img_match(p, buf) != 0; }
void t_cover(u32 sw, u32 sh, u32 W, u32 H, u32 *dw, u32 *dh) { cover(sw, sh, W, H, dw, dh); }
u32 t_comp(int i, u8 **bgr, u32 *w, u32 *h, int *key) { *bgr = comp[i].bgr; *w = comp[i].w; *h = comp[i].h; *key = comp[i].key; return cm_stat[2]; }
#endif
