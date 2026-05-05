/*
 * string.c — Minimal C string / memory functions.
 * These are the functions CPython actually calls; nothing more.
 */

#include "include/libc.h"
#include <stdint.h>
#include <stddef.h>
#include <wchar.h>

void *memset(void *dst, int c, size_t n) {
    unsigned char *d = dst;
    while (n--) *d++ = (unsigned char)c;
    return dst;
}

void *memcpy(void *dst, const void *src, size_t n) {
    unsigned char *d = dst;
    const unsigned char *s = src;
    while (n--) *d++ = *s++;
    return dst;
}

void *memmove(void *dst, const void *src, size_t n) {
    unsigned char *d = dst;
    const unsigned char *s = src;
    if (d < s) {
        while (n--) *d++ = *s++;
    } else {
        d += n; s += n;
        while (n--) *--d = *--s;
    }
    return dst;
}

int memcmp(const void *a, const void *b, size_t n) {
    const unsigned char *p = a, *q = b;
    while (n--) {
        if (*p != *q) return (int)*p - (int)*q;
        p++; q++;
    }
    return 0;
}

void *memchr(const void *s, int c, size_t n) {
    const unsigned char *p = s;
    while (n--) { if (*p == (unsigned char)c) return (void *)p; p++; }
    return NULL;
}

size_t strlen(const char *s) {
    const char *p = s;
    while (*p) p++;
    return p - s;
}

size_t strnlen(const char *s, size_t maxlen) {
    size_t n = 0;
    while (n < maxlen && s[n]) n++;
    return n;
}

int strcmp(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}

int strncmp(const char *a, const char *b, size_t n) {
    while (n) {
        unsigned char ca = (unsigned char)*a;
        unsigned char cb = (unsigned char)*b;
        if (ca != cb) return (int)ca - (int)cb;
        if (ca == 0) return 0;
        a++; b++; n--;
    }
    return 0;
}

char *strcpy(char *dst, const char *src) {
    char *d = dst;
    while ((*d++ = *src++));
    return dst;
}

char *strncpy(char *dst, const char *src, size_t n) {
    char *d = dst;
    while (n && (*d++ = *src++)) n--;
    while (n--) *d++ = '\0';
    return dst;
}

char *strcat(char *dst, const char *src) {
    char *d = dst + strlen(dst);
    while ((*d++ = *src++));
    return dst;
}

char *strncat(char *dst, const char *src, size_t n) {
    char *d = dst + strlen(dst);
    while (n-- && *src) *d++ = *src++;
    *d = '\0';
    return dst;
}

char *strchr(const char *s, int c) {
    while (*s) { if (*s == (char)c) return (char *)s; s++; }
    return (c == '\0') ? (char *)s : NULL;
}

char *strrchr(const char *s, int c) {
    const char *last = NULL;
    while (*s) { if (*s == (char)c) last = s; s++; }
    return (*s == (char)c) ? (char *)s : (char *)last;
}

char *strstr(const char *hay, const char *needle) {
    size_t nlen = strlen(needle);
    if (!nlen) return (char *)hay;
    while (*hay) {
        if (strncmp(hay, needle, nlen) == 0) return (char *)hay;
        hay++;
    }
    return NULL;
}

char *strdup(const char *s) {
    size_t n = strlen(s) + 1;
    char *d = malloc(n);
    if (d) memcpy(d, s, n);
    return d;
}

char *strndup(const char *s, size_t n) {
    size_t len = strnlen(s, n);
    char *d = malloc(len + 1);
    if (d) { memcpy(d, s, len); d[len] = '\0'; }
    return d;
}

int strcasecmp(const char *a, const char *b) {
    while (*a && (*a | 32) == (*b | 32)) { a++; b++; }
    return (unsigned char)(*a | 32) - (unsigned char)(*b | 32);
}

int strncasecmp(const char *a, const char *b, size_t n) {
    while (n-- && *a && (*a | 32) == (*b | 32)) { a++; b++; }
    if (!n) return 0;
    return (unsigned char)(*a | 32) - (unsigned char)(*b | 32);
}

// ── Character classification ──────────────────────────────────────────────────

int isalpha(int c)  { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); }
int isdigit(int c)  { return c >= '0' && c <= '9'; }
int isalnum(int c)  { return isalpha(c) || isdigit(c); }
int isspace(int c)  { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v'; }
int isupper(int c)  { return c >= 'A' && c <= 'Z'; }
int islower(int c)  { return c >= 'a' && c <= 'z'; }
int isprint(int c)  { return c >= 0x20 && c < 0x7F; }
int ispunct(int c)  { return isprint(c) && !isalnum(c) && c != ' '; }
int isxdigit(int c) { return isdigit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'); }
int iscntrl(int c)  { return (unsigned)c < 0x20 || c == 0x7F; }
int isblank(int c)  { return c == ' ' || c == '\t'; }
int toupper(int c)  { return islower(c) ? c - 32 : c; }
int tolower(int c)  { return isupper(c) ? c + 32 : c; }

// ── Number conversion ─────────────────────────────────────────────────────────

long strtol(const char *s, char **end, int base) {
    while (isspace(*s)) s++;
    int neg = 0;
    if (*s == '-') { neg = 1; s++; } else if (*s == '+') s++;
    if (base == 0) {
        if (*s == '0' && (s[1] == 'x' || s[1] == 'X')) { base = 16; s += 2; }
        else if (*s == '0') { base = 8; s++; }
        else base = 10;
    } else if (base == 16 && *s == '0' && (s[1] == 'x' || s[1] == 'X')) {
        s += 2;
    }
    long val = 0;
    while (*s) {
        int d;
        if (*s >= '0' && *s <= '9') d = *s - '0';
        else if (*s >= 'a' && *s <= 'z') d = *s - 'a' + 10;
        else if (*s >= 'A' && *s <= 'Z') d = *s - 'A' + 10;
        else break;
        if (d >= base) break;
        val = val * base + d;
        s++;
    }
    if (end) *end = (char *)s;
    return neg ? -val : val;
}

unsigned long strtoul(const char *s, char **end, int base) {
    return (unsigned long)strtol(s, end, base);
}

long long strtoll(const char *s, char **end, int base) {
    return (long long)strtol(s, end, base);
}

unsigned long long strtoull(const char *s, char **end, int base) {
    return (unsigned long long)strtol(s, end, base);
}

int atoi(const char *s) { return (int)strtol(s, NULL, 10); }
long atol(const char *s) { return strtol(s, NULL, 10); }

// ── Sorting ────────────────────────────────────────────────────────────────────

void qsort(void *base, size_t n, size_t size,
           int (*cmp)(const void *, const void *)) {
    // Insertion sort — O(n²) but correct and simple for kernel use
    char *b = base;
    char *tmp = malloc(size);
    if (!tmp) return;
    for (size_t i = 1; i < n; i++) {
        memcpy(tmp, b + i * size, size);
        size_t j = i;
        while (j > 0 && cmp(b + (j-1) * size, tmp) > 0) {
            memcpy(b + j * size, b + (j-1) * size, size);
            j--;
        }
        memcpy(b + j * size, tmp, size);
    }
    free(tmp);
}

void *bsearch(const void *key, const void *base, size_t n, size_t size,
              int (*cmp)(const void *, const void *)) {
    const char *b = base;
    size_t lo = 0, hi = n;
    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        int r = cmp(key, b + mid * size);
        if (r == 0) return (void *)(b + mid * size);
        if (r < 0) hi = mid; else lo = mid + 1;
    }
    return NULL;
}

// ── Wide character string functions ──────────────────────────────────────────

size_t wcslen(const wchar_t *s) {
    const wchar_t *p = s;
    while (*p) p++;
    return (size_t)(p - s);
}

wchar_t *wcscpy(wchar_t *dst, const wchar_t *src) {
    wchar_t *d = dst;
    while ((*d++ = *src++)) {}
    return dst;
}

wchar_t *wcsncpy(wchar_t *dst, const wchar_t *src, size_t n) {
    wchar_t *d = dst;
    while (n-- && (*d++ = *src++)) {}
    while (n-- > 0) *d++ = L'\0';
    return dst;
}

int wcscmp(const wchar_t *a, const wchar_t *b) {
    while (*a && *a == *b) { a++; b++; }
    return (int)(*a - *b);
}

int wcsncmp(const wchar_t *a, const wchar_t *b, size_t n) {
    while (n-- && *a && *a == *b) { a++; b++; }
    if (!n) return 0;
    return (int)(*a - *b);
}

wchar_t *wcschr(const wchar_t *s, wchar_t c) {
    while (*s) { if (*s == c) return (wchar_t *)s; s++; }
    return c == L'\0' ? (wchar_t *)s : NULL;
}

wchar_t *wcsrchr(const wchar_t *s, wchar_t c) {
    const wchar_t *last = NULL;
    while (*s) { if (*s == c) last = s; s++; }
    return (wchar_t *)last;
}

wchar_t *wcscat(wchar_t *dst, const wchar_t *src) {
    wchar_t *d = dst;
    while (*d) d++;
    while ((*d++ = *src++)) {}
    return dst;
}

wchar_t *wcsncat(wchar_t *dst, const wchar_t *src, size_t n) {
    wchar_t *d = dst;
    while (*d) d++;
    while (n-- && (*d++ = *src++)) {}
    *d = L'\0';
    return dst;
}

wchar_t *wcsdup(const wchar_t *s) {
    size_t n = (wcslen(s) + 1) * sizeof(wchar_t);
    wchar_t *p = malloc(n);
    if (p) memcpy(p, s, n);
    return p;
}

size_t wcstombs(char *dst, const wchar_t *src, size_t n) {
    size_t i = 0;
    while (i + 1 < n && *src) {
        wchar_t wc = *src++;
        if (wc < 0x80) { dst[i++] = (char)wc; }
        else if (wc < 0x800) {
            if (i + 2 >= n) break;
            dst[i++] = (char)(0xC0 | (wc >> 6));
            dst[i++] = (char)(0x80 | (wc & 0x3F));
        } else {
            if (i + 3 >= n) break;
            dst[i++] = (char)(0xE0 | (wc >> 12));
            dst[i++] = (char)(0x80 | ((wc >> 6) & 0x3F));
            dst[i++] = (char)(0x80 | (wc & 0x3F));
        }
    }
    if (i < n) dst[i] = '\0';
    return i;
}

size_t mbstowcs(wchar_t *dst, const char *src, size_t n) {
    size_t i = 0;
    while (i < n && *src) dst[i++] = (wchar_t)(unsigned char)*src++;
    if (i < n) dst[i] = L'\0';
    return i;
}

int wctomb(char *s, wchar_t wc) {
    if (!s) return 0;
    if (wc < 0x80) { *s = (char)wc; return 1; }
    return -1;
}

int mbtowc(wchar_t *pwc, const char *s, size_t n) {
    if (!s || n == 0) return -1;
    if (pwc) *pwc = (wchar_t)(unsigned char)*s;
    return *s ? 1 : 0;
}

size_t mblen(const char *s, size_t n) {
    (void)n;
    if (!s || !*s) return 0;
    return 1;
}

int wcswidth(const wchar_t *s, size_t n) {
    (void)s; (void)n;
    return (int)wcslen(s);
}

long wcstol(const wchar_t *s, wchar_t **end, int base) {
    char buf[64]; size_t i = 0;
    while (i < sizeof(buf)-1 && s[i] && s[i] < 128) { buf[i] = (char)s[i]; i++; }
    buf[i] = '\0';
    char *e;
    long v = strtol(buf, &e, base);
    if (end) *end = (wchar_t *)s + (e - buf);
    return v;
}

unsigned long wcstoul(const wchar_t *s, wchar_t **end, int base) {
    char buf[64]; size_t i = 0;
    while (i < sizeof(buf)-1 && s[i] && s[i] < 128) { buf[i] = (char)s[i]; i++; }
    buf[i] = '\0';
    char *e;
    unsigned long v = strtoul(buf, &e, base);
    if (end) *end = (wchar_t *)s + (e - buf);
    return v;
}

double wcstod(const wchar_t *s, wchar_t **end) {
    char buf[64]; size_t i = 0;
    while (i < sizeof(buf)-1 && s[i] && s[i] < 128) { buf[i] = (char)s[i]; i++; }
    buf[i] = '\0';
    char *e;
    double v = strtod(buf, &e);
    if (end) *end = (wchar_t *)s + (e - buf);
    return v;
}

wint_t btowc(int c) { return (c < 0 || c > 127) ? (wint_t)-1 : (wint_t)c; }

wchar_t *wmemchr(const wchar_t *s, wchar_t c, size_t n) {
    while (n--) { if (*s == c) return (wchar_t *)s; s++; }
    return NULL;
}

wchar_t *wmemcpy(wchar_t *dst, const wchar_t *src, size_t n) {
    wchar_t *d = dst;
    while (n--) *d++ = *src++;
    return dst;
}

wchar_t *wmemset(wchar_t *s, wchar_t c, size_t n) {
    wchar_t *p = s;
    while (n--) *p++ = c;
    return s;
}

int wmemcmp(const wchar_t *a, const wchar_t *b, size_t n) {
    while (n--) {
        if (*a != *b) return *a < *b ? -1 : 1;
        a++; b++;
    }
    return 0;
}

wchar_t *wcstok(wchar_t *str, const wchar_t *delim, wchar_t **saveptr) {
    wchar_t *s = str ? str : (saveptr ? *saveptr : NULL);
    if (!s) return NULL;
    while (*s && wcschr(delim, *s)) s++;
    if (!*s) { if (saveptr) *saveptr = s; return NULL; }
    wchar_t *tok = s;
    while (*s && !wcschr(delim, *s)) s++;
    if (*s) { *s++ = L'\0'; }
    if (saveptr) *saveptr = s;
    return tok;
}

int swprintf(wchar_t *buf, size_t n, const wchar_t *fmt, ...) {
    (void)buf; (void)n; (void)fmt;
    return -1;
}

int vswprintf(wchar_t *buf, size_t n, const wchar_t *fmt, ...) {
    (void)buf; (void)n; (void)fmt;
    return -1;
}

char *strpbrk(const char *s, const char *accept) {
    for (; *s; s++) {
        for (const char *a = accept; *a; a++) {
            if (*s == *a) return (char *)s;
        }
    }
    return NULL;
}

size_t strcspn(const char *s, const char *reject) {
    size_t n = 0;
    for (; *s; s++, n++) {
        for (const char *r = reject; *r; r++) {
            if (*s == *r) return n;
        }
    }
    return n;
}

size_t strspn(const char *s, const char *accept) {
    size_t n = 0;
    for (; *s; s++) {
        const char *a;
        for (a = accept; *a && *a != *s; a++) {}
        if (!*a) break;
        n++;
    }
    return n;
}
