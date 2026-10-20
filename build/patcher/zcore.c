#include <string.h>
#include "zcore.h"

static const uint32_t K[64] = {
0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
#define ROR(x,n) (((x) >> (n)) | ((x) << (32 - (n))))

static void block(zsha *s, const uint8_t *p) {
    uint32_t w[64], a, b, c, d, e, f, g, h, t1, t2;
    int i;
    for (i = 0; i < 16; i++) w[i] = (uint32_t)p[4*i] << 24 | p[4*i+1] << 16 | p[4*i+2] << 8 | p[4*i+3];
    for (i = 16; i < 64; i++) {
        uint32_t s0 = ROR(w[i-15],7) ^ ROR(w[i-15],18) ^ (w[i-15] >> 3);
        uint32_t s1 = ROR(w[i-2],17) ^ ROR(w[i-2],19) ^ (w[i-2] >> 10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    a = s->h[0]; b = s->h[1]; c = s->h[2]; d = s->h[3]; e = s->h[4]; f = s->h[5]; g = s->h[6]; h = s->h[7];
    for (i = 0; i < 64; i++) {
        t1 = h + (ROR(e,6) ^ ROR(e,11) ^ ROR(e,25)) + ((e & f) ^ (~e & g)) + K[i] + w[i];
        t2 = (ROR(a,2) ^ ROR(a,13) ^ ROR(a,22)) + ((a & b) ^ (a & c) ^ (b & c));
        h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d; s->h[4] += e; s->h[5] += f; s->h[6] += g; s->h[7] += h;
}

void zsha_init(zsha *s) {
    static const uint32_t i0[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    memcpy(s->h, i0, sizeof i0); s->n = 0; s->fill = 0;
}

void zsha_add(zsha *s, const void *v, size_t n) {
    const uint8_t *p = v;
    s->n += n;
    if (s->fill) {
        size_t k = 64 - s->fill; if (k > n) k = n;
        memcpy(s->buf + s->fill, p, k); s->fill += k; p += k; n -= k;
        if (s->fill < 64) return;
        block(s, s->buf); s->fill = 0;
    }
    for (; n >= 64; p += 64, n -= 64) block(s, p);
    memcpy(s->buf, p, n); s->fill = n;
}

void zsha_hex(zsha *s, char out[65]) {
    uint64_t bits = s->n * 8; uint8_t pad[72]; size_t k = (s->fill < 56 ? 56 : 120) - s->fill; int i;
    memset(pad, 0, sizeof pad); pad[0] = 0x80;
    for (i = 0; i < 8; i++) pad[k + i] = (uint8_t)(bits >> (56 - 8 * i));
    zsha_add(s, pad, k + 8);
    for (i = 0; i < 8; i++) { static const char x[] = "0123456789abcdef"; int j;
        for (j = 0; j < 4; j++) { uint8_t by = (uint8_t)(s->h[i] >> (24 - 8 * j));
            out[8*i + 2*j] = x[by >> 4]; out[8*i + 2*j + 1] = x[by & 15]; } }
    out[64] = 0;
}

static uint32_t rd32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

int zd_outsize(const uint8_t *d, size_t dn, uint32_t *outsize) {
    if (dn < 8 || memcmp(d, "ZD1", 4)) return 1;
    *outsize = rd32(d + 4); return 0;
}

int zd_apply(const uint8_t *d, size_t dn, const uint8_t *src, size_t sn, uint8_t *out, size_t on) {
    size_t p = 8, o = 0;
    uint32_t os;
    if (zd_outsize(d, dn, &os) || os != on) return 1;
    while (o < on) {
        uint32_t a, n;
        if (p >= dn) return 2;
        if (d[p] == 0) {
            if (p + 5 > dn) return 2;
            n = rd32(d + p + 1); p += 5;
            if (n > dn - p || n > on - o) return 3;
            memcpy(out + o, d + p, n); p += n;
        } else if (d[p] == 1) {
            if (p + 9 > dn) return 2;
            a = rd32(d + p + 1); n = rd32(d + p + 5); p += 9;
            if (a > sn || n > sn - a || n > on - o) return 3;
            memcpy(out + o, src + a, n);
        } else return 4;
        o += n;
    }
    return p == dn ? 0 : 5;
}
