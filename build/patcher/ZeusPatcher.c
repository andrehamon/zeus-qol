/* Zeus + Poseidon widescreen patch installer (Win32, 32-bit, no libc dependencies beyond msvcrt).
   Reads payload\manifest.txt next to the exe.  Install = check -> stage to *.zpt -> back up -> commit by rename -> verify;
   any failure after the backup rolls back through restore().  "/auto <folder> install|restore|check" runs without a window. */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shlobj.h>
#include <shellapi.h>
#include <commctrl.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include "zcore.h"

#define VERSION L"1.0"
#define BACKUP  L"ZeusPatch_backup"
#define MAXENT  64
#define IDC_PATH 101
#define IDC_BROWSE 102
#define IDC_STAT 103
#define IDC_PROG 104
#define IDC_INSTALL 105
#define IDC_RESTORE 106
#define IDC_CLOSE 107

typedef struct { int delta; wchar_t target[MAX_PATH], src[MAX_PATH]; char sin[65], sout[65]; uint64_t size; } Ent;
static Ent ents[MAXENT];
static int nent;
static wchar_t exedir[MAX_PATH], payload[MAX_PATH], game[MAX_PATH], err[1024], logpath[MAX_PATH];
static HWND hwnd, hpath, hstat, hprog, hinst, hrest, hclose;
static uint64_t total_work, done_work;
static volatile LONG busy;
static int g_ok, g_op;
static wchar_t g_msg[1024];
static uint8_t *iobuf;
#define IOBUF (4u << 20)

/* ---- small helpers ---- */
static void logw(const wchar_t *fmt, ...) {
    wchar_t w[1400]; char u[4200]; va_list ap; HANDLE h; DWORD n; SYSTEMTIME t; wchar_t line[1500];
    va_start(ap, fmt); _vsnwprintf(w, 1399, fmt, ap); va_end(ap); w[1399] = 0;
    GetLocalTime(&t);
    _snwprintf(line, 1499, L"%04d-%02d-%02d %02d:%02d:%02d  %ls\r\n", t.wYear, t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond, w);
    line[1499] = 0;
    n = WideCharToMultiByte(CP_UTF8, 0, line, -1, u, sizeof u, NULL, NULL);
    h = CreateFileW(logpath, FILE_APPEND_DATA, FILE_SHARE_READ, NULL, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h != INVALID_HANDLE_VALUE) { if (n > 1) WriteFile(h, u, n - 1, &n, NULL); CloseHandle(h); }
}
static void fail(const wchar_t *fmt, ...) {
    va_list ap; va_start(ap, fmt); _vsnwprintf(err, 1023, fmt, ap); va_end(ap); err[1023] = 0;
    logw(L"ERROR: %ls", err);
}
static void failw32(const wchar_t *what, const wchar_t *path) {
    DWORD e = GetLastError();
    const wchar_t *hint = e == ERROR_ACCESS_DENIED ? L" (access denied: close the game, or run the patcher as administrator)"
        : e == ERROR_SHARING_VIOLATION || e == ERROR_LOCK_VIOLATION ? L" (file in use: close the game)"
        : e == ERROR_DISK_FULL || e == ERROR_HANDLE_DISK_FULL ? L" (disk full)" : L"";
    fail(L"%ls\n%ls\nWindows error %lu%ls", what, path, (unsigned long)e, hint);
}
static void status(const wchar_t *s) { if (hstat) SetWindowTextW(hstat, s); logw(L"%ls", s); }
static void progress(uint64_t n) {
    done_work += n; if (done_work > total_work) done_work = total_work;
    if (hprog && total_work) PostMessageW(hprog, PBM_SETPOS, (WPARAM)(done_work * 1000 / total_work), 0);
}
static void pjoin(wchar_t *o, const wchar_t *a, const wchar_t *b) { _snwprintf(o, MAX_PATH - 1, L"%ls\\%ls", a, b); o[MAX_PATH - 1] = 0; }
static void tmpname(wchar_t *o, const wchar_t *p) { _snwprintf(o, MAX_PATH - 1, L"%ls.zpt", p); o[MAX_PATH - 1] = 0; }
static int exists(const wchar_t *p) { return GetFileAttributesW(p) != INVALID_FILE_ATTRIBUTES; }
static uint64_t fsize(const wchar_t *p) {
    WIN32_FILE_ATTRIBUTE_DATA d;
    return GetFileAttributesExW(p, GetFileExInfoStandard, &d) ? ((uint64_t)d.nFileSizeHigh << 32) | d.nFileSizeLow : 0;
}
static void mkparents(const wchar_t *p) {
    wchar_t b[MAX_PATH]; wchar_t *c;
    wcsncpy(b, p, MAX_PATH - 1); b[MAX_PATH - 1] = 0;
    for (c = b + 3; *c; c++) if (*c == L'\\') { *c = 0; CreateDirectoryW(b, NULL); *c = L'\\'; }
}
static int read_all(const wchar_t *p, uint8_t **buf, size_t *n) {
    HANDLE h = CreateFileW(p, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    LARGE_INTEGER sz; DWORD got; size_t off = 0;
    if (h == INVALID_HANDLE_VALUE) { failw32(L"Cannot open", p); return 1; }
    GetFileSizeEx(h, &sz);
    if (sz.QuadPart > 0x7fffffffLL || !(*buf = malloc(sz.QuadPart ? (size_t)sz.QuadPart : 1))) { CloseHandle(h); fail(L"Out of memory reading %ls", p); return 1; }
    while (off < (size_t)sz.QuadPart) {
        DWORD want = (DWORD)(sz.QuadPart - off > IOBUF ? IOBUF : sz.QuadPart - off);
        if (!ReadFile(h, *buf + off, want, &got, NULL) || !got) { failw32(L"Read error", p); CloseHandle(h); free(*buf); return 1; }
        off += got;
    }
    CloseHandle(h); *n = off; return 0;
}
static int write_all(const wchar_t *p, const uint8_t *b, size_t n) {
    HANDLE h = CreateFileW(p, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    size_t off = 0; DWORD put;
    if (h == INVALID_HANDLE_VALUE) { failw32(L"Cannot create", p); return 1; }
    while (off < n) {
        DWORD want = (DWORD)(n - off > IOBUF ? IOBUF : n - off);
        if (!WriteFile(h, b + off, want, &put, NULL) || !put) { failw32(L"Write error", p); CloseHandle(h); DeleteFileW(p); return 1; }
        off += put;
    }
    FlushFileBuffers(h); CloseHandle(h); return 0;
}
/* sha256 of a file; returns 0 and the hex in `hex`, 2 = file missing (no error text), 1 = other error */
static int hash_file(const wchar_t *p, char hex[65], int count) {
    HANDLE h = CreateFileW(p, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    zsha s; DWORD got;
    if (h == INVALID_HANDLE_VALUE) { if (GetLastError() == ERROR_FILE_NOT_FOUND || GetLastError() == ERROR_PATH_NOT_FOUND) return 2; failw32(L"Cannot open", p); return 1; }
    zsha_init(&s);
    for (;;) {
        if (!ReadFile(h, iobuf, IOBUF, &got, NULL)) { failw32(L"Read error", p); CloseHandle(h); return 1; }
        if (!got) break;
        zsha_add(&s, iobuf, got); if (count) progress(got);
    }
    CloseHandle(h); zsha_hex(&s, hex); return 0;
}
/* copy src -> dst while hashing; dst is removed and 1 returned when the hash differs from `want` */
static int copy_hash(const wchar_t *src, const wchar_t *dst, const char *want) {
    HANDLE a = CreateFileW(src, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL), b;
    zsha s; DWORD got, put; char hex[65];
    if (a == INVALID_HANDLE_VALUE) { failw32(L"Cannot open", src); return 1; }
    mkparents(dst);
    b = CreateFileW(dst, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (b == INVALID_HANDLE_VALUE) { failw32(L"Cannot create", dst); CloseHandle(a); return 1; }
    zsha_init(&s);
    for (;;) {
        if (!ReadFile(a, iobuf, IOBUF, &got, NULL)) { failw32(L"Read error", src); goto bad; }
        if (!got) break;
        zsha_add(&s, iobuf, got);
        if (!WriteFile(b, iobuf, got, &put, NULL) || put != got) { failw32(L"Write error", dst); goto bad; }
        progress(got);
    }
    FlushFileBuffers(b); CloseHandle(a); CloseHandle(b);
    zsha_hex(&s, hex);
    if (want && strcmp(hex, want)) { fail(L"Checksum mismatch (damaged or wrong file):\n%ls", src); DeleteFileW(dst); return 1; }
    return 0;
bad:
    CloseHandle(a); CloseHandle(b); DeleteFileW(dst); return 1;
}
static int move_over(const wchar_t *tmp, const wchar_t *dst) {
    SetFileAttributesW(dst, FILE_ATTRIBUTE_NORMAL);
    if (!MoveFileExW(tmp, dst, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) { failw32(L"Cannot replace", dst); return 1; }
    return 0;
}

/* ---- manifest ---- */
static void widen(wchar_t *o, const char *s) {
    MultiByteToWideChar(CP_UTF8, 0, s, -1, o, MAX_PATH);
    for (; *o; o++) if (*o == L'/') *o = L'\\';
}
static int load_manifest(void) {
    wchar_t p[MAX_PATH]; uint8_t *b; size_t n; char *line, *save; int ver = 0;
    pjoin(p, payload, L"manifest.txt");
    if (read_all(p, &b, &n)) { fail(L"The 'payload' folder next to the patcher is missing or incomplete.\nUnzip the whole release and keep ZeusPatcher.exe beside its 'payload' folder."); return 1; }
    b = realloc(b, n + 1); b[n] = 0;
    for (line = strtok_s((char *)b, "\r\n", &save); line; line = strtok_s(NULL, "\r\n", &save)) {
        char kind[16], t[MAX_PATH], s[MAX_PATH], a[80], c[80], d[40]; Ent *e = &ents[nent];
        if (!strncmp(line, "ZEUSPATCH ", 10)) { ver = 1; continue; }
        if (nent >= MAXENT) break;
        if (sscanf(line, "%15s", kind) != 1) continue;
        if (!strcmp(kind, "delta") && sscanf(line, "%*s %259s %259s %64s %64s %39s", t, s, a, c, d) == 5) {
            e->delta = 1; strcpy(e->sin, a); strcpy(e->sout, c);
        } else if (!strcmp(kind, "file") && sscanf(line, "%*s %259s %259s %64s %39s", t, s, c, d) == 4) {
            e->delta = 0; strcpy(e->sout, c);
        } else continue;
        widen(e->target, t); widen(e->src, s); e->size = (uint64_t)_strtoui64(d, NULL, 10); nent++;
    }
    free(b);
    if (!ver || nent < 2 || !ents[0].delta) { fail(L"payload\\manifest.txt is not valid."); return 1; }
    return 0;
}

/* ---- jobs ---- */
enum { ST_FRESH, ST_INSTALLED, ST_BAD };
static int check_state(int count) {
    wchar_t p[MAX_PATH]; char hex[65]; int i, r;
    pjoin(p, game, ents[0].target);
    r = hash_file(p, hex, count);
    if (r == 2) { fail(L"Zeus.exe was not found in this folder:\n%ls\nChoose the folder that contains Zeus.exe (your Zeus + Poseidon install).", game); return ST_BAD; }
    if (r) return ST_BAD;
    if (!strcmp(hex, ents[0].sout)) return ST_INSTALLED;
    if (strcmp(hex, ents[0].sin)) {
        fail(L"This Zeus.exe is not the original GOG version 2.1.4.0 (checksum %.12hs...).\nThis patch supports only that version. If you installed another patch, restore your original Zeus.exe first.", hex);
        return ST_BAD;
    }
    for (i = 1; i < nent; i++) {
        if (!ents[i].delta) continue;
        pjoin(p, game, ents[i].target);
        r = hash_file(p, hex, count);
        if (r == 2) { fail(L"Missing game file:\n%ls", p); return ST_BAD; }
        if (r) return ST_BAD;
        if (strcmp(hex, ents[i].sin)) { fail(L"This game file is not the original GOG file (already modified?):\n%ls", p); return ST_BAD; }
    }
    return ST_FRESH;
}
static void backup_path(wchar_t *o, int i) { wchar_t b[MAX_PATH]; pjoin(b, game, BACKUP); pjoin(o, b, ents[i].target); }

static void drop_temps(void) {
    int i; wchar_t p[MAX_PATH], t[MAX_PATH];
    for (i = 0; i < nent; i++) { pjoin(p, game, ents[i].target); tmpname(t, p); DeleteFileW(t); }
}

static int do_restore(void) {
    int i; wchar_t p[MAX_PATH], b[MAX_PATH], t[MAX_PATH], d[MAX_PATH]; char hex[65];
    status(L"Checking the backup...");
    for (i = 0; i < nent; i++) {
        if (!ents[i].delta) continue;
        backup_path(b, i);
        int r = hash_file(b, hex, 0);
        if (r == 2) { fail(L"No backup found (%ls\\%ls).\nThe patch cannot be undone automatically; copy your original files back by hand.", BACKUP, ents[i].target); return 1; }
        if (r) return 1;
        if (strcmp(hex, ents[i].sin)) { fail(L"The backup file was changed and is not the original any more:\n%ls", b); return 1; }
    }
    status(L"Restoring the original files...");
    for (i = 0; i < nent; i++) {
        if (!ents[i].delta) continue;
        backup_path(b, i); pjoin(p, game, ents[i].target); tmpname(t, p);
        if (copy_hash(b, t, ents[i].sin)) { drop_temps(); return 1; }
    }
    for (i = nent - 1; i >= 0; i--) {                    /* exe (entry 0) last */
        if (!ents[i].delta) continue;
        pjoin(p, game, ents[i].target); tmpname(t, p);
        if (move_over(t, p)) { drop_temps(); return 1; }
    }
    for (i = 0; i < nent; i++) {
        if (ents[i].delta) continue;
        pjoin(p, game, ents[i].target); backup_path(b, i);
        if (exists(b)) {                                   /* a file the player already had: put it back */
            tmpname(t, p);
            if (copy_hash(b, t, NULL) || move_over(t, p)) { DeleteFileW(t); return 1; }
            continue;
        }
        SetFileAttributesW(p, FILE_ATTRIBUTE_NORMAL); DeleteFileW(p);
        wcscpy(d, p); wchar_t *c = wcsrchr(d, L'\\'); if (c) { *c = 0; RemoveDirectoryW(d); }   /* only if now empty */
    }
    for (i = 0; i < nent; i++) { backup_path(b, i); SetFileAttributesW(b, FILE_ATTRIBUTE_NORMAL); DeleteFileW(b); }
    for (int pass = 0; pass < 2; pass++)                   /* sub-folders of the backup, then the backup folder */
        for (i = 0; i < nent; i++) { wchar_t q[MAX_PATH]; backup_path(q, i); wchar_t *c = wcsrchr(q, L'\\'); if (c) { *c = 0; RemoveDirectoryW(q); } }
    pjoin(b, game, BACKUP);
    RemoveDirectoryW(b);
    return 0;
}

static int do_install(void) {
    int i, st; wchar_t p[MAX_PATH], t[MAX_PATH], b[MAX_PATH], pl[MAX_PATH]; uint64_t need = 64u << 20, sum = 0; ULARGE_INTEGER freeb;
    status(L"Checking your game files...");
    st = check_state(1);
    if (st == ST_BAD) return 1;
    if (st == ST_INSTALLED) { fail(L"The patch is already installed in this folder.\nRun the patcher again and choose \"Reinstall\" to patch again."); return 1; }
    for (i = 0; i < nent; i++) {
        pjoin(p, game, ents[i].target);
        sum += ents[i].size; need += ents[i].size + (ents[i].delta ? 2 * ents[i].size : fsize(p));
    }
    total_work = 2 * sum + (64u << 20); done_work = 0;
    if (GetDiskFreeSpaceExW(game, &freeb, NULL, NULL) && freeb.QuadPart < need) {
        fail(L"Not enough free disk space: about %.0f MB are needed, %.0f MB are free.", (double)need / 1048576.0, (double)freeb.QuadPart / 1048576.0); return 1;
    }
    status(L"Preparing the new files (nothing is changed yet)...");
    for (i = 0; i < nent; i++) {
        pjoin(p, game, ents[i].target); tmpname(t, p); pjoin(pl, payload, ents[i].src); mkparents(t);
        if (ents[i].delta) {
            uint8_t *src, *dl, *out; size_t sn, dn; uint32_t on; zsha s; char hex[65];
            if (read_all(p, &src, &sn)) goto stage_fail;
            if (read_all(pl, &dl, &dn)) { free(src); goto stage_fail; }
            if (zd_outsize(dl, dn, &on) || !(out = malloc(on ? on : 1))) { free(src); free(dl); fail(L"Patch data is damaged:\n%ls", pl); goto stage_fail; }
            if (zd_apply(dl, dn, src, sn, out, on)) { free(src); free(dl); free(out); fail(L"Patch data does not fit your file:\n%ls", p); goto stage_fail; }
            free(src); free(dl);
            zsha_init(&s); zsha_add(&s, out, on); zsha_hex(&s, hex);
            if (strcmp(hex, ents[i].sout)) { free(out); fail(L"Internal check failed for %ls (wrong result).", ents[i].target); goto stage_fail; }
            if (write_all(t, out, on)) { free(out); goto stage_fail; }
            free(out); progress(ents[i].size);
        } else if (copy_hash(pl, t, ents[i].sout)) goto stage_fail;
    }
    status(L"Backing up your original files...");
    for (i = 0; i < nent; i++) {
        pjoin(p, game, ents[i].target); backup_path(b, i);
        if (ents[i].delta) { if (copy_hash(p, b, ents[i].sin)) goto stage_fail; }
        else {
            DeleteFileW(b);                                /* never keep a stale backup of a file that is not there now */
            if (exists(p) && copy_hash(p, b, NULL)) goto stage_fail;
        }
    }
    status(L"Installing...");
    for (i = nent - 1; i >= 0; i--) {                    /* exe (entry 0) last */
        pjoin(p, game, ents[i].target); tmpname(t, p);
        if (move_over(t, p)) { wchar_t keep[1024]; wcscpy(keep, err); drop_temps(); do_restore(); wcscpy(err, keep); return 1; }
    }
    status(L"Verifying the installed files...");
    for (i = 0; i < nent; i++) {
        char hex[65] = ""; int r; wchar_t keep[1024];
        pjoin(p, game, ents[i].target);
        r = hash_file(p, hex, 1);
        if (r || strcmp(hex, ents[i].sout)) {
            if (r) wcscpy(keep, err); else _snwprintf(keep, 1023, L"Verification failed for %ls.", ents[i].target);
            keep[1023] = 0; logw(L"ERROR: %ls (rolling back)", keep); do_restore(); wcscpy(err, keep); return 1;
        }
    }
    return 0;
stage_fail:
    drop_temps(); return 1;
}

static DWORD WINAPI job(LPVOID arg) {
    (void)arg;
    g_ok = (g_op == 1 ? do_install() : g_op == 2 ? do_restore() : g_op == 3 ? (do_restore() || do_install()) : 0) == 0;
    if (g_ok) _snwprintf(g_msg, 1023, g_op != 2 ? L"Done. The widescreen patch is installed.\n\nStart the game as usual. Options > Display options picks the resolution.\nYour original files are backed up in the folder '%ls' (use \"Remove patch\" to undo)."
                                       : L"Done. Your original game files are back.", BACKUP);
    else wcscpy(g_msg, err);
    g_msg[1023] = 0;
    logw(L"%ls: %ls", g_ok ? L"OK" : L"FAILED", g_msg);
    if (hwnd) PostMessageW(hwnd, WM_APP, 0, 0);
    return 0;
}

/* ---- window ---- */
static void getgame(void) {
    GetWindowTextW(hpath, game, MAX_PATH);
    size_t n = wcslen(game);
    while (n > 3 && (game[n - 1] == L'\\' || game[n - 1] == L'/' || game[n - 1] == L' ')) game[--n] = 0;
}
static void refresh(void) {
    int st; wchar_t m[1100];
    getgame(); total_work = 0; done_work = 0; SendMessageW(hprog, PBM_SETPOS, 0, 0);
    EnableWindow(hinst, FALSE); EnableWindow(hrest, FALSE);
    if (!game[0]) { SetWindowTextW(hstat, L"Choose your Zeus + Poseidon game folder."); return; }
    SetCursor(LoadCursorW(NULL, (LPCWSTR)IDC_WAIT));
    st = check_state(0);
    SetCursor(LoadCursorW(NULL, (LPCWSTR)IDC_ARROW));
    SetWindowTextW(hinst, L"Install");
    if (st == ST_FRESH) { SetWindowTextW(hstat, L"Original GOG version 2.1.4.0 found. Ready to install."); EnableWindow(hinst, TRUE); }
    else if (st == ST_INSTALLED) {
        wchar_t b[MAX_PATH]; backup_path(b, 0);
        if (exists(b)) {
            EnableWindow(hrest, TRUE); EnableWindow(hinst, TRUE); SetWindowTextW(hinst, L"Reinstall");
            SetWindowTextW(hstat, L"The widescreen patch is already installed.\nReinstall = put the backed-up originals back, then patch again.  Remove patch = put the originals back.");
        } else SetWindowTextW(hstat, L"The patched Zeus.exe is installed, but no backup was found, so it cannot be undone from here.");
    } else { _snwprintf(m, 1099, L"%ls", err); m[1099] = 0; SetWindowTextW(hstat, m); }
}
static void browse(void) {
    BROWSEINFOW bi; LPITEMIDLIST l; wchar_t p[MAX_PATH];
    memset(&bi, 0, sizeof bi); bi.hwndOwner = hwnd; bi.lpszTitle = L"Select the folder that contains Zeus.exe"; bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    if ((l = SHBrowseForFolderW(&bi)) && SHGetPathFromIDListW(l, p)) { SetWindowTextW(hpath, p); refresh(); }
    if (l) CoTaskMemFree(l);
}
static void start(int op) {
    wchar_t q[MAX_PATH + 300];
    getgame();
    if (op == 1) _snwprintf(q, MAX_PATH + 299, L"Install the widescreen patch into:\n%ls\n\nYour original files are backed up first. Continue?", game);
    else if (op == 3) _snwprintf(q, MAX_PATH + 299, L"Reinstall the widescreen patch in:\n%ls\n\nThe backed-up original files are put back, then the patch is applied again. Continue?", game);
    else _snwprintf(q, MAX_PATH + 299, L"Restore the original game files in:\n%ls\n\nThis removes the patch. Continue?", game);
    q[MAX_PATH + 299] = 0;
    if (MessageBoxW(hwnd, q, L"Zeus + Poseidon Widescreen Patch", MB_YESNO | MB_ICONQUESTION) != IDYES) return;
    EnableWindow(hinst, FALSE); EnableWindow(hrest, FALSE); EnableWindow(hpath, FALSE); EnableWindow(GetDlgItem(hwnd, IDC_BROWSE), FALSE);
    busy = 1; g_op = op; total_work = op == 2 ? 1 : 0; done_work = 0;
    SendMessageW(hprog, PBM_SETPOS, 0, 0);
    CloseHandle(CreateThread(NULL, 0, job, NULL, 0, NULL));
}
static LRESULT CALLBACK wproc(HWND h, UINT m, WPARAM w, LPARAM l) {
    switch (m) {
    case WM_COMMAND:
        switch (LOWORD(w)) {
        case IDC_BROWSE: if (!busy) browse(); break;
        case IDC_INSTALL: if (!busy) { wchar_t bt[32]; GetWindowTextW(hinst, bt, 32); start(wcscmp(bt, L"Reinstall") ? 1 : 3); } break;
        case IDC_RESTORE: if (!busy) start(2); break;
        case IDC_CLOSE: if (!busy) DestroyWindow(h); break;
        case IDC_PATH: if (HIWORD(w) == EN_KILLFOCUS && !busy) refresh(); break;
        }
        return 0;
    case WM_APP:
        busy = 0; EnableWindow(hpath, TRUE); EnableWindow(GetDlgItem(h, IDC_BROWSE), TRUE);
        SendMessageW(hprog, PBM_SETPOS, g_ok ? 1000 : 0, 0);
        MessageBoxW(h, g_msg, L"Zeus + Poseidon Widescreen Patch", g_ok ? MB_OK | MB_ICONINFORMATION : MB_OK | MB_ICONERROR);
        refresh();
        return 0;
    case WM_CLOSE: if (!busy) DestroyWindow(h); return 0;
    case WM_DESTROY: PostQuitMessage(0); return 0;
    }
    return DefWindowProcW(h, m, w, l);
}
static HWND ctl(const wchar_t *cls, const wchar_t *txt, DWORD style, int x, int y, int w, int h, int id) {
    HWND c = CreateWindowExW(cls[0] == L'E' ? WS_EX_CLIENTEDGE : 0, cls, txt, WS_CHILD | WS_VISIBLE | style, x, y, w, h, hwnd, (HMENU)(INT_PTR)id, GetModuleHandleW(NULL), NULL);
    SendMessageW(c, WM_SETFONT, (WPARAM)GetStockObject(DEFAULT_GUI_FONT), TRUE);
    return c;
}
static void defaultgame(void) {
    static const wchar_t *c[] = { L"C:\\GOG Games\\Zeus + Poseidon", L"C:\\Program Files (x86)\\GOG Galaxy\\Games\\Zeus + Poseidon", L"C:\\GOG Games\\Zeus", L"C:\\Program Files (x86)\\GOG.com\\Zeus + Poseidon" };
    wchar_t p[MAX_PATH]; int i;
    pjoin(p, exedir, L"Zeus.exe"); if (exists(p)) { SetWindowTextW(hpath, exedir); return; }
    for (i = 0; i < 4; i++) { pjoin(p, c[i], L"Zeus.exe"); if (exists(p)) { SetWindowTextW(hpath, c[i]); return; } }
}

int WINAPI wWinMain(HINSTANCE hi, HINSTANCE hp, LPWSTR cmd, int show) {
    wchar_t *c; WNDCLASSW wc; MSG msg; int argc, auto_ = 0; wchar_t **argv; int rc = 0;
    (void)hp; (void)cmd;
    GetModuleFileNameW(NULL, exedir, MAX_PATH); c = wcsrchr(exedir, L'\\'); if (c) *c = 0;
    pjoin(payload, exedir, L"payload"); pjoin(logpath, exedir, L"ZeusPatcher.log");
    iobuf = malloc(IOBUF);
    argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argc >= 4 && !wcscmp(argv[1], L"/auto")) auto_ = 1;
    logw(L"ZeusPatcher %ls started%ls", VERSION, auto_ ? L" (auto)" : L"");
    if (load_manifest()) {
        if (auto_) return 2;
        MessageBoxW(NULL, err, L"Zeus + Poseidon Widescreen Patch", MB_OK | MB_ICONERROR); return 2;
    }
    if (auto_) {
        wcsncpy(game, argv[2], MAX_PATH - 1);
        if (!wcscmp(argv[3], L"check")) rc = check_state(0) == ST_BAD ? 1 : 0;
        else if (!wcscmp(argv[3], L"install")) { g_op = 1; job(NULL); rc = !g_ok; }
        else if (!wcscmp(argv[3], L"restore")) { g_op = 2; job(NULL); rc = !g_ok; }
        else if (!wcscmp(argv[3], L"reinstall")) { g_op = 3; job(NULL); rc = !g_ok; }
        else rc = 3;
        logw(L"exit code %d", rc);
        return rc;
    }
    OleInitialize(NULL); InitCommonControls();
    memset(&wc, 0, sizeof wc); wc.lpfnWndProc = wproc; wc.hInstance = hi; wc.hCursor = LoadCursorW(NULL, (LPCWSTR)IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1); wc.lpszClassName = L"ZeusPatcherWnd"; wc.hIcon = LoadIconW(NULL, (LPCWSTR)IDI_APPLICATION);
    RegisterClassW(&wc);
    hwnd = CreateWindowExW(0, wc.lpszClassName, L"Zeus + Poseidon Widescreen Patch " VERSION, WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
                           CW_USEDEFAULT, CW_USEDEFAULT, 620, 300, NULL, NULL, hi, NULL);
    ctl(L"STATIC", L"Zeus + Poseidon Widescreen Patch " VERSION, SS_LEFT, 16, 14, 580, 20, 0);
    ctl(L"STATIC", L"Game folder (the folder that contains Zeus.exe):", SS_LEFT, 16, 44, 580, 18, 0);
    hpath = ctl(L"EDIT", L"", ES_AUTOHSCROLL, 16, 64, 480, 24, IDC_PATH);
    ctl(L"BUTTON", L"Browse...", BS_PUSHBUTTON | WS_TABSTOP, 506, 63, 90, 26, IDC_BROWSE);
    hstat = ctl(L"STATIC", L"", SS_LEFT, 16, 102, 580, 56, IDC_STAT);
    hprog = CreateWindowExW(0, PROGRESS_CLASSW, NULL, WS_CHILD | WS_VISIBLE, 16, 168, 580, 18, hwnd, (HMENU)IDC_PROG, hi, NULL);
    SendMessageW(hprog, PBM_SETRANGE32, 0, 1000);
    hinst = ctl(L"BUTTON", L"Install", BS_DEFPUSHBUTTON | WS_TABSTOP, 16, 206, 120, 30, IDC_INSTALL);
    hrest = ctl(L"BUTTON", L"Remove patch", BS_PUSHBUTTON | WS_TABSTOP, 146, 206, 130, 30, IDC_RESTORE);
    hclose = ctl(L"BUTTON", L"Close", BS_PUSHBUTTON | WS_TABSTOP, 476, 206, 120, 30, IDC_CLOSE);
    defaultgame();
    if (argc == 2) SetWindowTextW(hpath, argv[1]);          /* folder given on the command line / dropped on the exe */
    refresh();
    ShowWindow(hwnd, show); UpdateWindow(hwnd);
    while (GetMessageW(&msg, NULL, 0, 0) > 0) { if (!IsDialogMessageW(hwnd, &msg)) { TranslateMessage(&msg); DispatchMessageW(&msg); } }
    return 0;
}
