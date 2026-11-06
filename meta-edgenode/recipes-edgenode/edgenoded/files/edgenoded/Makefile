# SPDX-License-Identifier: MIT
#
# Standalone build: `make SPLEEN=/path/to/spleen`, `make install DESTDIR=...`.
# SPLEEN is a checkout of https://github.com/fcambus/spleen, whose BDF fonts
# are converted to C at build time (bdf2c.py).
# The Yocto recipe passes CC, CFLAGS and LDFLAGS through the environment
# and sets VERSION, SPLEEN, BINDIR and UNITDIR.

VERSION  ?= 0.0.0-dev
PREFIX   ?= /usr
BINDIR   ?= $(PREFIX)/bin
UNITDIR  ?= $(PREFIX)/lib/systemd/system
PYTHON   ?= python3

CFLAGS   ?= -O2 -g
WARNINGS := -Wall -Wextra -Wformat=2 -Wshadow -Wstrict-prototypes
LDLIBS   := -lm

SRCS     := edgenoded.c sensors.c display.c draw.c screen.c fonts.c
OBJS     := $(SRCS:.c=.o)
FONTS    := font_small=$(SPLEEN)/spleen-12x24.bdf \
	    font_large=$(SPLEEN)/spleen-16x32.bdf

edgenoded: $(OBJS)
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(OBJS) $(LDLIBS)

%.o: %.c
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) \
		-DEDGENODED_VERSION='"$(VERSION)"' -c -o $@ $<

fonts.c: bdf2c.py $(if $(SPLEEN),$(SPLEEN)/spleen-12x24.bdf $(SPLEEN)/spleen-16x32.bdf)
	@test -n "$(SPLEEN)" || \
		{ echo "Set SPLEEN to a checkout of https://github.com/fcambus/spleen" >&2; exit 1; }
	$(PYTHON) bdf2c.py $(FONTS) > $@.tmp
	mv $@.tmp $@

edgenoded.o: display.h draw.h font.h screen.h sensors.h
sensors.o: sensors.h
display.o: display.h draw.h font.h
draw.o: draw.h font.h
screen.o: screen.h draw.h font.h sensors.h
fonts.o: font.h

install: edgenoded
	install -D -m 0755 edgenoded $(DESTDIR)$(BINDIR)/edgenoded
	install -D -m 0644 edgenoded.service $(DESTDIR)$(UNITDIR)/edgenoded.service

clean:
	rm -f edgenoded $(OBJS) fonts.c fonts.c.tmp

.PHONY: install clean
