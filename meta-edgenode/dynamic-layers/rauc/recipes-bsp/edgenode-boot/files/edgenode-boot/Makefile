# SPDX-License-Identifier: MIT
#
# Standalone build: `make`, `make install DESTDIR=...`. The Yocto recipe
# passes CC, CFLAGS and LDFLAGS through the environment and sets the
# installation directories.

PREFIX     ?= /usr
BINDIR     ?= $(PREFIX)/bin
LIBEXECDIR ?= $(PREFIX)/libexec
UNITDIR    ?= $(PREFIX)/lib/systemd/system
GENERATORDIR ?= $(PREFIX)/lib/systemd/system-generators

CFLAGS   ?= -O2 -g
WARNINGS := -Wall -Wextra -Wformat=2 -Wshadow -Wstrict-prototypes

edgenode-boot: edgenode-boot.c
	$(CC) $(CPPFLAGS) $(CFLAGS) $(WARNINGS) $(LDFLAGS) -o $@ $<

install: edgenode-boot
	install -D -m 0755 edgenode-boot $(DESTDIR)$(BINDIR)/edgenode-boot
	install -D -m 0755 edgenode-boot-commit $(DESTDIR)$(LIBEXECDIR)/edgenode-boot-commit
	install -D -m 0644 edgenode-boot-commit.service \
		$(DESTDIR)$(UNITDIR)/edgenode-boot-commit.service
	install -D -m 0644 data.mount $(DESTDIR)$(UNITDIR)/data.mount
	install -D -m 0644 edgenode-data.service $(DESTDIR)$(UNITDIR)/edgenode-data.service
	install -D -m 0755 edgenode-boot-generator \
		$(DESTDIR)$(GENERATORDIR)/edgenode-boot-generator
	install -d -m 0755 $(DESTDIR)/data

clean:
	rm -f edgenode-boot

.PHONY: install clean
