/* Portable core of the patcher: SHA-256 and the ZD1 delta format (host + Windows build). */
#ifndef ZCORE_H
#define ZCORE_H
#include <stddef.h>
#include <stdint.h>

typedef struct { uint32_t h[8]; uint64_t n; uint8_t buf[64]; size_t fill; } zsha;
void zsha_init(zsha *s);
void zsha_add(zsha *s, const void *p, size_t n);
void zsha_hex(zsha *s, char out[65]);          /* finishes the hash */

/* ZD1: "ZD1\0", u32 out_size, then ops until out_size bytes exist:
   0 + u32 len + bytes = literal;  1 + u32 src_off + u32 len = copy from the source (all little endian). */
int zd_outsize(const uint8_t *d, size_t dn, uint32_t *outsize);   /* 0 = ok */
int zd_apply(const uint8_t *d, size_t dn, const uint8_t *src, size_t sn, uint8_t *out, size_t on); /* 0 = ok */
#endif
