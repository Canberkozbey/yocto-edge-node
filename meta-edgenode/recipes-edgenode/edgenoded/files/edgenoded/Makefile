# SPDX-License-Identifier: MIT
#
# Standalone build: `make`, `make install DESTDIR=...`.
# The Yocto recipe passes CC, CFLAGS and LDFLAGS through the environment
# and sets VERSION, BINDIR and UNITDIR.

VERSION  ?= 0.0.0-dev
PREFIX   ?= /usr
BINDIR   ?= $(PREFIX)/bin
UNITDIR  ?= $(PREFIX)/lib/systemd/system

CFLAGS   ?= -O2 -g
WARNINGS := -Wall -Wextra -Wformat=2 -Wshadow -Wstrict-prototypes

edgenoded: edgenoded.c
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) \
		-DEDGENODED_VERSION='"$(VERSION)"' $(LDFLAGS) -o $@ $<

install: edgenoded
	install -D -m 0755 edgenoded $(DESTDIR)$(BINDIR)/edgenoded
	install -D -m 0644 edgenoded.service $(DESTDIR)$(UNITDIR)/edgenoded.service

clean:
	rm -f edgenoded

.PHONY: install clean
