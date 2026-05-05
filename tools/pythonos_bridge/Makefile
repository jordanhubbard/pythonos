# pythonos_bridge — host-side companion for the PythonOS API bridge.
#
# Links real SDL2 (later: SDL_ttf, SDL_mixer) and exposes draw/audio/input
# to the guest as JSON-RPC over a length-prefixed byte stream. The byte
# stream itself is plumbing — see pythonos-xaz; the bridge doesn't care
# whether QEMU reaches it through TCP, a UART chardev, or stdio.

CC      ?= cc
PREFIX  ?= /usr/local

CFLAGS  ?= -O2 -g -Wall -Wextra -Wno-unused-parameter -std=c11
CFLAGS  += -D_DEFAULT_SOURCE
CFLAGS  += $(shell pkg-config --cflags sdl2)
CFLAGS  += $(shell pkg-config --cflags SDL2_ttf)
CFLAGS  += -Ivendor

LDFLAGS ?=
LDLIBS  := $(shell pkg-config --libs sdl2)
LDLIBS  += $(shell pkg-config --libs SDL2_ttf)

SRCS := main.c vendor/cJSON.c
OBJS := $(SRCS:.c=.o)
BIN  := pythonos_bridge

.PHONY: all clean install selftest

all: $(BIN)

$(BIN): $(OBJS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(OBJS) $(LDLIBS)

%.o: %.c
	$(CC) $(CFLAGS) -c -o $@ $<

# Quick smoke check — opens a window for ~2 seconds, no JSON involved.
selftest: $(BIN)
	./$(BIN) --selftest

clean:
	rm -f $(OBJS) $(BIN)

install: $(BIN)
	install -d $(DESTDIR)$(PREFIX)/bin
	install -m 755 $(BIN) $(DESTDIR)$(PREFIX)/bin/$(BIN)
