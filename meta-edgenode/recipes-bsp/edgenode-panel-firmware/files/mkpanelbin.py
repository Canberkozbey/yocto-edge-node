#!/usr/bin/env python3
"""Compiles a display controller's init sequence into the firmware file that
the kernel's panel-mipi-dbi driver loads (<compatible>.bin; panel.bin here).

Input, one statement per line; '#' starts a comment:

    command 0x11            # a command without parameters
    command 0x3a 0x55       # a command and its parameters
    delay 120               # wait 120 ms

Output format (drivers/gpu/drm/tiny/panel-mipi-dbi.c): the 15-byte magic
"MIPI DBI" padded with NUL bytes, file format version 1, then each command
as its byte, its number of parameters and the parameters. A delay is the
NOP command (0x00) with one parameter, the time in milliseconds; longer
delays are split into several of at most 255 ms.

    mkpanelbin.py <sequence.txt> <panel.bin>
"""

import sys

MAGIC = b"MIPI DBI" + bytes(7)
FORMAT_VERSION = 1
NOP = 0x00


def compile_sequence(text):
    out = bytearray(MAGIC)
    out.append(FORMAT_VERSION)
    for number, line in enumerate(text.splitlines(), 1):
        words = line.split("#", 1)[0].split()
        if not words:
            continue
        keyword, args = words[0], words[1:]

        def fail(reason):
            sys.exit(f"line {number}: {reason}: {line.strip()}")

        try:
            values = [int(arg, 0) for arg in args]
        except ValueError:
            fail("not a number")
        if keyword == "command":
            if not values:
                fail("command without a command byte")
            if any(not 0 <= value <= 0xFF for value in values):
                fail("value out of byte range")
            command, parameters = values[0], values[1:]
            if command == NOP:
                fail("0x00 is reserved for delays")
            out += bytes([command, len(parameters), *parameters])
        elif keyword == "delay":
            if len(values) != 1 or values[0] <= 0:
                fail("delay needs one positive number of milliseconds")
            remaining = values[0]
            while remaining:
                step = min(remaining, 0xFF)
                out += bytes([NOP, 1, step])
                remaining -= step
        else:
            fail(f"unknown statement {keyword!r}")
    return bytes(out)


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip().splitlines()[-1].strip())
    source, target = sys.argv[1:]
    with open(source, encoding="utf-8") as f:
        firmware = compile_sequence(f.read())
    with open(target, "wb") as f:
        f.write(firmware)


if __name__ == "__main__":
    main()
