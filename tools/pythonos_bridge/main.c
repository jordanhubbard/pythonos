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
#include <arpa/inet.h>
#include <errno.h>
#include <getopt.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <unistd.h>

#include "vendor/cJSON.h"

#define BRIDGE_PROTOCOL_VERSION 1
#define MAX_FRAME_BYTES         (16 * 1024 * 1024)   /* 16 MiB hard cap */

/* ─── Logging ───────────────────────────────────────────────────────────── */

static int g_verbose = 0;

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

/* ─── Op dispatch ───────────────────────────────────────────────────────── */

typedef struct {
    int fd;
    int should_exit;     /* set by op_shutdown */
} BridgeState;

/* Each op handler returns 0 on success after sending its own response,
 * or non-zero to indicate a transport error (caller tears down). */
typedef int (*op_handler)(BridgeState *, int id, cJSON *params);

static int op_hello(BridgeState *st, int id, cJSON *params) {
    int peer_proto = 0;
    cJSON *p = cJSON_GetObjectItemCaseSensitive(params, "protocol");
    if (cJSON_IsNumber(p)) peer_proto = p->valueint;

    cJSON *r = cJSON_CreateObject();
    cJSON_AddNumberToObject(r, "protocol",   BRIDGE_PROTOCOL_VERSION);
    cJSON_AddStringToObject(r, "agent",      "pythonos_bridge");
    cJSON_AddStringToObject(r, "agent_ver",  "0.1");
    cJSON_AddStringToObject(r, "sdl_ver",    SDL_GetRevision());
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

/* Dispatch table — keep small and stable; SDL ops land in later slices. */
static const struct {
    const char *name;
    op_handler  fn;
} OP_TABLE[] = {
    { "hello",    op_hello    },
    { "ping",     op_ping     },
    { "shutdown", op_shutdown },
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
    int rc;
    if (!fn) {
        rc = send_err(st->fd, id, 3, "unknown op");
    } else {
        rc = fn(st, id, params);
    }
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
    return st.should_exit ? 0 : 1;
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

    int conn = accept(srv, NULL, NULL);
    if (conn < 0) {
        LOG_ERROR("accept: %s", strerror(errno)); close(srv); return 1;
    }
    int rc = serve_fd(conn);
    close(conn);
    close(srv);
    unlink(path);
    return rc;
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
        "  --listen PATH    serve JSON-RPC frames on unix socket PATH\n"
        "  --selftest       open an SDL2 window directly (no socket, no JSON)\n"
        "  -v, --verbose    enable debug logging\n"
        "  -h, --help       show this help\n",
        argv0);
}

int main(int argc, char **argv) {
    const char *listen_path = NULL;
    int selftest = 0;

    static const struct option longopts[] = {
        { "listen",   required_argument, NULL, 'l' },
        { "selftest", no_argument,       NULL, 's' },
        { "verbose",  no_argument,       NULL, 'v' },
        { "help",     no_argument,       NULL, 'h' },
        { 0,          0,                 0,    0   },
    };

    int c;
    while ((c = getopt_long(argc, argv, "l:svh", longopts, NULL)) != -1) {
        switch (c) {
            case 'l': listen_path = optarg; break;
            case 's': selftest = 1; break;
            case 'v': g_verbose = 1; break;
            case 'h': usage(stdout, argv[0]); return 0;
            default:  usage(stderr, argv[0]); return 2;
        }
    }

    if (selftest && listen_path) {
        LOG_ERROR("--selftest and --listen are mutually exclusive");
        return 2;
    }
    if (!selftest && !listen_path) {
        usage(stderr, argv[0]);
        return 2;
    }

    if (selftest)    return run_selftest();
    return serve_unix_socket(listen_path);
}
