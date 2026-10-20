/* Host tool for the tests: zcli sha FILE | zcli apply SRC DELTA OUT */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "zcore.h"

static uint8_t *slurp(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb"); uint8_t *b; long l;
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); l = ftell(f); fseek(f, 0, SEEK_SET);
    b = malloc(l ? l : 1);
    if (fread(b, 1, l, f) != (size_t)l) { fclose(f); free(b); return NULL; }
    fclose(f); *n = l; return b;
}

int main(int argc, char **argv) {
    size_t n, dn, sn; uint8_t *b, *d, *s, *o; uint32_t os; zsha h; char hex[65];
    if (argc == 3 && !strcmp(argv[1], "sha")) {
        if (!(b = slurp(argv[2], &n))) return 2;
        zsha_init(&h); zsha_add(&h, b, n); zsha_hex(&h, hex); puts(hex); return 0;
    }
    if (argc == 5 && !strcmp(argv[1], "apply")) {
        FILE *f;
        if (!(s = slurp(argv[2], &sn)) || !(d = slurp(argv[3], &dn))) return 2;
        if (zd_outsize(d, dn, &os)) return 3;
        o = malloc(os ? os : 1);
        if (zd_apply(d, dn, s, sn, o, os)) return 4;
        f = fopen(argv[4], "wb"); fwrite(o, 1, os, f); fclose(f); return 0;
    }
    return 1;
}
