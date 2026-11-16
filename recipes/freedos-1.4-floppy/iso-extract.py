# SPDX-License-Identifier: AGPL-3.0-or-later
"""Copy named files out of an ISO 9660 image, reading only the primary
volume descriptor and its directory records (ECMA-119, sections 8.4 and 9.1).
Names compare case-insensitively without the ";1" version suffix.
Usage: python3 -I iso-extract.py IMAGE OUTDIR PATH...

The caller checks the image against a pinned SHA-256 before running this, so
the input is known bytes; the bounds below only stop a damaged or unexpected
image early, and the caller checks every copied file against its own pin.
Output paths come from the arguments, never from names in the image: each
PATH is a plain relative path (no empty, "." or ".." component), and the copy
lands at OUTDIR/PATH. OUTDIR must be a directory, not a link; every
directory on the way is created there or must already be a directory, never
a link (one is refused before anything is created through it), and the file
must not exist yet, so nothing is replaced or followed. A name that more
than one record of a directory carries (a multi-extent file, whose every
extent has a record, or a duplicate) is refused, and so is an interleaved
file. Nothing in the image is executed.

Ported from unemupc98's vendor/freedos/iso-extract.py, with the argument
checks, the link-safe exclusive writes, the file size bound and the refusal
of duplicate names, damaged name lengths, multi-extent and interleaved files
added."""
import os
import re
import sys

SECTOR = 2048
# A directory record is at least 33 bytes plus a one-byte name.
MIN_RECORD = 34
# Directory extents of the release LiveCD are a few sectors; more means a
# damaged image.
MAX_DIRECTORY = 4 * 1024 * 1024
# The files copied are the release's package zips, a few MB each; more
# means a damaged image or the wrong file.
MAX_FILE = 64 * 1024 * 1024
# ECMA-119 9.1.6, the file flags: a directory, and a record that is not the
# last of a file's extents.
FLAG_DIRECTORY = 0x02
FLAG_MULTI_EXTENT = 0x80
# One component of a PATH argument: d-characters in either case, '.' and
# '-', never starting with '.'.
PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
# A directory opened without following a link at its own name.
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def create(out, out_fd, path):
    """A new file at OUTDIR/PATH, opened for writing: each directory on the
    way is made (mkdir) or opened relative to its parent without following a
    link, so nothing is created or written outside OUTDIR, and the file is
    created exclusively (O_EXCL fails on any existing name, a link
    included)."""
    parts = path.split("/")
    fd = os.dup(out_fd)
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, 0o777, dir_fd=fd)
            except FileExistsError:
                pass
            try:
                inner = os.open(part, DIRECTORY_FLAGS, dir_fd=fd)
            except OSError:
                sys.exit(f"{path}: {part} in {out} is a link or not a directory")
            os.close(fd)
            fd = inner
        try:
            file_fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o666, dir_fd=fd)
        except FileExistsError:
            sys.exit(f"{path}: already exists in {out}")
    finally:
        os.close(fd)
    return os.fdopen(file_fd, "wb")


def main():
    if len(sys.argv) < 4:
        sys.exit("usage: iso-extract.py IMAGE OUTDIR PATH...")
    image, out, wanted = sys.argv[1], sys.argv[2], sys.argv[3:]
    for path in wanted:
        if not all(PART.fullmatch(part) for part in path.split("/")):
            sys.exit(f"{path}: not a plain relative path")
    if os.path.islink(out) or not os.path.isdir(out):
        sys.exit(f"{out}: not a directory")
    out_fd = os.open(out, DIRECTORY_FLAGS)
    image_size = os.path.getsize(image)
    with open(image, "rb") as iso:
        def read(lba, length):
            if lba * SECTOR + length > image_size:
                sys.exit("extent past the end of the image")
            iso.seek(lba * SECTOR)
            data = iso.read(length)
            if len(data) != length:
                sys.exit("short read from the image")
            return data

        pvd = read(16, SECTOR)
        if pvd[0] != 1 or pvd[1:6] != b"CD001":
            sys.exit("not an ISO 9660 primary volume descriptor")
        root = pvd[156:190]

        def entries(record):
            lba = int.from_bytes(record[2:6], "little")
            size = int.from_bytes(record[10:14], "little")
            if size > MAX_DIRECTORY:
                sys.exit("directory extent too large")
            data = read(lba, size)
            at = 0
            while at < len(data):
                length = data[at]
                if length == 0:
                    at = (at // SECTOR + 1) * SECTOR
                    continue
                if length < MIN_RECORD or at + length > len(data):
                    sys.exit("damaged directory record")
                entry = data[at:at + length]
                name_length = entry[32]
                if 33 + name_length > length:
                    sys.exit("damaged directory record: the name runs past the record")
                name = entry[33:33 + name_length]
                if name not in (b"\x00", b"\x01"):
                    # A name outside ASCII cannot match a PATH argument.
                    text = name.decode("ascii", "replace").split(";")[0].rstrip(".").lower()
                    yield text, entry
                at += length

        def lookup(record, part, path):
            # Every record of the name: a multi-extent file has one per
            # extent, only the last without FLAG_MULTI_EXTENT.
            found = [entry for text, entry in entries(record) if text == part]
            if not found:
                sys.exit(f"{path}: not in the image")
            if len(found) > 1:
                sys.exit(f"{path}: {len(found)} records are named {part} (a multi-extent file or a duplicate name)")
            return found[0]

        for path in wanted:
            record = root
            parts = path.lower().split("/")
            for depth, part in enumerate(parts):
                if depth > 0 and not record[25] & FLAG_DIRECTORY:
                    sys.exit(f"{path}: {parts[depth - 1]} is not a directory")
                record = lookup(record, part, path)
            if record[25] & FLAG_DIRECTORY:
                sys.exit(f"{path}: is a directory")
            if record[25] & FLAG_MULTI_EXTENT or record[26] or record[27]:
                sys.exit(f"{path}: a multi-extent or interleaved file, which this reader does not copy")
            lba = int.from_bytes(record[2:6], "little")
            size = int.from_bytes(record[10:14], "little")
            if size > MAX_FILE:
                sys.exit(f"{path}: {size} bytes, more than {MAX_FILE}")
            # Read first, so a refusal leaves nothing behind for this PATH.
            data = read(lba, size)
            with create(out, out_fd, path) as copy:
                copy.write(data)


main()
