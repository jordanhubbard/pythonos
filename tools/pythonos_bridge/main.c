/*
 * pythonos_bridge — host-side companion that serves draw/audio/input
 * ops to PythonOS over a JSON-RPC byte stream.
 *
 * Architecture: see beads pythonos-xkj (epic) and pythonos-juo (protocol).
 *
 * Wire format: length-prefixed JSON frames.
 *   4-byte big-endian unsigned length, then UTF-8 JSON payload.
 *
 * Frame schemas:
 *   request:  {"v":1,"id":<int>,"op":<str>,"params":{...}}
 *   response: {"v":1,"id":<int>,"ok":true,"result":{...}}
 *   error:    {"v":1,"id":<int>,"ok":false,"error":{"code":...,"msg":...}}
 *   event:    {"v":1,"id":0,"op":<str>,"params":{...}}     (host->guest)
 *
 * Slice 1: hello / ping / shutdown ops + --selftest mode that opens a
 * real SDL2 window directly (no socket, no JSON). The display / audio
 * / input ops land in subsequent slices.
 */

#include <SDL.h>
#include <SDL_image.h>
#include <SDL_ttf.h>
#include <arpa/inet.h>
#include <errno.h>
#include <getopt.h>
#include <limits.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "vendor/cJSON.h"
#include "font.h"

#define BRIDGE_PROTOCOL_VERSION 1
#define MAX_FRAME_BYTES         (16 * 1024 * 1024)   /* 16 MiB hard cap */

/* ─── Logging ───────────────────────────────────────────────────────────── */

static int g_verbose = 0;
/* When set (by op_batch), send_ok/send_err become no-ops so individual
 * ops inside a batch don't write per-op responses. The batch handler
 * sends ONE response after running all child ops. */
static int g_silent_response = 0;

static void bridge_log(const char *level, const char *fmt, ...) {
    fprintf(stderr, "[pythonos_bridge:%s] ", level);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
}

#define LOG_INFO(...)  bridge_log("info",  __VA_ARGS__)
#define LOG_WARN(...)  bridge_log("warn",  __VA_ARGS__)
#define LOG_ERROR(...) bridge_log("error", __VA_ARGS__)
#define LOG_DEBUG(...) do { if (g_verbose) bridge_log("debug", __VA_ARGS__); } while (0)

/* ─── Frame I/O ─────────────────────────────────────────────────────────── */

/* Read exactly `len` bytes from `fd` into `buf`. Returns 0 on success, -1
 * on EOF before completion, -2 on transient error. */
static int read_exact(int fd, void *buf, size_t len) {
    char *p = (char *)buf;
    size_t got = 0;
    while (got < len) {
        ssize_t n = read(fd, p + got, len - got);
        if (n == 0) return -1;                 /* peer closed */
        if (n < 0) {
            if (errno == EINTR) continue;
            return -2;
        }
        got += (size_t)n;
    }
    return 0;
}

static int write_exact(int fd, const void *buf, size_t len) {
    const char *p = (const char *)buf;
    size_t put = 0;
    while (put < len) {
        ssize_t n = write(fd, p + put, len - put);
        if (n < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        put += (size_t)n;
    }
    return 0;
}

static int read_frame(int fd, char **out_payload, size_t *out_len) {
    uint32_t be_len = 0;
    int rc = read_exact(fd, &be_len, sizeof(be_len));
    if (rc != 0) return rc;
    uint32_t len = ntohl(be_len);
    if (len == 0 || len > MAX_FRAME_BYTES) {
        LOG_ERROR("rejecting frame of length %u (cap=%u)", len, MAX_FRAME_BYTES);
        return -3;
    }
    char *payload = (char *)malloc(len + 1);
    if (!payload) return -2;
    if (read_exact(fd, payload, len) != 0) {
        free(payload);
        return -1;
    }
    payload[len] = '\0';
    *out_payload = payload;
    *out_len = len;
    return 0;
}

static int write_frame(int fd, const char *payload, size_t len) {
    if (len > MAX_FRAME_BYTES) {
        LOG_ERROR("refusing to write oversized frame (%zu)", len);
        return -1;
    }
    uint32_t be_len = htonl((uint32_t)len);
    if (write_exact(fd, &be_len, sizeof(be_len)) != 0) return -1;
    if (write_exact(fd, payload, len) != 0) return -1;
    return 0;
}

/* ─── Response helpers ──────────────────────────────────────────────────── */

static cJSON *make_response_envelope(int id) {
    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "v",  BRIDGE_PROTOCOL_VERSION);
    cJSON_AddNumberToObject(r, "id", id);
    return r;
}

static int send_ok(int fd, int id, cJSON *result_or_null) {
    if (g_silent_response) {
        if (result_or_null) cJSON_Delete(result_or_null);
        return 0;
    }
    cJSON *env = make_response_envelope(id);
    cJSON_AddBoolToObject(env, "ok", 1);
    if (result_or_null) {
        cJSON_AddItemToObject(env, "result", result_or_null);
    } else {
        cJSON_AddItemToObject(env, "result", cJSON_CreateObject());
    }
    char *s = cJSON_PrintUnformatted(env);
    cJSON_Delete(env);
    if (!s) return -1;
    int rc = write_frame(fd, s, strlen(s));
    free(s);
    return rc;
}

static int send_err(int fd, int id, int code, const char *msg) {
    if (g_silent_response) {
        return 0;
    }
    cJSON *env = make_response_envelope(id);
    cJSON_AddBoolToObject(env, "ok", 0);
    cJSON *err = cJSON_CreateObject();
    cJSON_AddNumberToObject(err, "code", code);
    cJSON_AddStringToObject(err, "msg",  msg ? msg : "");
    cJSON_AddItemToObject(env, "error", err);
    char *s = cJSON_PrintUnformatted(env);
    cJSON_Delete(env);
    if (!s) return -1;
    int rc = write_frame(fd, s, strlen(s));
    free(s);
    return rc;
}

/* ─── Input event queue ─────────────────────────────────────────────────── */

/* Mirrors kernel.gui.input.Event "kind" enum so the guest doesn't have to
 * remap. Keep values in sync with kernel/gui/input.py. */
#define EVT_KEY_DOWN    1
#define EVT_KEY_UP      2
#define EVT_MOUSE_MOVE  3
#define EVT_MOUSE_DOWN  4
#define EVT_MOUSE_UP    5
#define EVT_QUIT        6
#define EVT_FILE_DROP   7

typedef struct {
    int kind;
    int x, y, dx, dy;
    int button;     /* mouse button: 1=L, 2=M, 3=R */
    int code;       /* keyboard: SDL_Keycode */
    int mod;        /* keyboard: SDL_Keymod bits */
    char text[8];   /* keyboard: typed character (UTF-8) */
    int token;      /* opaque host-file token for EVT_FILE_DROP */
    long long size;
    char name[256]; /* safe basename for EVT_FILE_DROP */
} BridgeEvent;

#define EVENT_QUEUE_CAP 256
static BridgeEvent g_events[EVENT_QUEUE_CAP];
static int g_evt_head = 0, g_evt_tail = 0;

#define DROP_FILE_CAP 32
typedef struct {
    int token;
    long long size;
    char path[PATH_MAX];
    char name[256];
} HostDropFile;
static HostDropFile g_drop_files[DROP_FILE_CAP];
static int g_next_drop_token = 1;

static const char *safe_basename(const char *path) {
    const char *slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}

static HostDropFile *remember_drop_file(const char *path) {
    struct stat sb;
    if (!path || stat(path, &sb) != 0 || !S_ISREG(sb.st_mode)) return NULL;
    int token = g_next_drop_token++;
    if (g_next_drop_token <= 0) g_next_drop_token = 1;
    HostDropFile *slot = &g_drop_files[token % DROP_FILE_CAP];
    memset(slot, 0, sizeof(*slot));
    slot->token = token;
    slot->size = (long long)sb.st_size;
    snprintf(slot->path, sizeof(slot->path), "%s", path);
    snprintf(slot->name, sizeof(slot->name), "%s", safe_basename(path));
    return slot;
}

static HostDropFile *find_drop_file(int token) {
    HostDropFile *slot = &g_drop_files[token % DROP_FILE_CAP];
    return slot->token == token ? slot : NULL;
}

static void evt_enqueue(const BridgeEvent *e) {
    int next = (g_evt_head + 1) % EVENT_QUEUE_CAP;
    if (next == g_evt_tail) {
        /* Drop oldest to keep the queue from going stale. */
        g_evt_tail = (g_evt_tail + 1) % EVENT_QUEUE_CAP;
    }
    g_events[g_evt_head] = *e;
    g_evt_head = next;
}

/* Pull all pending SDL events into our queue. Called on every op so
 * the OS doesn't think the SDL window has hung. */
static void drain_sdl_events(void) {
    SDL_Event e;
    while (SDL_PollEvent(&e)) {
        BridgeEvent be = {0};
        switch (e.type) {
            case SDL_MOUSEMOTION:
                be.kind = EVT_MOUSE_MOVE;
                be.x = e.motion.x;  be.y = e.motion.y;
                be.dx = e.motion.xrel; be.dy = e.motion.yrel;
                evt_enqueue(&be);
                break;
            case SDL_MOUSEBUTTONDOWN:
                be.kind = EVT_MOUSE_DOWN;
                be.x = e.button.x;  be.y = e.button.y;
                be.button = e.button.button;
                evt_enqueue(&be);
                break;
            case SDL_MOUSEBUTTONUP:
                be.kind = EVT_MOUSE_UP;
                be.x = e.button.x;  be.y = e.button.y;
                be.button = e.button.button;
                evt_enqueue(&be);
                break;
            case SDL_KEYDOWN:
                be.kind = EVT_KEY_DOWN;
                be.code = (int)e.key.keysym.sym;
                be.mod  = e.key.keysym.mod;
                evt_enqueue(&be);
                break;
            case SDL_KEYUP:
                be.kind = EVT_KEY_UP;
                be.code = (int)e.key.keysym.sym;
                be.mod  = e.key.keysym.mod;
                evt_enqueue(&be);
                break;
            case SDL_TEXTINPUT:
                /* SDL emits a separate TEXTINPUT after KEYDOWN for
                 * printable keys (handles shift/AltGr properly). Tag
                 * the most recent KEY_DOWN with the typed text. */
                if (g_evt_head != g_evt_tail) {
                    int prev = (g_evt_head + EVENT_QUEUE_CAP - 1) % EVENT_QUEUE_CAP;
                    if (g_events[prev].kind == EVT_KEY_DOWN &&
                        g_events[prev].text[0] == '\0') {
                        size_t n = strlen(e.text.text);
                        if (n >= sizeof(g_events[prev].text)) {
                            n = sizeof(g_events[prev].text) - 1;
                        }
                        memcpy(g_events[prev].text, e.text.text, n);
                        g_events[prev].text[n] = '\0';
                    }
                }
                break;
            case SDL_DROPFILE: {
                HostDropFile *drop = remember_drop_file(e.drop.file);
                if (drop) {
                    be.kind = EVT_FILE_DROP;
                    SDL_GetMouseState(&be.x, &be.y);
                    be.token = drop->token;
                    be.size = drop->size;
                    snprintf(be.name, sizeof(be.name), "%s", drop->name);
                    evt_enqueue(&be);
                    LOG_INFO("host file dropped: %s (%lld bytes)",
                             drop->name, drop->size);
                } else {
                    LOG_WARN("ignored non-file drop: %s", e.drop.file);
                }
                SDL_free(e.drop.file);
                break;
            }
            case SDL_QUIT:
                be.kind = EVT_QUIT;
                evt_enqueue(&be);
                break;
            default:
                break;
        }
    }
}

/* ─── Display state + surface handle table ─────────────────────────────── */

typedef struct {
    int          open;
    SDL_Window  *win;
    SDL_Surface *fb;     /* software framebuffer — guest blits land here */
    int          fb_owned;
    SDL_Renderer *renderer;
    SDL_Texture  *texture;
    int          texture_w, texture_h;
    unsigned char *last_scaled_pixels;
    size_t       last_scaled_len;
    int          last_scaled_w, last_scaled_h, last_scale;
    int          w, h;
    int          fb_handle;   /* handle table entry pointing at fb (borrowed) */
} BridgeWindow;

static BridgeWindow g_window = { 0 };

static void forget_scaled_frame(void) {
    free(g_window.last_scaled_pixels);
    g_window.last_scaled_pixels = NULL;
    g_window.last_scaled_len = 0;
    g_window.last_scaled_w = g_window.last_scaled_h = 0;
    g_window.last_scale = 1;
}

static int remember_scaled_frame(const unsigned char *pixels, size_t len,
                                 int w, int h, int scale) {
    if (g_window.last_scaled_len != len) {
        unsigned char *replacement = (unsigned char *)realloc(
            g_window.last_scaled_pixels, len);
        if (!replacement) return -1;
        g_window.last_scaled_pixels = replacement;
        g_window.last_scaled_len = len;
    }
    memcpy(g_window.last_scaled_pixels, pixels, len);
    g_window.last_scaled_w = w;
    g_window.last_scaled_h = h;
    g_window.last_scale = scale;
    return 0;
}

static int render_pixels(const unsigned char *pixels, int w, int h,
                         int pitch, int scale) {
    if (!g_window.renderer) return -1;
    if (!g_window.texture || g_window.texture_w != w ||
        g_window.texture_h != h) {
        SDL_DestroyTexture(g_window.texture);
        g_window.texture = SDL_CreateTexture(
            g_window.renderer, SDL_PIXELFORMAT_ARGB8888,
            SDL_TEXTUREACCESS_STREAMING, w, h);
        if (!g_window.texture) return -1;
        g_window.texture_w = w;
        g_window.texture_h = h;
    }
    if (SDL_UpdateTexture(g_window.texture, NULL, pixels, pitch) != 0)
        return -1;
    SDL_SetRenderDrawColor(g_window.renderer, 0, 0, 0, 255);
    if (SDL_RenderClear(g_window.renderer) != 0) return -1;
    SDL_Rect dst = {
        (g_window.w - w * scale) / 2,
        (g_window.h - h * scale) / 2,
        w * scale, h * scale,
    };
    if (SDL_RenderCopy(g_window.renderer, g_window.texture, NULL, &dst) != 0)
        return -1;
    SDL_RenderPresent(g_window.renderer);
    drain_sdl_events();
    return 0;
}

#define MAX_HANDLES 1024

/* Kind tag — keeps surface/font/etc. handles in a single typed table so a
 * misdirected handle (font passed where surface expected) returns NULL
 * instead of a hard cast crash. */
typedef enum {
    HK_NONE    = 0,
    HK_SURFACE = 1,
    HK_FONT    = 2,
} HandleKind;

typedef struct {
    HandleKind kind;
    void      *ptr;     /* NULL when kind == HK_NONE */
    int        owned;   /* 1 = free on destroy; 0 = borrowed (e.g. window fb) */
} HandleEntry;

static HandleEntry g_handles[MAX_HANDLES];

static int handle_alloc_kind(HandleKind kind, void *p, int owned) {
    /* Slot 0 reserved as "invalid" sentinel. */
    for (int i = 1; i < MAX_HANDLES; i++) {
        if (g_handles[i].kind == HK_NONE) {
            g_handles[i].kind  = kind;
            g_handles[i].ptr   = p;
            g_handles[i].owned = owned;
            return i;
        }
    }
    return 0;
}

static void *handle_get_typed(int h, HandleKind expected) {
    if (h <= 0 || h >= MAX_HANDLES) return NULL;
    if (g_handles[h].kind != expected) return NULL;
    return g_handles[h].ptr;
}

/* Backward-compat: existing code paths assume surfaces. */
static int handle_alloc(SDL_Surface *s, int owned) {
    return handle_alloc_kind(HK_SURFACE, s, owned);
}

static SDL_Surface *handle_get(int h) {
    return (SDL_Surface *)handle_get_typed(h, HK_SURFACE);
}

/* Invalidate a handle slot. Cleanup of the underlying object is the
 * caller's responsibility — this function only forgets the slot. The
 * SDL_Surface specialisation kept the SDL_FreeSurface side effect for
 * existing call sites; for other kinds (fonts, …), the kind-specific
 * wrapper (wrap_TTF_CloseFont) frees the object before invalidating. */
static void handle_free(int h) {
    if (h <= 0 || h >= MAX_HANDLES) return;
    HandleEntry *e = &g_handles[h];
    if (e->kind == HK_SURFACE && e->ptr && e->owned) {
        SDL_FreeSurface((SDL_Surface *)e->ptr);
    }
    e->kind  = HK_NONE;
    e->ptr   = NULL;
    e->owned = 0;
}

static int read_payload_trailer(int fd, size_t n, char **out_buf) {
    *out_buf = NULL;
    if (n == 0) return 0;
    if (n > MAX_FRAME_BYTES) {
        LOG_ERROR("payload trailer too large (%zu)", n);
        return -1;
    }
    char *buf = (char *)malloc(n);
    if (!buf) return -1;
    if (read_exact(fd, buf, n) != 0) {
        free(buf);
        return -1;
    }
    *out_buf = buf;
    return 0;
}

/* Decode the compact rle32 wire representation used for animation frames.
 * Each record is a little-endian uint16 run length followed by one native
 * BGRX pixel (4 bytes). This deliberately has a tiny decoder and no external
 * dependency so the same protocol works across local and remote desktops. */
static int decode_rle32(const unsigned char *src, size_t src_len,
                        unsigned char *dst, size_t dst_len) {
    size_t si = 0, di = 0;
    while (si < src_len) {
        if (src_len - si < 6) return -1;
        unsigned int run = (unsigned int)src[si]
                         | ((unsigned int)src[si + 1] << 8);
        if (run == 0 || run > (dst_len - di) / 4) return -1;
        const unsigned char *pixel = src + si + 2;
        for (unsigned int i = 0; i < run; i++) {
            memcpy(dst + di, pixel, 4);
            di += 4;
        }
        si += 6;
    }
    return di == dst_len ? 0 : -1;
}

/* ─── Op dispatch ───────────────────────────────────────────────────────── */

typedef struct {
    int fd;
    int should_exit;     /* set by op_shutdown */
} BridgeState;

/* Each op handler returns 0 on success after sending its own response,
 * or non-zero to indicate a transport error (caller tears down). */
typedef int (*op_handler)(BridgeState *, int id, cJSON *params);

/* Per-operation host service time. This deliberately measures after a full
 * request has arrived and before its response is written: guest metrics add
 * transport/guest scheduling time, while these isolate the SDL co-process. */
#define METRIC_CAP 32
typedef struct {
    char name[48];
    uint64_t count, total_ticks, max_ticks;
} OpMetric;
static OpMetric g_metrics[METRIC_CAP];
static int g_metric_count = 0;

#define SLOW_TRACE_CAP 128
typedef struct { char op[48]; uint64_t ticks; } SlowTrace;
static SlowTrace g_slow_trace[SLOW_TRACE_CAP];
static int g_slow_head = 0, g_slow_count = 0;
static uint64_t g_slow_last_log_tick = 0;
static uint64_t g_slow_suppressed = 0;

static uint64_t slow_threshold_us(void) {
    const char *value = getenv("PYTHONOS_BRIDGE_SLOW_US");
    if (!value || !value[0]) return 10000;  /* 10 ms blocks a 60 Hz UI frame */
    char *end = NULL;
    unsigned long long parsed = strtoull(value, &end, 10);
    return end != value ? parsed : 10000;
}

static void slow_trace_record(const char *name, uint64_t elapsed) {
    uint64_t frequency = SDL_GetPerformanceFrequency();
    uint64_t us = frequency ? elapsed * 1000000ULL / frequency : 0;
    if (us < slow_threshold_us()) return;
    SlowTrace *row = &g_slow_trace[g_slow_head];
    snprintf(row->op, sizeof(row->op), "%s", name);
    row->ticks = elapsed;
    g_slow_head = (g_slow_head + 1) % SLOW_TRACE_CAP;
    if (g_slow_count < SLOW_TRACE_CAP) g_slow_count++;

    /* Keep every sample in the in-memory ring, but do not turn profiling into
     * a per-frame disk-write benchmark.  One diagnostic per second is enough
     * to make a live stall visible; the metrics RPC retains the full detail. */
    uint64_t now = SDL_GetPerformanceCounter();
    if (!g_slow_last_log_tick || !frequency ||
        now - g_slow_last_log_tick >= frequency) {
        if (g_slow_suppressed) {
            LOG_WARN("desktop-main blocked: op=%s service_us=%llu "
                     "(suppressed %llu similar warnings)", name,
                     (unsigned long long)us,
                     (unsigned long long)g_slow_suppressed);
        } else {
            LOG_WARN("desktop-main blocked: op=%s service_us=%llu", name,
                     (unsigned long long)us);
        }
        g_slow_last_log_tick = now;
        g_slow_suppressed = 0;
    } else {
        g_slow_suppressed++;
    }
}

static void metric_record(const char *name, uint64_t elapsed) {
    OpMetric *m = NULL;
    for (int i = 0; i < g_metric_count; i++) {
        if (strcmp(g_metrics[i].name, name) == 0) { m = &g_metrics[i]; break; }
    }
    if (!m && g_metric_count < METRIC_CAP) {
        m = &g_metrics[g_metric_count++];
        snprintf(m->name, sizeof(m->name), "%s", name);
    }
    if (!m) return;
    m->count++;
    m->total_ticks += elapsed;
    if (elapsed > m->max_ticks) m->max_ticks = elapsed;
    slow_trace_record(name, elapsed);
}

static int op_debug_metrics(BridgeState *st, int id, cJSON *params) {
    int reset = 0;
    cJSON *jreset = cJSON_GetObjectItemCaseSensitive(params, "reset");
    if (cJSON_IsTrue(jreset)) reset = 1;
    uint64_t frequency = SDL_GetPerformanceFrequency();
    cJSON *result = cJSON_CreateObject();
    cJSON_AddNumberToObject(result, "frequency_hz", (double)frequency);
    cJSON *rows = cJSON_CreateObject();
    for (int i = 0; i < g_metric_count; i++) {
        OpMetric *m = &g_metrics[i];
        cJSON *row = cJSON_CreateObject();
        cJSON_AddNumberToObject(row, "count", (double)m->count);
        cJSON_AddNumberToObject(row, "total_ticks", (double)m->total_ticks);
        cJSON_AddNumberToObject(row, "max_ticks", (double)m->max_ticks);
        cJSON_AddNumberToObject(row, "mean_us", m->count ?
            (double)(m->total_ticks * 1000000ULL / m->count) / (double)frequency : 0);
        cJSON_AddNumberToObject(row, "max_us",
            (double)(m->max_ticks * 1000000ULL) / (double)frequency);
        cJSON_AddItemToObject(rows, m->name, row);
    }
    cJSON_AddItemToObject(result, "ops", rows);
    cJSON *trace = cJSON_CreateArray();
    for (int i = 0; i < g_slow_count; i++) {
        int slot = (g_slow_head - g_slow_count + i + SLOW_TRACE_CAP) % SLOW_TRACE_CAP;
        SlowTrace *entry = &g_slow_trace[slot];
        cJSON *row = cJSON_CreateObject();
        cJSON_AddStringToObject(row, "op", entry->op);
        cJSON_AddNumberToObject(row, "service_us",
            (double)(entry->ticks * 1000000ULL / frequency));
        cJSON_AddItemToArray(trace, row);
    }
    cJSON_AddItemToObject(result, "slow_ops", trace);
    if (reset) {
        memset(g_metrics, 0, sizeof(g_metrics)); g_metric_count = 0;
        g_slow_head = g_slow_count = 0;
    }
    return send_ok(st->fd, id, result);
}

static int op_debug_capture(BridgeState *st, int id, cJSON *params) {
    cJSON *jpath = cJSON_GetObjectItemCaseSensitive(params, "path");
    if (!cJSON_IsString(jpath) || !jpath->valuestring || !jpath->valuestring[0]) {
        return send_err(st->fd, id, 4, "debug.capture: path required");
    }
    if (!g_window.open || !g_window.fb) {
        return send_err(st->fd, id, 7, "debug.capture: display not open");
    }
    SDL_Surface *capture = g_window.fb;
    SDL_Surface *scaled = NULL;
    SDL_Surface *source = NULL;
    if (g_window.renderer && g_window.last_scaled_pixels) {
        scaled = SDL_CreateRGBSurfaceWithFormat(
            0, g_window.w, g_window.h, 32, SDL_PIXELFORMAT_ARGB8888);
        source = SDL_CreateRGBSurfaceFrom(
            g_window.last_scaled_pixels,
            g_window.last_scaled_w, g_window.last_scaled_h, 32,
            g_window.last_scaled_w * 4,
            0x00FF0000, 0x0000FF00, 0x000000FF, 0);
        if (!scaled || !source) {
            SDL_FreeSurface(source); SDL_FreeSurface(scaled);
            return send_err(st->fd, id, 11, SDL_GetError());
        }
        SDL_FillRect(scaled, NULL, SDL_MapRGB(scaled->format, 0, 0, 0));
        SDL_Rect dst = {
            (g_window.w - g_window.last_scaled_w * g_window.last_scale) / 2,
            (g_window.h - g_window.last_scaled_h * g_window.last_scale) / 2,
            g_window.last_scaled_w * g_window.last_scale,
            g_window.last_scaled_h * g_window.last_scale,
        };
        if (SDL_BlitScaled(source, NULL, scaled, &dst) != 0) {
            SDL_FreeSurface(source); SDL_FreeSurface(scaled);
            return send_err(st->fd, id, 11, SDL_GetError());
        }
        capture = scaled;
    }
    if (SDL_SaveBMP(capture, jpath->valuestring) != 0) {
        SDL_FreeSurface(source); SDL_FreeSurface(scaled);
        return send_err(st->fd, id, 8, SDL_GetError());
    }
    SDL_FreeSurface(source); SDL_FreeSurface(scaled);
    cJSON *result = cJSON_CreateObject();
    cJSON_AddStringToObject(result, "path", jpath->valuestring);
    cJSON_AddNumberToObject(result, "w", g_window.w);
    cJSON_AddNumberToObject(result, "h", g_window.h);
    return send_ok(st->fd, id, result);
}

static int op_hello(BridgeState *st, int id, cJSON *params) {
    int peer_proto = 0;
    cJSON *p = cJSON_GetObjectItemCaseSensitive(params, "protocol");
    if (cJSON_IsNumber(p)) peer_proto = p->valueint;

    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "protocol",   BRIDGE_PROTOCOL_VERSION);
    cJSON_AddStringToObject(r, "agent",      "pythonos_bridge");
    cJSON_AddStringToObject(r, "agent_ver",  "0.1");
    cJSON_AddStringToObject(r, "sdl_ver",    SDL_GetRevision());
    cJSON *features = cJSON_CreateArray();
    cJSON_AddItemToArray(features, cJSON_CreateString("batch"));
    cJSON_AddItemToArray(features, cJSON_CreateString("image.decode"));
    cJSON_AddItemToArray(features, cJSON_CreateString("frame.rle32"));
    cJSON_AddItemToArray(features, cJSON_CreateString("oneway"));
    cJSON_AddItemToArray(features, cJSON_CreateString("file.drop"));
    cJSON_AddItemToArray(features, cJSON_CreateString("file.export"));
    cJSON_AddItemToObject(r, "features", features);
    if (peer_proto != 0 && peer_proto != BRIDGE_PROTOCOL_VERSION) {
        LOG_WARN("peer requested protocol v%d, we are v%d",
                 peer_proto, BRIDGE_PROTOCOL_VERSION);
    }
    return send_ok(st->fd, id, r);
}

static int op_ping(BridgeState *st, int id, cJSON *params) {
    cJSON *r = cJSON_CreateObject();
    /* Echo any 'tag' the peer sent so they can correlate. */
    cJSON *tag = cJSON_GetObjectItemCaseSensitive(params, "tag");
    if (tag) cJSON_AddItemToObject(r, "tag", cJSON_Duplicate(tag, 1));
    cJSON_AddStringToObject(r, "pong", "ok");
    return send_ok(st->fd, id, r);
}

static int op_shutdown(BridgeState *st, int id, cJSON *params) {
    int rc = send_ok(st->fd, id, NULL);
    st->should_exit = 1;
    return rc;
}

/* ─── Display + surface ops ─────────────────────────────────────────────── */

static int op_display_open(BridgeState *st, int id, cJSON *params) {
    cJSON *jw = cJSON_GetObjectItemCaseSensitive(params, "w");
    cJSON *jh = cJSON_GetObjectItemCaseSensitive(params, "h");
    cJSON *jt = cJSON_GetObjectItemCaseSensitive(params, "title");
    if (!cJSON_IsNumber(jw) || !cJSON_IsNumber(jh)) {
        return send_err(st->fd, id, 4, "w/h required");
    }
    int w = jw->valueint, h = jh->valueint;
    const char *title = cJSON_IsString(jt) ? jt->valuestring : "PythonOS";
    const char *desktop_mode = getenv("PYTHONOS_DESKTOP_MODE");
    int headless = desktop_mode && strcmp(desktop_mode, "headless") == 0;

    if (g_window.open) {
        if (g_window.fb_handle) handle_free(g_window.fb_handle);
        SDL_DestroyTexture(g_window.texture);
        SDL_DestroyRenderer(g_window.renderer);
        if (g_window.fb_owned) SDL_FreeSurface(g_window.fb);
        forget_scaled_frame();
        SDL_DestroyWindow(g_window.win);
        memset(&g_window, 0, sizeof(g_window));
    }
    if (SDL_WasInit(SDL_INIT_VIDEO) == 0) {
        if (SDL_Init(SDL_INIT_VIDEO) != 0) {
            return send_err(st->fd, id, 5, SDL_GetError());
        }
    }
    SDL_EventState(SDL_DROPFILE, SDL_ENABLE);
    Uint32 flags = headless ? SDL_WINDOW_HIDDEN : SDL_WINDOW_SHOWN;
    SDL_Window *win = SDL_CreateWindow(title,
                                       SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                                       w, h, flags);
    if (!win) return send_err(st->fd, id, 6, SDL_GetError());
    if (!headless) {
        SDL_ShowWindow(win);
        SDL_RaiseWindow(win);
        SDL_SetWindowInputFocus(win);
    }
    SDL_Renderer *renderer = NULL;
    SDL_Surface *fb = NULL;
    int fb_owned = 0;
    if (!headless) {
        SDL_SetHint(SDL_HINT_RENDER_SCALE_QUALITY, "0");
        renderer = SDL_CreateRenderer(win, -1, SDL_RENDERER_ACCELERATED);
        if (!renderer)
            renderer = SDL_CreateRenderer(win, -1, SDL_RENDERER_SOFTWARE);
        if (renderer) {
            fb = SDL_CreateRGBSurfaceWithFormat(
                0, w, h, 32, SDL_PIXELFORMAT_ARGB8888);
            fb_owned = 1;
        }
    }
    if (!fb) fb = SDL_GetWindowSurface(win);
    if (!fb) {
        SDL_DestroyRenderer(renderer);
        SDL_DestroyWindow(win);
        return send_err(st->fd, id, 6, SDL_GetError());
    }
    g_window.win  = win;
    g_window.fb   = fb;
    g_window.fb_owned = fb_owned;
    g_window.renderer = renderer;
    g_window.w    = w;
    g_window.h    = h;
    g_window.open = 1;
    g_window.fb_handle = handle_alloc(g_window.fb, /*owned=*/0);
    LOG_INFO("display.open: %dx%d (%s) mode=%s backend=%s fb_handle=%d",
             w, h, title, headless ? "headless" : "interactive",
             renderer ? "renderer" : "window-surface",
             g_window.fb_handle);

    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "handle", 1);
    cJSON_AddNumberToObject(r, "fb_handle", g_window.fb_handle);
    cJSON_AddNumberToObject(r, "w", w);
    cJSON_AddNumberToObject(r, "h", h);
    cJSON_AddStringToObject(r, "mode", headless ? "headless" : "interactive");
    cJSON_AddBoolToObject(r, "visible", !headless);
    return send_ok(st->fd, id, r);
}

static int op_display_close(BridgeState *st, int id, cJSON *params) {
    if (g_window.open) {
        if (g_window.fb_handle) {
            handle_free(g_window.fb_handle);
            g_window.fb_handle = 0;
        }
        SDL_DestroyTexture(g_window.texture);
        SDL_DestroyRenderer(g_window.renderer);
        if (g_window.fb_owned) SDL_FreeSurface(g_window.fb);
        forget_scaled_frame();
        SDL_DestroyWindow(g_window.win);
        memset(&g_window, 0, sizeof(g_window));
    }
    return send_ok(st->fd, id, NULL);
}

static int op_display_present(BridgeState *st, int id, cJSON *params) {
    if (!g_window.open) return send_err(st->fd, id, 7, "display not open");
    forget_scaled_frame();
    if (g_window.renderer) {
        if (render_pixels((unsigned char *)g_window.fb->pixels,
                          g_window.fb->w, g_window.fb->h,
                          g_window.fb->pitch, 1) != 0)
            return send_err(st->fd, id, 11, SDL_GetError());
    } else {
        SDL_UpdateWindowSurface(g_window.win);
        drain_sdl_events();
    }
    return send_ok(st->fd, id, NULL);
}

static int op_event_poll(BridgeState *st, int id, cJSON *params) {
    drain_sdl_events();
    cJSON *arr = cJSON_CreateArray();
    while (g_evt_tail != g_evt_head) {
        BridgeEvent be = g_events[g_evt_tail];
        g_evt_tail = (g_evt_tail + 1) % EVENT_QUEUE_CAP;
        cJSON *e = cJSON_CreateObject();
        cJSON_AddNumberToObject(e, "kind",   be.kind);
        cJSON_AddNumberToObject(e, "x",      be.x);
        cJSON_AddNumberToObject(e, "y",      be.y);
        cJSON_AddNumberToObject(e, "dx",     be.dx);
        cJSON_AddNumberToObject(e, "dy",     be.dy);
        cJSON_AddNumberToObject(e, "button", be.button);
        cJSON_AddNumberToObject(e, "code",   be.code);
        cJSON_AddNumberToObject(e, "mod",    be.mod);
        if (be.text[0]) cJSON_AddStringToObject(e, "text", be.text);
        if (be.kind == EVT_FILE_DROP) {
            cJSON_AddNumberToObject(e, "token", be.token);
            cJSON_AddNumberToObject(e, "size", (double)be.size);
            cJSON_AddStringToObject(e, "name", be.name);
        }
        cJSON_AddItemToArray(arr, e);
    }
    cJSON *r = cJSON_CreateObject();
    cJSON_AddItemToObject(r, "events", arr);
    return send_ok(st->fd, id, r);
}

/* ─── Bounded host file transfer ─────────────────────────────────────── */

#define FILE_CHUNK_MAX (32 * 1024)
#define EXPORT_CAP 16
typedef struct {
    int token;
    FILE *file;
    char temp_path[PATH_MAX];
    char final_path[PATH_MAX];
} ExportFile;
static ExportFile g_exports[EXPORT_CAP];
static int g_next_export_token = 1;

static int valid_export_name(const char *name) {
    return name && name[0] && strcmp(name, ".") != 0 && strcmp(name, "..") != 0
        && !strchr(name, '/') && !strchr(name, '\\');
}

static const char *export_directory(void) {
    static char path[PATH_MAX];
    if (path[0]) return path;
    const char *configured = getenv("PYTHONOS_EXPORT_DIR");
    if (configured && configured[0]) {
        snprintf(path, sizeof(path), "%s", configured);
    } else {
        const char *home = getenv("HOME");
        snprintf(path, sizeof(path), "%s/Downloads", home ? home : ".");
    }
    return path;
}

static int ensure_export_directory(void) {
    const char *path = export_directory();
    struct stat sb;
    if (stat(path, &sb) == 0) return S_ISDIR(sb.st_mode) ? 0 : -1;
    if (errno != ENOENT) return -1;
    return mkdir(path, 0700);
}

static void choose_export_path(const char *name, char *out, size_t out_len) {
    snprintf(out, out_len, "%s/%s", export_directory(), name);
    if (access(out, F_OK) != 0) return;
    for (int suffix = 1; suffix < 10000; suffix++) {
        snprintf(out, out_len, "%s/%s.%d", export_directory(), name, suffix);
        if (access(out, F_OK) != 0) return;
    }
}

static ExportFile *find_export(int token) {
    ExportFile *slot = &g_exports[token % EXPORT_CAP];
    return slot->token == token ? slot : NULL;
}

static void discard_export(ExportFile *slot) {
    if (!slot) return;
    if (slot->file) fclose(slot->file);
    if (slot->temp_path[0]) unlink(slot->temp_path);
    memset(slot, 0, sizeof(*slot));
}

static void discard_all_exports(void) {
    for (int i = 0; i < EXPORT_CAP; i++) discard_export(&g_exports[i]);
}

static int op_host_file_read(BridgeState *st, int id, cJSON *params) {
    cJSON *jt = cJSON_GetObjectItemCaseSensitive(params, "token");
    cJSON *jo = cJSON_GetObjectItemCaseSensitive(params, "offset");
    cJSON *jl = cJSON_GetObjectItemCaseSensitive(params, "length");
    if (!cJSON_IsNumber(jt) || !cJSON_IsNumber(jo) || !cJSON_IsNumber(jl))
        return send_err(st->fd, id, 4, "token/offset/length required");
    int length = jl->valueint;
    long long offset = (long long)jo->valuedouble;
    if (length < 1 || length > FILE_CHUNK_MAX || offset < 0)
        return send_err(st->fd, id, 4, "invalid file chunk range");
    HostDropFile *drop = find_drop_file(jt->valueint);
    if (!drop) return send_err(st->fd, id, 7, "expired host file token");
    FILE *file = fopen(drop->path, "rb");
    if (!file) return send_err(st->fd, id, errno, strerror(errno));
    if (fseek(file, (long)offset, SEEK_SET) != 0) {
        fclose(file);
        return send_err(st->fd, id, errno, "host file seek failed");
    }
    unsigned char *bytes = malloc((size_t)length);
    char *hex = malloc((size_t)length * 2 + 1);
    if (!bytes || !hex) {
        free(bytes); free(hex); fclose(file);
        return send_err(st->fd, id, 12, "OOM");
    }
    size_t got = fread(bytes, 1, (size_t)length, file);
    fclose(file);
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < got; i++) {
        hex[i * 2] = digits[bytes[i] >> 4];
        hex[i * 2 + 1] = digits[bytes[i] & 15];
    }
    hex[got * 2] = '\0';
    free(bytes);
    cJSON *r = cJSON_CreateObject();
    cJSON_AddStringToObject(r, "data", hex);
    cJSON_AddNumberToObject(r, "bytes", (double)got);
    cJSON_AddBoolToObject(r, "eof", offset + (long long)got >= drop->size);
    free(hex);
    return send_ok(st->fd, id, r);
}

static int op_host_export_begin(BridgeState *st, int id, cJSON *params) {
    cJSON *jn = cJSON_GetObjectItemCaseSensitive(params, "name");
    if (!cJSON_IsString(jn) || !valid_export_name(jn->valuestring))
        return send_err(st->fd, id, 4, "safe basename required");
    if (ensure_export_directory() != 0)
        return send_err(st->fd, id, errno, "export directory unavailable");
    int token = g_next_export_token++;
    if (g_next_export_token <= 0) g_next_export_token = 1;
    ExportFile *slot = &g_exports[token % EXPORT_CAP];
    if (slot->file) { fclose(slot->file); unlink(slot->temp_path); }
    memset(slot, 0, sizeof(*slot));
    slot->token = token;
    choose_export_path(jn->valuestring, slot->final_path,
                       sizeof(slot->final_path));
    snprintf(slot->temp_path, sizeof(slot->temp_path), "%s.part-%d-%d",
             slot->final_path, (int)getpid(), token);
    slot->file = fopen(slot->temp_path, "wb");
    if (!slot->file) {
        slot->token = 0;
        return send_err(st->fd, id, errno, strerror(errno));
    }
    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "token", token);
    cJSON_AddStringToObject(r, "path", slot->final_path);
    return send_ok(st->fd, id, r);
}

static int op_host_export_chunk(BridgeState *st, int id, cJSON *params) {
    cJSON *jt = cJSON_GetObjectItemCaseSensitive(params, "token");
    cJSON *jp = cJSON_GetObjectItemCaseSensitive(params, "payload_len");
    if (!cJSON_IsNumber(jp) || jp->valueint < 0 ||
        jp->valueint > FILE_CHUNK_MAX)
        return send_err(st->fd, id, 4, "valid payload_len required");
    size_t length = (size_t)jp->valueint;
    unsigned char *bytes = length ? malloc(length) : NULL;
    if (length && !bytes) return send_err(st->fd, id, 12, "OOM");
    if (length && read_exact(st->fd, bytes, length) != 0) {
        free(bytes);
        return -1;
    }
    ExportFile *slot = cJSON_IsNumber(jt) ? find_export(jt->valueint) : NULL;
    if (!slot || !slot->file) {
        free(bytes);
        return send_err(st->fd, id, 7, "expired export token");
    }
    size_t wrote = length ? fwrite(bytes, 1, length, slot->file) : 0;
    free(bytes);
    if (wrote != length) return send_err(st->fd, id, errno, "host write failed");
    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "bytes", (double)wrote);
    return send_ok(st->fd, id, r);
}

static int op_host_export_finish(BridgeState *st, int id, cJSON *params) {
    cJSON *jt = cJSON_GetObjectItemCaseSensitive(params, "token");
    ExportFile *slot = cJSON_IsNumber(jt) ? find_export(jt->valueint) : NULL;
    if (!slot || !slot->file)
        return send_err(st->fd, id, 7, "expired export token");
    if (fclose(slot->file) != 0) {
        slot->file = NULL;
        return send_err(st->fd, id, errno, "host close failed");
    }
    slot->file = NULL;
    if (rename(slot->temp_path, slot->final_path) != 0)
        return send_err(st->fd, id, errno, "host rename failed");
    cJSON *r = cJSON_CreateObject();
    cJSON_AddStringToObject(r, "path", slot->final_path);
    LOG_INFO("exported PythonOS file to %s", slot->final_path);
    slot->token = 0;
    return send_ok(st->fd, id, r);
}

static int op_host_export_abort(BridgeState *st, int id, cJSON *params) {
    cJSON *jt = cJSON_GetObjectItemCaseSensitive(params, "token");
    ExportFile *slot = cJSON_IsNumber(jt) ? find_export(jt->valueint) : NULL;
    if (!slot) return send_err(st->fd, id, 7, "expired export token");
    discard_export(slot);
    return send_ok(st->fd, id, NULL);
}

static int op_surface_create(BridgeState *st, int id, cJSON *params) {
    cJSON *jw = cJSON_GetObjectItemCaseSensitive(params, "w");
    cJSON *jh = cJSON_GetObjectItemCaseSensitive(params, "h");
    if (!cJSON_IsNumber(jw) || !cJSON_IsNumber(jh)) {
        return send_err(st->fd, id, 4, "w/h required");
    }
    int w = jw->valueint, h = jh->valueint;
    /* XRGB8888 to match guest byte order (B,G,R,X in memory on LE). */
    SDL_Surface *s = SDL_CreateRGBSurface(0, w, h, 32,
                                          0x00FF0000, 0x0000FF00,
                                          0x000000FF, 0x00000000);
    if (!s) return send_err(st->fd, id, 6, SDL_GetError());
    int handle = handle_alloc(s, /*owned=*/1);
    if (handle == 0) {
        SDL_FreeSurface(s);
        return send_err(st->fd, id, 10, "handle table full");
    }
    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "handle", handle);
    return send_ok(st->fd, id, r);
}

static int op_surface_destroy(BridgeState *st, int id, cJSON *params) {
    cJSON *jh = cJSON_GetObjectItemCaseSensitive(params, "handle");
    if (!cJSON_IsNumber(jh)) return send_err(st->fd, id, 4, "handle required");
    handle_free(jh->valueint);
    return send_ok(st->fd, id, NULL);
}

static int parse_rect(cJSON *jrect, SDL_Rect *out) {
    if (!cJSON_IsObject(jrect)) return -1;
    cJSON *jx = cJSON_GetObjectItemCaseSensitive(jrect, "x");
    cJSON *jy = cJSON_GetObjectItemCaseSensitive(jrect, "y");
    cJSON *jw = cJSON_GetObjectItemCaseSensitive(jrect, "w");
    cJSON *jh = cJSON_GetObjectItemCaseSensitive(jrect, "h");
    if (!cJSON_IsNumber(jx) || !cJSON_IsNumber(jy) ||
        !cJSON_IsNumber(jw) || !cJSON_IsNumber(jh)) return -1;
    out->x = jx->valueint; out->y = jy->valueint;
    out->w = jw->valueint; out->h = jh->valueint;
    return 0;
}

static int op_surface_fill_rect(BridgeState *st, int id, cJSON *params) {
    cJSON *jh    = cJSON_GetObjectItemCaseSensitive(params, "handle");
    cJSON *jrect = cJSON_GetObjectItemCaseSensitive(params, "rect");
    cJSON *jrgb  = cJSON_GetObjectItemCaseSensitive(params, "rgb");
    if (!cJSON_IsNumber(jh) || !cJSON_IsNumber(jrgb)) {
        return send_err(st->fd, id, 4, "handle/rgb required");
    }
    SDL_Surface *s = handle_get(jh->valueint);
    if (!s) return send_err(st->fd, id, 7, "invalid handle");
    SDL_Rect rect;
    SDL_Rect *rp = NULL;
    if (cJSON_IsObject(jrect)) {
        if (parse_rect(jrect, &rect) != 0) {
            return send_err(st->fd, id, 4, "rect must have x/y/w/h");
        }
        rp = &rect;
    }
    Uint32 color = (Uint32)jrgb->valuedouble;   /* may exceed int32 */
    SDL_FillRect(s, rp, color);
    return send_ok(st->fd, id, NULL);
}

static int op_surface_blit(BridgeState *st, int id, cJSON *params) {
    cJSON *jsh = cJSON_GetObjectItemCaseSensitive(params, "src");
    cJSON *jdh = cJSON_GetObjectItemCaseSensitive(params, "dst");
    if (!cJSON_IsNumber(jsh) || !cJSON_IsNumber(jdh)) {
        return send_err(st->fd, id, 4, "src/dst required");
    }
    SDL_Surface *src = handle_get(jsh->valueint);
    SDL_Surface *dst = handle_get(jdh->valueint);
    if (!src || !dst) return send_err(st->fd, id, 7, "invalid handle");
    SDL_Rect src_rect, dst_rect;
    SDL_Rect *sp = NULL, *dp = NULL;
    cJSON *jsrc = cJSON_GetObjectItemCaseSensitive(params, "src_rect");
    cJSON *jdst = cJSON_GetObjectItemCaseSensitive(params, "dst_rect");
    if (cJSON_IsObject(jsrc)) {
        if (parse_rect(jsrc, &src_rect) != 0) return send_err(st->fd, id, 4, "src_rect");
        sp = &src_rect;
    }
    if (cJSON_IsObject(jdst)) {
        if (parse_rect(jdst, &dst_rect) != 0) return send_err(st->fd, id, 4, "dst_rect");
        dp = &dst_rect;
    }
    SDL_BlitSurface(src, sp, dst, dp);
    return send_ok(st->fd, id, NULL);
}

/* Bresenham line draw. params: {handle, x0, y0, x1, y1, rgb}.
 * One bridge round-trip draws an arbitrarily long line — much
 * cheaper than emitting one fill_rect per pixel from the guest. */
static int op_surface_line(BridgeState *st, int id, cJSON *params) {
    cJSON *jh   = cJSON_GetObjectItemCaseSensitive(params, "handle");
    cJSON *jx0  = cJSON_GetObjectItemCaseSensitive(params, "x0");
    cJSON *jy0  = cJSON_GetObjectItemCaseSensitive(params, "y0");
    cJSON *jx1  = cJSON_GetObjectItemCaseSensitive(params, "x1");
    cJSON *jy1  = cJSON_GetObjectItemCaseSensitive(params, "y1");
    cJSON *jrgb = cJSON_GetObjectItemCaseSensitive(params, "rgb");
    if (!cJSON_IsNumber(jh) || !cJSON_IsNumber(jx0) || !cJSON_IsNumber(jy0)
        || !cJSON_IsNumber(jx1) || !cJSON_IsNumber(jy1)
        || !cJSON_IsNumber(jrgb)) {
        return send_err(st->fd, id, 4, "handle/x0/y0/x1/y1/rgb required");
    }
    SDL_Surface *s = handle_get(jh->valueint);
    if (!s) return send_err(st->fd, id, 7, "invalid handle");
    int x0 = jx0->valueint, y0 = jy0->valueint;
    int x1 = jx1->valueint, y1 = jy1->valueint;
    Uint32 color = (Uint32)jrgb->valuedouble;
    int dx = (x1 > x0 ? x1 - x0 : x0 - x1);
    int dy = -(y1 > y0 ? y1 - y0 : y0 - y1);
    int sx = x0 < x1 ? 1 : -1;
    int sy = y0 < y1 ? 1 : -1;
    int err = dx + dy;
    while (1) {
        SDL_Rect r = { x0, y0, 1, 1 };
        SDL_FillRect(s, &r, color);
        if (x0 == x1 && y0 == y1) break;
        int e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
    }
    return send_ok(st->fd, id, NULL);
}

/* Vertical in-place scroll. dy < 0 shifts content up by |dy| pixels;
 * dy > 0 shifts down. Pixels at the newly exposed edge are unchanged —
 * the caller is expected to paint them. memmove handles overlap.
 * params: {handle, dy} */
static int op_surface_scroll(BridgeState *st, int id, cJSON *params) {
    cJSON *jh  = cJSON_GetObjectItemCaseSensitive(params, "handle");
    cJSON *jdy = cJSON_GetObjectItemCaseSensitive(params, "dy");
    if (!cJSON_IsNumber(jh) || !cJSON_IsNumber(jdy)) {
        return send_err(st->fd, id, 4, "handle/dy required");
    }
    SDL_Surface *s = handle_get(jh->valueint);
    if (!s) return send_err(st->fd, id, 7, "invalid handle");
    int dy = jdy->valueint;
    int abs_dy = dy < 0 ? -dy : dy;
    if (dy == 0 || abs_dy >= s->h) {
        return send_ok(st->fd, id, NULL);
    }
    if (SDL_LockSurface(s) != 0) {
        return send_err(st->fd, id, 8, SDL_GetError());
    }
    int pitch = s->pitch;
    int rows = s->h - abs_dy;
    Uint8 *base = (Uint8 *)s->pixels;
    if (dy < 0) {
        memmove(base, base + abs_dy * pitch, (size_t)rows * (size_t)pitch);
    } else {
        memmove(base + dy * pitch, base, (size_t)rows * (size_t)pitch);
    }
    SDL_UnlockSurface(s);
    return send_ok(st->fd, id, NULL);
}

/* Draw an ASCII string into the surface using the embedded 8x8 bitmap
 * font. Pixels are written via SDL_FillRect for the foreground cells —
 * one fill per "on" pixel. Cheap because rect=1x1 fills are tiny.
 * params: {handle, x, y, text, fg, bg (optional)} */
static int op_text_draw(BridgeState *st, int id, cJSON *params) {
    cJSON *jh  = cJSON_GetObjectItemCaseSensitive(params, "handle");
    cJSON *jx  = cJSON_GetObjectItemCaseSensitive(params, "x");
    cJSON *jy  = cJSON_GetObjectItemCaseSensitive(params, "y");
    cJSON *jt  = cJSON_GetObjectItemCaseSensitive(params, "text");
    cJSON *jfg = cJSON_GetObjectItemCaseSensitive(params, "fg");
    cJSON *jbg = cJSON_GetObjectItemCaseSensitive(params, "bg");
    if (!cJSON_IsNumber(jh) || !cJSON_IsNumber(jx) || !cJSON_IsNumber(jy) ||
        !cJSON_IsString(jt) || !cJSON_IsNumber(jfg)) {
        return send_err(st->fd, id, 4, "handle/x/y/text/fg required");
    }
    SDL_Surface *s = handle_get(jh->valueint);
    if (!s) return send_err(st->fd, id, 7, "invalid handle");
    int x0 = jx->valueint, y0 = jy->valueint;
    Uint32 fg = (Uint32)jfg->valuedouble;
    int has_bg = cJSON_IsNumber(jbg);
    Uint32 bg = has_bg ? (Uint32)jbg->valuedouble : 0;
    const char *text = jt->valuestring;
    int cx = x0;
    int cy = y0;
    for (const char *p = text; *p; p++) {
        char c = *p;
        if (c == '\n') { cy += BRIDGE_GLYPH_H; cx = x0; continue; }
        if (c < 0x20 || c > 0x7E) c = '?';
        const uint8_t *glyph = FONT_DATA[c - 0x20];
        for (int row = 0; row < BRIDGE_GLYPH_H; row++) {
            uint8_t bits = glyph[row];
            for (int col = 0; col < BRIDGE_GLYPH_W; col++) {
                int on = bits & (0x80 >> col);
                SDL_Rect r = { cx + col, cy + row, 1, 1 };
                if (on)         SDL_FillRect(s, &r, fg);
                else if (has_bg) SDL_FillRect(s, &r, bg);
            }
        }
        cx += BRIDGE_GLYPH_W;
    }
    return send_ok(st->fd, id, NULL);
}

/* Bulk pixel upload — used by image decoders + other one-shot pixel
 * pushes. params: {handle, payload_len:N}, then N raw BGRX bytes. */
static int op_surface_upload(BridgeState *st, int id, cJSON *params) {
    cJSON *jh = cJSON_GetObjectItemCaseSensitive(params, "handle");
    cJSON *jp = cJSON_GetObjectItemCaseSensitive(params, "payload_len");
    if (!cJSON_IsNumber(jh) || !cJSON_IsNumber(jp)) {
        if (cJSON_IsNumber(jp)) {
            char *drop = NULL;
            (void)read_payload_trailer(st->fd, (size_t)jp->valueint, &drop);
            free(drop);
        }
        return send_err(st->fd, id, 4, "handle/payload_len required");
    }
    size_t plen = (size_t)jp->valueint;
    char *payload = NULL;
    if (read_payload_trailer(st->fd, plen, &payload) != 0) return -1;

    SDL_Surface *s = handle_get(jh->valueint);
    if (!s) {
        free(payload);
        return send_err(st->fd, id, 7, "invalid handle");
    }
    if (plen != (size_t)s->w * s->h * 4) {
        free(payload);
        return send_err(st->fd, id, 9, "payload size != w*h*4");
    }
    SDL_LockSurface(s);
    memcpy(s->pixels, payload, plen);
    SDL_UnlockSurface(s);
    free(payload);
    return send_ok(st->fd, id, NULL);
}

/* Encoded image upload. The guest sends the original PNG/JPEG bytes and the
 * host decodes them directly into an SDL surface. This avoids expanding the
 * 86 KiB desktop PNG to a 3 MiB framebuffer before it crosses TCP. */
static int op_surface_load_image(BridgeState *st, int id, cJSON *params) {
    cJSON *jp = cJSON_GetObjectItemCaseSensitive(params, "payload_len");
    if (!cJSON_IsNumber(jp) || jp->valuedouble <= 0) {
        return send_err(st->fd, id, 4, "payload_len required");
    }
    size_t plen = (size_t)jp->valuedouble;
    char *payload = NULL;
    if (read_payload_trailer(st->fd, plen, &payload) != 0) return -1;

    SDL_RWops *rw = SDL_RWFromConstMem(payload, (int)plen);
    SDL_Surface *decoded = rw ? IMG_Load_RW(rw, 1) : NULL;
    if (!decoded) {
        free(payload);
        return send_err(st->fd, id, 11, IMG_GetError());
    }
    SDL_Surface *surface = SDL_ConvertSurfaceFormat(
        decoded, SDL_PIXELFORMAT_ARGB8888, 0);
    SDL_FreeSurface(decoded);
    free(payload);
    if (!surface) return send_err(st->fd, id, 11, SDL_GetError());

    int handle = handle_alloc(surface, /*owned=*/1);
    if (handle == 0) {
        SDL_FreeSurface(surface);
        return send_err(st->fd, id, 10, "handle table full");
    }
    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "handle", handle);
    cJSON_AddNumberToObject(r, "w", surface->w);
    cJSON_AddNumberToObject(r, "h", surface->h);
    cJSON_AddNumberToObject(r, "wire_bytes", (double)plen);
    return send_ok(st->fd, id, r);
}

/* Upload a compact native-resolution animation frame and scale it on the
 * host. params: {handle, src_w, src_h, scale, encoding, payload_len}.
 * `encoding` is either raw BGRX or rle32. The destination is letterboxed and
 * updated by the following display.present call. */
static int op_surface_upload_scaled(BridgeState *st, int id, cJSON *params) {
    cJSON *jh = cJSON_GetObjectItemCaseSensitive(params, "handle");
    cJSON *jw = cJSON_GetObjectItemCaseSensitive(params, "src_w");
    cJSON *jheight = cJSON_GetObjectItemCaseSensitive(params, "src_h");
    cJSON *jscale = cJSON_GetObjectItemCaseSensitive(params, "scale");
    cJSON *jencoding = cJSON_GetObjectItemCaseSensitive(params, "encoding");
    cJSON *jp = cJSON_GetObjectItemCaseSensitive(params, "payload_len");
    if (!cJSON_IsNumber(jh) || !cJSON_IsNumber(jw) ||
        !cJSON_IsNumber(jheight) || !cJSON_IsNumber(jp)) {
        return send_err(st->fd, id, 4,
                        "handle/src_w/src_h/payload_len required");
    }
    size_t plen = (size_t)jp->valuedouble;
    char *payload = NULL;
    if (read_payload_trailer(st->fd, plen, &payload) != 0) return -1;
    SDL_Surface *dst = handle_get(jh->valueint);
    int src_w = jw->valueint, src_h = jheight->valueint;
    int scale = cJSON_IsNumber(jscale) ? jscale->valueint : 1;
    if (!dst || src_w <= 0 || src_h <= 0 || scale <= 0) {
        free(payload);
        return send_err(st->fd, id, 7, "invalid surface or dimensions");
    }
    size_t raw_len = (size_t)src_w * (size_t)src_h * 4;
    unsigned char *pixels = NULL;
    int owned = 0;
    const char *encoding = cJSON_IsString(jencoding)
                         ? jencoding->valuestring : "raw";
    if (strcmp(encoding, "rle32") == 0) {
        pixels = (unsigned char *)malloc(raw_len);
        owned = 1;
        if (!pixels || decode_rle32((unsigned char *)payload, plen,
                                    pixels, raw_len) != 0) {
            free(pixels); free(payload);
            return send_err(st->fd, id, 9, "invalid rle32 payload");
        }
    } else if (strcmp(encoding, "raw") == 0 && plen == raw_len) {
        pixels = (unsigned char *)payload;
    } else {
        free(payload);
        return send_err(st->fd, id, 9, "invalid raw frame payload");
    }

    cJSON *jpresent = cJSON_GetObjectItemCaseSensitive(params, "present");
    if (cJSON_IsTrue(jpresent) && g_window.open && dst == g_window.fb &&
        g_window.renderer) {
        if (remember_scaled_frame(pixels, raw_len, src_w, src_h, scale) != 0 ||
            render_pixels(pixels, src_w, src_h, src_w * 4, scale) != 0) {
            if (owned) free(pixels);
            free(payload);
            return send_err(st->fd, id, 11, SDL_GetError());
        }
        if (owned) free(pixels);
        free(payload);
        cJSON *r = cJSON_CreateObject();
        cJSON_AddNumberToObject(r, "wire_bytes", (double)plen);
        cJSON_AddNumberToObject(r, "raw_bytes", (double)raw_len);
        cJSON_AddStringToObject(r, "encoding", encoding);
        return send_ok(st->fd, id, r);
    }

    SDL_Surface *src = SDL_CreateRGBSurfaceFrom(
        pixels, src_w, src_h, 32, src_w * 4,
        0x00FF0000, 0x0000FF00, 0x000000FF, 0x00000000);
    if (!src) {
        if (owned) free(pixels);
        free(payload);
        return send_err(st->fd, id, 11, SDL_GetError());
    }
    SDL_FillRect(dst, NULL, SDL_MapRGB(dst->format, 0, 0, 0));
    SDL_Rect dr = {
        (dst->w - src_w * scale) / 2,
        (dst->h - src_h * scale) / 2,
        src_w * scale, src_h * scale,
    };
    int rc = SDL_BlitScaled(src, NULL, dst, &dr);
    SDL_FreeSurface(src);
    if (owned) free(pixels);
    free(payload);
    if (rc != 0) return send_err(st->fd, id, 11, SDL_GetError());

    if (cJSON_IsTrue(jpresent) && g_window.open && dst == g_window.fb) {
        if (SDL_UpdateWindowSurface(g_window.win) != 0)
            return send_err(st->fd, id, 11, SDL_GetError());
        drain_sdl_events();
    }

    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "wire_bytes", (double)plen);
    cJSON_AddNumberToObject(r, "raw_bytes", (double)raw_len);
    cJSON_AddStringToObject(r, "encoding", encoding);
    return send_ok(st->fd, id, r);
}

static op_handler lookup_op(const char *name);

/* ─── Generic SDL dispatcher ─────────────────────────────────────────────
 *
 * The bridge protocol's long-term shape is:
 *   sdl.call:  { "name": "<SDL_FuncName>", "args": [<positional args>] }
 *
 * Each registered SDL function lives in SDL_FN_TABLE below as a tiny
 * wrapper that pulls positional args out of the JSON array, calls the
 * libSDL2 function, and packs the return value into the response.
 * Adding a new function = one entry. PythonOS doesn't carry a custom
 * decomposition of SDL — guest sdl2 wrappers just marshal arguments.
 *
 * Convention: every wrapper sends a response shaped {"rc": <int>} for
 * status-returning functions, {"handle": <int>} for pointer-returning
 * functions, or {} for void. During op_batch (which sets
 * g_silent_response), the per-call response is suppressed and the
 * batch handler sends a single ok at the end.
 */

typedef int (*sdl_wrapper)(BridgeState *st, int id, cJSON *args);

/* Helper: send {"rc": rc} as the ok response. */
static int send_ok_rc(int fd, int id, int rc) {
    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "rc", rc);
    return send_ok(fd, id, r);
}

/* Helper: parse an SDL_Rect from either {x,y,w,h} object or [x,y,w,h] array.
 * Returns 0 on success, -1 if the JSON shape is wrong. */
static int parse_rect_any(cJSON *je, SDL_Rect *out) {
    if (cJSON_IsArray(je) && cJSON_GetArraySize(je) >= 4) {
        out->x = cJSON_GetArrayItem(je, 0)->valueint;
        out->y = cJSON_GetArrayItem(je, 1)->valueint;
        out->w = cJSON_GetArrayItem(je, 2)->valueint;
        out->h = cJSON_GetArrayItem(je, 3)->valueint;
        return 0;
    }
    return parse_rect(je, out);
}

/* SDL_FillRect(surface, rect_or_null, color)  →  int rc */
static int wrap_SDL_FillRect(BridgeState *st, int id, cJSON *args) {
    if (cJSON_GetArraySize(args) < 3)
        return send_err(st->fd, id, 4, "SDL_FillRect: 3 args");
    cJSON *jh = cJSON_GetArrayItem(args, 0);
    cJSON *jr = cJSON_GetArrayItem(args, 1);
    cJSON *jc = cJSON_GetArrayItem(args, 2);
    SDL_Surface *s = handle_get(jh->valueint);
    if (!s) return send_err(st->fd, id, 7, "invalid surface");
    SDL_Rect rect, *rp = NULL;
    if (!cJSON_IsNull(jr) && (cJSON_IsObject(jr) || cJSON_IsArray(jr))) {
        if (parse_rect_any(jr, &rect) != 0)
            return send_err(st->fd, id, 4, "bad rect");
        rp = &rect;
    }
    return send_ok_rc(st->fd, id,
                      SDL_FillRect(s, rp, (Uint32)jc->valuedouble));
}

/* SDL_FillRects(surface, rect_array, color)  →  int rc
 * The natural batching primitive for animation: one bridge round-trip
 * fills hundreds of rects. */
static int wrap_SDL_FillRects(BridgeState *st, int id, cJSON *args) {
    if (cJSON_GetArraySize(args) < 3)
        return send_err(st->fd, id, 4, "SDL_FillRects: 3 args");
    cJSON *jh = cJSON_GetArrayItem(args, 0);
    cJSON *jr = cJSON_GetArrayItem(args, 1);
    cJSON *jc = cJSON_GetArrayItem(args, 2);
    SDL_Surface *s = handle_get(jh->valueint);
    if (!s) return send_err(st->fd, id, 7, "invalid surface");
    if (!cJSON_IsArray(jr))
        return send_err(st->fd, id, 4, "rects must be array");
    int n = cJSON_GetArraySize(jr);
    if (n <= 0) return send_ok_rc(st->fd, id, 0);
    SDL_Rect *rects = (SDL_Rect *)calloc((size_t)n, sizeof(SDL_Rect));
    if (!rects) return send_err(st->fd, id, 12, "OOM");
    for (int i = 0; i < n; i++) {
        if (parse_rect_any(cJSON_GetArrayItem(jr, i), &rects[i]) != 0) {
            free(rects);
            return send_err(st->fd, id, 4, "bad rect in array");
        }
    }
    int rc = SDL_FillRects(s, rects, n, (Uint32)jc->valuedouble);
    free(rects);
    return send_ok_rc(st->fd, id, rc);
}

/* SDL_BlitSurface(src, src_rect_or_null, dst, dst_rect_or_null)  →  int rc */
static int wrap_SDL_BlitSurface(BridgeState *st, int id, cJSON *args) {
    if (cJSON_GetArraySize(args) < 4)
        return send_err(st->fd, id, 4, "SDL_BlitSurface: 4 args");
    cJSON *jsh = cJSON_GetArrayItem(args, 0);
    cJSON *jsr = cJSON_GetArrayItem(args, 1);
    cJSON *jdh = cJSON_GetArrayItem(args, 2);
    cJSON *jdr = cJSON_GetArrayItem(args, 3);
    SDL_Surface *src = handle_get(jsh->valueint);
    SDL_Surface *dst = handle_get(jdh->valueint);
    if (!src || !dst) return send_err(st->fd, id, 7, "invalid handle");
    SDL_Rect sr, dr, *sp = NULL, *dp = NULL;
    if (!cJSON_IsNull(jsr) && (cJSON_IsObject(jsr) || cJSON_IsArray(jsr))) {
        if (parse_rect_any(jsr, &sr) != 0)
            return send_err(st->fd, id, 4, "bad src_rect");
        sp = &sr;
    }
    if (!cJSON_IsNull(jdr) && (cJSON_IsObject(jdr) || cJSON_IsArray(jdr))) {
        if (parse_rect_any(jdr, &dr) != 0)
            return send_err(st->fd, id, 4, "bad dst_rect");
        dp = &dr;
    }
    return send_ok_rc(st->fd, id, SDL_BlitSurface(src, sp, dst, dp));
}

/* SDL_GetTicks()  →  uint32 ticks (returned in {"rc": ticks}). */
static int wrap_SDL_GetTicks(BridgeState *st, int id, cJSON *args) {
    (void)args;
    return send_ok_rc(st->fd, id, (int)SDL_GetTicks());
}

/* ─── SDL_ttf wrappers ─────────────────────────────────────────────────
 *
 * Real anti-aliased text rendering via SDL_ttf. Each text-rendering
 * function returns a freshly-allocated SDL_Surface* — registered into
 * the same handle table the surface API uses, so guests can blit it
 * onto any window's backing surface and free it when done.
 */

/* TTF_Init()  →  int rc. Idempotent: SDL_ttf tracks its own ref count. */
static int wrap_TTF_Init(BridgeState *st, int id, cJSON *args) {
    (void)args;
    return send_ok_rc(st->fd, id, TTF_Init());
}

/* TTF_Quit()  →  void. */
static int wrap_TTF_Quit(BridgeState *st, int id, cJSON *args) {
    (void)args;
    TTF_Quit();
    return send_ok(st->fd, id, NULL);
}

/* TTF_OpenFont(path, ptsize)  →  {"handle": int} on success.
 * Returns error code 11 + error message if the font can't be loaded. */
static int wrap_TTF_OpenFont(BridgeState *st, int id, cJSON *args) {
    if (cJSON_GetArraySize(args) < 2)
        return send_err(st->fd, id, 4, "TTF_OpenFont: 2 args (path, ptsize)");
    cJSON *jpath = cJSON_GetArrayItem(args, 0);
    cJSON *jpt   = cJSON_GetArrayItem(args, 1);
    if (!cJSON_IsString(jpath) || !cJSON_IsNumber(jpt))
        return send_err(st->fd, id, 4, "TTF_OpenFont: (string, int)");
    TTF_Font *f = TTF_OpenFont(jpath->valuestring, jpt->valueint);
    if (!f) return send_err(st->fd, id, 11, TTF_GetError());
    int h = handle_alloc_kind(HK_FONT, f, /*owned=*/1);
    if (h == 0) { TTF_CloseFont(f); return send_err(st->fd, id, 12, "OOM"); }
    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "handle", h);
    return send_ok(st->fd, id, r);
}

/* TTF_CloseFont(font_handle)  →  void. */
static int wrap_TTF_CloseFont(BridgeState *st, int id, cJSON *args) {
    if (cJSON_GetArraySize(args) < 1)
        return send_err(st->fd, id, 4, "TTF_CloseFont: 1 arg");
    cJSON *jh = cJSON_GetArrayItem(args, 0);
    if (!cJSON_IsNumber(jh)) return send_err(st->fd, id, 4, "handle int");
    TTF_Font *f = (TTF_Font *)handle_get_typed(jh->valueint, HK_FONT);
    if (f) {
        TTF_CloseFont(f);
        /* handle_free's surface-only branch is a no-op for fonts; we've
         * already freed the underlying object above. */
        handle_free(jh->valueint);
    }
    return send_ok(st->fd, id, NULL);
}

/* TTF_RenderUTF8_Blended(font, text, color)  →  {"handle": int, "w": w, "h": h}
 *
 * Color is packed as a Uint32 0xRRGGBBAA. The returned surface handle
 * points to a freshly-allocated SDL_Surface that the guest can blit
 * onto any target. The guest is responsible for freeing the surface
 * (handle.destroy / SDL_FreeSurface) once it's drawn — typical pattern
 * is render → blit → free, all in one batched flush.
 */
static int wrap_TTF_RenderUTF8_Blended(BridgeState *st, int id, cJSON *args) {
    if (cJSON_GetArraySize(args) < 3)
        return send_err(st->fd, id, 4,
                        "TTF_RenderUTF8_Blended: 3 args (font, text, rgba)");
    cJSON *jh    = cJSON_GetArrayItem(args, 0);
    cJSON *jtext = cJSON_GetArrayItem(args, 1);
    cJSON *jrgba = cJSON_GetArrayItem(args, 2);
    if (!cJSON_IsNumber(jh) || !cJSON_IsString(jtext) || !cJSON_IsNumber(jrgba))
        return send_err(st->fd, id, 4, "(int, string, int)");
    TTF_Font *f = (TTF_Font *)handle_get_typed(jh->valueint, HK_FONT);
    if (!f) return send_err(st->fd, id, 7, "invalid font handle");
    Uint32 rgba = (Uint32)jrgba->valuedouble;
    SDL_Color c = {
        .r = (rgba >> 24) & 0xFF,
        .g = (rgba >> 16) & 0xFF,
        .b = (rgba >>  8) & 0xFF,
        .a = (rgba      ) & 0xFF,
    };
    SDL_Surface *s = TTF_RenderUTF8_Blended(f, jtext->valuestring, c);
    if (!s) return send_err(st->fd, id, 11, TTF_GetError());
    /* Convert to the same pixel format the windowing surfaces use, so
     * SDL_BlitSurface doesn't have to do per-blit conversions. */
    SDL_Surface *conv = SDL_ConvertSurfaceFormat(s, SDL_PIXELFORMAT_ARGB8888, 0);
    if (conv) { SDL_FreeSurface(s); s = conv; }
    int h = handle_alloc_kind(HK_SURFACE, s, /*owned=*/1);
    if (h == 0) { SDL_FreeSurface(s); return send_err(st->fd, id, 12, "OOM"); }
    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "handle", h);
    cJSON_AddNumberToObject(r, "w", s->w);
    cJSON_AddNumberToObject(r, "h", s->h);
    return send_ok(st->fd, id, r);
}

/* TTF_SizeUTF8(font, text)  →  {"w": w, "h": h, "rc": rc}.
 * Lets the guest measure text without allocating a surface. */
static int wrap_TTF_SizeUTF8(BridgeState *st, int id, cJSON *args) {
    if (cJSON_GetArraySize(args) < 2)
        return send_err(st->fd, id, 4, "TTF_SizeUTF8: 2 args (font, text)");
    cJSON *jh = cJSON_GetArrayItem(args, 0);
    cJSON *jtext = cJSON_GetArrayItem(args, 1);
    if (!cJSON_IsNumber(jh) || !cJSON_IsString(jtext))
        return send_err(st->fd, id, 4, "(int, string)");
    TTF_Font *f = (TTF_Font *)handle_get_typed(jh->valueint, HK_FONT);
    if (!f) return send_err(st->fd, id, 7, "invalid font handle");
    int w = 0, h = 0;
    int rc = TTF_SizeUTF8(f, jtext->valuestring, &w, &h);
    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "rc", rc);
    cJSON_AddNumberToObject(r, "w", w);
    cJSON_AddNumberToObject(r, "h", h);
    return send_ok(st->fd, id, r);
}

/* ─── pyo: small environment-bridging helpers ────────────────────────────
 *
 * "pyo." ops are NOT pure SDL — they answer questions the guest can't
 * answer for itself (host filesystem layout, font discovery). Keep this
 * set tiny; once a CPython interpreter lives on the host, queries like
 * font discovery move into Python and these go away.
 */

/* Search common system font locations and return the first that exists.
 * Returns {"path": <str>} on success or error code 13 if none found. */
static int wrap_pyo_default_font_path(BridgeState *st, int id, cJSON *args) {
    (void)args;
    static const char *CANDIDATES[] = {
        /* macOS — monospace first, proportional fallback. */
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        /* Linux distros — varies. */
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        NULL,
    };
    for (int i = 0; CANDIDATES[i]; i++) {
        struct stat sb;
        if (stat(CANDIDATES[i], &sb) == 0 && S_ISREG(sb.st_mode)) {
            cJSON *r = cJSON_CreateObject();
            cJSON_AddStringToObject(r, "path", CANDIDATES[i]);
            return send_ok(st->fd, id, r);
        }
    }
    return send_err(st->fd, id, 13, "no default font found on host");
}

static const struct {
    const char *name;
    sdl_wrapper fn;
} SDL_FN_TABLE[] = {
    { "SDL_FillRect",            wrap_SDL_FillRect            },
    { "SDL_FillRects",           wrap_SDL_FillRects           },
    { "SDL_BlitSurface",         wrap_SDL_BlitSurface         },
    { "SDL_GetTicks",            wrap_SDL_GetTicks            },
    { "TTF_Init",                wrap_TTF_Init                },
    { "TTF_Quit",                wrap_TTF_Quit                },
    { "TTF_OpenFont",            wrap_TTF_OpenFont            },
    { "TTF_CloseFont",           wrap_TTF_CloseFont           },
    { "TTF_RenderUTF8_Blended",  wrap_TTF_RenderUTF8_Blended  },
    { "TTF_SizeUTF8",            wrap_TTF_SizeUTF8            },
    { "pyo.default_font_path",   wrap_pyo_default_font_path   },
};
#define SDL_FN_TABLE_LEN ((int)(sizeof(SDL_FN_TABLE) / sizeof(SDL_FN_TABLE[0])))

static sdl_wrapper lookup_sdl_fn(const char *name) {
    for (int i = 0; i < SDL_FN_TABLE_LEN; i++) {
        if (strcmp(SDL_FN_TABLE[i].name, name) == 0) return SDL_FN_TABLE[i].fn;
    }
    return NULL;
}

/* op_sdl_call: dispatch to a registered SDL wrapper.
 * params: { "name": "<SDL_FuncName>", "args": [<positional args>] }
 */
static int op_sdl_call(BridgeState *st, int id, cJSON *params) {
    cJSON *jname = cJSON_GetObjectItemCaseSensitive(params, "name");
    cJSON *jargs = cJSON_GetObjectItemCaseSensitive(params, "args");
    if (!cJSON_IsString(jname))
        return send_err(st->fd, id, 4, "sdl.call: name required");
    sdl_wrapper fn = lookup_sdl_fn(jname->valuestring);
    if (!fn) return send_err(st->fd, id, 5, "sdl.call: unknown SDL fn");
    /* Tolerate omitted args: treat as empty array. */
    cJSON *empty = NULL;
    if (!cJSON_IsArray(jargs)) {
        empty = cJSON_CreateArray();
        jargs = empty;
    }
    int rc = fn(st, id, jargs);
    if (empty) cJSON_Delete(empty);
    return rc;
}

/* Run a list of fire-and-forget ops in a single round-trip.
 *
 * params: { "ops": [ { "op": <str>, "params": {...} }, ... ] }
 *
 * Each child op's per-call response is suppressed (g_silent_response).
 * The batch sends ONE response with {"count": N, "errors": M}. Designed
 * for ops where the guest doesn't care about per-op return data — fill
 * rects, blits, text.draw — i.e. animation frames. Stateful ops that
 * return handles (surface.create, hello) MUST NOT be batched.
 */
static int op_batch(BridgeState *st, int id, cJSON *params) {
    cJSON *ops = cJSON_GetObjectItemCaseSensitive(params, "ops");
    if (!cJSON_IsArray(ops)) {
        return send_err(st->fd, id, 4, "batch: ops must be array");
    }
    int n = cJSON_GetArraySize(ops);
    int errors = 0;
    g_silent_response = 1;
    for (int i = 0; i < n; i++) {
        cJSON *entry = cJSON_GetArrayItem(ops, i);
        if (!cJSON_IsObject(entry)) { errors++; continue; }
        cJSON *op = cJSON_GetObjectItemCaseSensitive(entry, "op");
        cJSON *p  = cJSON_GetObjectItemCaseSensitive(entry, "params");
        if (!cJSON_IsString(op)) { errors++; continue; }
        op_handler fn = lookup_op(op->valuestring);
        if (!fn) { errors++; continue; }
        (void)fn(st, id, p);
    }
    g_silent_response = 0;
    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "count",  n);
    cJSON_AddNumberToObject(r, "errors", errors);
    return send_ok(st->fd, id, r);
}

/* Dispatch table — keep small and stable; input/audio ops land in later slices. */
static const struct {
    const char *name;
    op_handler  fn;
} OP_TABLE[] = {
    { "hello",              op_hello              },
    { "ping",               op_ping               },
    { "shutdown",           op_shutdown           },
    { "batch",              op_batch              },
    { "sdl.call",           op_sdl_call           },
    { "display.open",       op_display_open       },
    { "display.close",      op_display_close      },
    { "display.present",    op_display_present    },
    { "surface.create",     op_surface_create     },
    { "surface.destroy",    op_surface_destroy    },
    { "surface.fill_rect",  op_surface_fill_rect  },
    { "surface.blit",       op_surface_blit       },
    { "surface.scroll",     op_surface_scroll     },
    { "surface.line",       op_surface_line       },
    { "surface.upload",     op_surface_upload     },
    { "surface.load_image", op_surface_load_image },
    { "surface.upload_scaled", op_surface_upload_scaled },
    { "text.draw",          op_text_draw          },
    { "event.poll",         op_event_poll         },
    { "host.file.read",     op_host_file_read     },
    { "host.export.begin",  op_host_export_begin  },
    { "host.export.chunk",  op_host_export_chunk  },
    { "host.export.finish", op_host_export_finish },
    { "host.export.abort",  op_host_export_abort  },
    { "debug.metrics",      op_debug_metrics      },
    { "debug.capture",      op_debug_capture      },
};

#define OP_TABLE_LEN ((int)(sizeof(OP_TABLE) / sizeof(OP_TABLE[0])))

static op_handler lookup_op(const char *name) {
    for (int i = 0; i < OP_TABLE_LEN; i++) {
        if (strcmp(OP_TABLE[i].name, name) == 0) return OP_TABLE[i].fn;
    }
    return NULL;
}

/* ─── Server loop ───────────────────────────────────────────────────────── */

static int handle_one_frame(BridgeState *st, const char *payload, size_t len) {
    cJSON *root = cJSON_ParseWithLength(payload, len);
    if (!root) {
        LOG_WARN("malformed JSON frame (%zu bytes)", len);
        return send_err(st->fd, 0, 1, "malformed JSON");
    }

    int id = 0;
    cJSON *jid = cJSON_GetObjectItemCaseSensitive(root, "id");
    if (cJSON_IsNumber(jid)) id = jid->valueint;

    cJSON *op = cJSON_GetObjectItemCaseSensitive(root, "op");
    cJSON *params = cJSON_GetObjectItemCaseSensitive(root, "params");
    if (!cJSON_IsString(op)) {
        cJSON_Delete(root);
        return send_err(st->fd, id, 2, "missing 'op' field");
    }

    op_handler fn = lookup_op(op->valuestring);
    LOG_DEBUG("dispatch op=%s id=%d", op->valuestring, id);
    uint64_t started = SDL_GetPerformanceCounter();
    int rc;
    int previous_silent = g_silent_response;
    if (id == 0) g_silent_response = 1;
    if (!fn) {
        rc = send_err(st->fd, id, 3, "unknown op");
    } else {
        rc = fn(st, id, params);
    }
    g_silent_response = previous_silent;
    metric_record(op->valuestring, SDL_GetPerformanceCounter() - started);
    cJSON_Delete(root);
    return rc;
}

static int serve_fd(int fd) {
    BridgeState st = { .fd = fd, .should_exit = 0 };
    LOG_INFO("client connected (fd=%d)", fd);
    for (;;) {
        char  *payload = NULL;
        size_t len = 0;
        int rc = read_frame(fd, &payload, &len);
        if (rc == -1) { LOG_INFO("peer closed"); break; }
        if (rc != 0)  { LOG_ERROR("frame read failed (rc=%d)", rc); break; }
        rc = handle_one_frame(&st, payload, len);
        free(payload);
        if (rc != 0) { LOG_ERROR("handler aborted transport"); break; }
        if (st.should_exit) {
            LOG_INFO("shutdown requested by peer");
            break;
        }
    }
    discard_all_exports();
    return st.should_exit ? 0 : 1;
}

static void cleanup_sdl(void) {
    if (g_window.open) {
        SDL_DestroyTexture(g_window.texture);
        SDL_DestroyRenderer(g_window.renderer);
        if (g_window.fb_owned) SDL_FreeSurface(g_window.fb);
        forget_scaled_frame();
        SDL_DestroyWindow(g_window.win);
        memset(&g_window, 0, sizeof(g_window));
    }
    IMG_Quit();
    if (SDL_WasInit(SDL_INIT_VIDEO)) SDL_Quit();
}

static int socket_buffer_bytes(void) {
    const char *value = getenv("PYTHONOS_BRIDGE_SOCKET_BUFFER");
    if (!value || !value[0]) return 4 * 1024 * 1024;
    char *end = NULL;
    long parsed = strtol(value, &end, 10);
    if (end == value || *end != '\0' || parsed < 65536 ||
        parsed > 64 * 1024 * 1024) {
        LOG_WARN("ignoring invalid PYTHONOS_BRIDGE_SOCKET_BUFFER=%s", value);
        return 4 * 1024 * 1024;
    }
    return (int)parsed;
}

static void tune_tcp_socket(int fd) {
    struct sockaddr_storage address;
    socklen_t address_len = sizeof(address);
    if (getsockname(fd, (struct sockaddr *)&address, &address_len) != 0 ||
        address.ss_family != AF_INET) return;
    int one = 1;
    int bytes = socket_buffer_bytes();
    if (setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one)) != 0)
        LOG_WARN("TCP_NODELAY: %s", strerror(errno));
    if (setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &bytes, sizeof(bytes)) != 0)
        LOG_WARN("SO_RCVBUF: %s", strerror(errno));
    if (setsockopt(fd, SOL_SOCKET, SO_SNDBUF, &bytes, sizeof(bytes)) != 0)
        LOG_WARN("SO_SNDBUF: %s", strerror(errno));
    int actual_rx = 0, actual_tx = 0;
    socklen_t n = sizeof(int);
    (void)getsockopt(fd, SOL_SOCKET, SO_RCVBUF, &actual_rx, &n);
    n = sizeof(int);
    (void)getsockopt(fd, SOL_SOCKET, SO_SNDBUF, &actual_tx, &n);
    LOG_INFO("TCP tuned: nodelay=1 rcvbuf=%d sndbuf=%d", actual_rx, actual_tx);
}

static int accept_loop(int srv) {
    int final_rc = 1;
    for (;;) {
        int conn = accept(srv, NULL, NULL);
        if (conn < 0) {
            LOG_ERROR("accept: %s", strerror(errno));
            break;
        }
        tune_tcp_socket(conn);
        int rc = serve_fd(conn);
        close(conn);
        if (rc == 0) {
            final_rc = 0;
            break;
        }
        LOG_INFO("client disconnected; waiting for next client");
    }
    cleanup_sdl();
    return final_rc;
}

static int serve_unix_socket(const char *path) {
    /* Stale-socket cleanup. */
    struct stat sb;
    if (lstat(path, &sb) == 0 && S_ISSOCK(sb.st_mode)) {
        if (unlink(path) != 0) {
            LOG_ERROR("unlink %s: %s", path, strerror(errno));
            return 1;
        }
    }

    int srv = socket(AF_UNIX, SOCK_STREAM, 0);
    if (srv < 0) { LOG_ERROR("socket: %s", strerror(errno)); return 1; }

    struct sockaddr_un addr = { .sun_family = AF_UNIX };
    if (strlen(path) >= sizeof(addr.sun_path)) {
        LOG_ERROR("socket path too long: %s", path); close(srv); return 1;
    }
    strncpy(addr.sun_path, path, sizeof(addr.sun_path) - 1);

    if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        LOG_ERROR("bind %s: %s", path, strerror(errno)); close(srv); return 1;
    }
    if (listen(srv, 1) != 0) {
        LOG_ERROR("listen: %s", strerror(errno)); close(srv); return 1;
    }
    LOG_INFO("listening on %s", path);

    int rc = accept_loop(srv);
    close(srv);
    unlink(path);
    return rc;
}

static int parse_tcp_endpoint(const char *spec,
                              char *host, size_t host_len,
                              int *port_out) {
    const char *port_s = spec;
    const char *colon = strrchr(spec, ':');
    if (colon) {
        size_t n = (size_t)(colon - spec);
        if (n == 0 || n >= host_len) return -1;
        memcpy(host, spec, n);
        host[n] = '\0';
        port_s = colon + 1;
    } else {
        snprintf(host, host_len, "127.0.0.1");
    }

    char *end = NULL;
    long port = strtol(port_s, &end, 10);
    if (!port_s[0] || *end != '\0' || port <= 0 || port > 65535)
        return -1;
    *port_out = (int)port;
    return 0;
}

static int serve_tcp_socket(const char *endpoint) {
    char host[64];
    int port = 0;
    if (parse_tcp_endpoint(endpoint, host, sizeof(host), &port) != 0) {
        LOG_ERROR("invalid TCP endpoint '%s' (expected HOST:PORT or PORT)",
                  endpoint);
        return 2;
    }

    int srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) { LOG_ERROR("socket: %s", strerror(errno)); return 1; }

    int one = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    if (strcmp(host, "*") == 0 || strcmp(host, "0.0.0.0") == 0) {
        addr.sin_addr.s_addr = htonl(INADDR_ANY);
    } else if (strcmp(host, "localhost") == 0) {
        if (inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr) != 1) {
            close(srv);
            return 1;
        }
    } else if (inet_pton(AF_INET, host, &addr.sin_addr) != 1) {
        LOG_ERROR("invalid IPv4 listen host '%s'", host);
        close(srv);
        return 2;
    }

    if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        LOG_ERROR("bind tcp %s:%d: %s", host, port, strerror(errno));
        close(srv);
        return 1;
    }
    if (listen(srv, 4) != 0) {
        LOG_ERROR("listen: %s", strerror(errno)); close(srv); return 1;
    }
    LOG_INFO("listening on tcp %s:%d", host, port);

    int rc = accept_loop(srv);
    close(srv);
    return rc;
}

static long long monotonic_ms(void) {
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
    return (long long)ts.tv_sec * 1000LL + (long long)(ts.tv_nsec / 1000000LL);
}

static int connect_tcp_socket(const char *endpoint, int timeout_ms) {
    char host[64];
    int port = 0;
    if (parse_tcp_endpoint(endpoint, host, sizeof(host), &port) != 0) {
        LOG_ERROR("invalid TCP endpoint '%s' (expected HOST:PORT or PORT)",
                  endpoint);
        return 2;
    }
    if (strcmp(host, "*") == 0 || strcmp(host, "0.0.0.0") == 0) {
        LOG_ERROR("connect host must be a concrete IPv4 address");
        return 2;
    }

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    if (strcmp(host, "localhost") == 0) {
        if (inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr) != 1)
            return 1;
    } else if (inet_pton(AF_INET, host, &addr.sin_addr) != 1) {
        LOG_ERROR("invalid IPv4 connect host '%s'", host);
        return 2;
    }

    long long start = monotonic_ms();
    long long deadline = timeout_ms < 0 ? -1 : start + timeout_ms;
    long long next_log = start;
    for (;;) {
        int fd = socket(AF_INET, SOCK_STREAM, 0);
        if (fd < 0) {
            LOG_ERROR("socket: %s", strerror(errno));
            return 1;
        }
        if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) == 0) {
            tune_tcp_socket(fd);
            LOG_INFO("connected to tcp %s:%d", host, port);
            int rc = serve_fd(fd);
            close(fd);
            cleanup_sdl();
            return rc;
        }
        int saved = errno;
        close(fd);
        long long now = monotonic_ms();
        if (deadline >= 0 && now >= deadline) {
            LOG_ERROR("connect tcp %s:%d timed out: %s",
                      host, port, strerror(saved));
            return 1;
        }
        if (now >= next_log) {
            LOG_INFO("waiting for tcp %s:%d (%s)", host, port, strerror(saved));
            next_log = now + 1000;
        }
        usleep(100000);
    }
}

/* ─── Self-test ─────────────────────────────────────────────────────────── */

static int run_selftest(void) {
    if (SDL_Init(SDL_INIT_VIDEO) != 0) {
        LOG_ERROR("SDL_Init: %s", SDL_GetError());
        return 1;
    }
    SDL_Window *w = SDL_CreateWindow("pythonos_bridge selftest",
                                     SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                                     640, 480, SDL_WINDOW_SHOWN);
    if (!w) {
        LOG_ERROR("SDL_CreateWindow: %s", SDL_GetError());
        SDL_Quit();
        return 1;
    }
    SDL_Surface *fb = SDL_GetWindowSurface(w);
    SDL_FillRect(fb, NULL, SDL_MapRGB(fb->format, 0x20, 0x28, 0x40));
    SDL_Rect r = { 80, 80, 320, 200 };
    SDL_FillRect(fb, &r, SDL_MapRGB(fb->format, 0x22, 0x44, 0x88));
    SDL_UpdateWindowSurface(w);

    LOG_INFO("selftest window open — press ESC or close to dismiss");

    Uint32 deadline = SDL_GetTicks() + 10000;
    SDL_Event e;
    int running = 1;
    while (running && SDL_GetTicks() < deadline) {
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) running = 0;
            if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_ESCAPE)
                running = 0;
        }
        SDL_Delay(16);
    }

    SDL_DestroyWindow(w);
    SDL_Quit();
    LOG_INFO("selftest exited cleanly");
    return 0;
}

/* ─── Main ──────────────────────────────────────────────────────────────── */

static void usage(FILE *out, const char *argv0) {
    fprintf(out,
        "usage: %s [options]\n"
        "  --listen-tcp HOST:PORT  serve JSON-RPC frames on TCP\n"
        "  --connect-tcp HOST:PORT connect to a PythonOS TCP bridge listener\n"
        "  --connect-timeout-ms N  connect retry timeout (default: 30000; -1 forever)\n"
        "  --listen PATH           serve JSON-RPC frames on unix socket PATH\n"
        "  --selftest              open an SDL2 window directly (no socket, no JSON)\n"
        "  -v, --verbose           enable debug logging\n"
        "  -h, --help              show this help\n",
        argv0);
}

int main(int argc, char **argv) {
    const char *listen_path = NULL;
    const char *listen_tcp = NULL;
    const char *connect_tcp = NULL;
    int connect_timeout_ms = 30000;
    int selftest = 0;

    static const struct option longopts[] = {
        { "listen",             required_argument, NULL, 'l' },
        { "listen-tcp",         required_argument, NULL, 't' },
        { "connect-tcp",        required_argument, NULL, 'c' },
        { "connect-timeout-ms", required_argument, NULL, 'T' },
        { "selftest",           no_argument,       NULL, 's' },
        { "verbose",            no_argument,       NULL, 'v' },
        { "help",               no_argument,       NULL, 'h' },
        { 0,                    0,                 0,    0   },
    };

    int c;
    while ((c = getopt_long(argc, argv, "l:t:c:T:svh", longopts, NULL)) != -1) {
        switch (c) {
            case 'l': listen_path = optarg; break;
            case 't': listen_tcp = optarg; break;
            case 'c': connect_tcp = optarg; break;
            case 'T':
                connect_timeout_ms = atoi(optarg);
                if (connect_timeout_ms < -1) {
                    LOG_ERROR("--connect-timeout-ms must be -1 or >= 0");
                    return 2;
                }
                break;
            case 's': selftest = 1; break;
            case 'v': g_verbose = 1; break;
            case 'h': usage(stdout, argv[0]); return 0;
            default:  usage(stderr, argv[0]); return 2;
        }
    }

    int mode_count = (listen_path ? 1 : 0) + (listen_tcp ? 1 : 0)
                   + (connect_tcp ? 1 : 0) + (selftest ? 1 : 0);
    if (mode_count != 1) {
        LOG_ERROR("choose exactly one of --listen-tcp, --connect-tcp, --listen, or --selftest");
        usage(stderr, argv[0]);
        return 2;
    }

    if (selftest)    return run_selftest();
    if (listen_tcp)  return serve_tcp_socket(listen_tcp);
    if (connect_tcp) return connect_tcp_socket(connect_tcp, connect_timeout_ms);
    return serve_unix_socket(listen_path);
}
