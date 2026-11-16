# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests of recipes/freedos-1.4-floppy/iso-extract.py on tiny ISO 9660
images built here, in a fresh temporary directory: the listed files are
copied byte for byte, and each refusal fires before anything is written
where it should not be. Offline, standard library only.

    python3 -I tests/iso_extract_test.py
"""

import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
READER = HERE.parent / "recipes" / "freedos-1.4-floppy" / "iso-extract.py"

SECTOR = 2048
# iso-extract.py's bound on one copied file.
MAX_FILE = 64 * 1024 * 1024
DIRECTORY = 0x02
MULTI_EXTENT = 0x80


def both32(value):
    return value.to_bytes(4, "little") + value.to_bytes(4, "big")


def record(name, lba, size, flags=0, unit=0, gap=0, name_length=None):
    """One directory record (ECMA-119 9.1), padded to an even length."""
    body = (
        bytes(1) + both32(lba) + both32(size) + bytes(7)
        + bytes([flags, unit, gap]) + (1).to_bytes(2, "little") + (1).to_bytes(2, "big")
        + bytes([len(name) if name_length is None else name_length]) + name
    )
    if len(body) % 2 == 0:
        body += bytes(1)
    return bytes([len(body) + 1]) + body


class Image:
    """Extents from sector 18 on; write() adds the primary volume
    descriptor at sector 16 naming the root directory."""

    def __init__(self):
        self.data = bytearray()

    def extent(self, data):
        """DATA at the next free sector, padded to whole sectors (one at
        least); its first sector number."""
        lba = 18 + len(self.data) // SECTOR
        self.data += data + bytes(-len(data) % SECTOR)
        if not data:
            self.data += bytes(SECTOR)
        return lba

    def file(self, name, data, **fields):
        return record(name, self.extent(data), len(data), **fields)

    def directory(self, records):
        data = record(b"\x00", 0, 0, DIRECTORY) + record(b"\x01", 0, 0, DIRECTORY) + b"".join(records)
        assert len(data) <= SECTOR
        return self.extent(data), len(data)

    def subdirectory(self, name, records):
        lba, size = self.directory(records)
        return record(name, lba, size, DIRECTORY)

    def write(self, path, root):
        pvd = bytearray(SECTOR)
        pvd[0:7] = b"\x01CD001\x01"
        pvd[156:190] = record(b"\x00", root[0], root[1], DIRECTORY)
        with open(path, "wb") as handle:
            handle.write(bytes(16 * SECTOR) + pvd + bytes(SECTOR) + self.data)


class IsoExtract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = pathlib.Path(temporary.name).resolve()
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.iso = self.tmp / "image.iso"

    def standard(self, **top_fields):
        """PACKAGES/BASE/A.ZIP and TOP.ZIP, with TOP.ZIP's record fields."""
        image = Image()
        base = image.subdirectory(b"BASE", [image.file(b"A.ZIP;1", b"package A\n")])
        packages = image.subdirectory(b"PACKAGES", [base])
        top = image.file(b"TOP.ZIP;1", b"top file\n", **top_fields)
        image.write(self.iso, image.directory([packages, top]))

    def run_reader(self, *paths, out=None):
        return subprocess.run(
            [sys.executable, "-I", str(READER), str(self.iso), str(out or self.out), *paths],
            capture_output=True, text=True,
        )

    def files(self, directory=None):
        root = directory or self.out
        return sorted(str(p.relative_to(root)) for p in root.rglob("*"))

    def refused(self, message, *paths, out=None):
        done = self.run_reader(*paths, out=out)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn(message, done.stderr)
        return done

    def test_copies_the_listed_files_and_nothing_else(self):
        self.standard()
        done = self.run_reader("packages/base/a.zip", "TOP.ZIP")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((self.out / "packages" / "base" / "a.zip").read_bytes(), b"package A\n")
        self.assertEqual((self.out / "TOP.ZIP").read_bytes(), b"top file\n")
        self.assertEqual(self.files(), ["TOP.ZIP", "packages", "packages/base", "packages/base/a.zip"])

    def test_multi_extent_file(self):
        image = Image()
        first = image.file(b"PKG.ZIP;1", b"first extent", flags=MULTI_EXTENT)
        last = image.file(b"PKG.ZIP;1", b"last extent")
        image.write(self.iso, image.directory([first, last]))
        self.refused("2 records are named pkg.zip", "pkg.zip")
        self.assertEqual(self.files(), [])

    def test_single_record_marked_multi_extent(self):
        self.standard(flags=MULTI_EXTENT)
        self.refused("multi-extent or interleaved", "top.zip")
        self.assertEqual(self.files(), [])

    def test_interleaved_file(self):
        self.standard(unit=1, gap=1)
        self.refused("multi-extent or interleaved", "top.zip")

    def test_duplicate_name(self):
        image = Image()
        good = image.file(b"PKG.ZIP;1", b"good")
        evil = image.file(b"PKG.ZIP;1", b"evil")
        image.write(self.iso, image.directory([good, evil]))
        self.refused("2 records are named pkg.zip", "pkg.zip")
        self.assertEqual(self.files(), [])

    def test_paths_that_are_not_plain_and_relative(self):
        self.standard()
        for path in ("../top.zip", "/top.zip", "packages/../top.zip", "packages//base/a.zip",
                     "./top.zip", ".top.zip", "top.zip/", ""):
            with self.subTest(path=path):
                self.refused("not a plain relative path", path)
        self.assertEqual(self.files(), [])

    def test_existing_target(self):
        self.standard()
        (self.out / "top.zip").write_bytes(b"kept")
        self.refused("already exists", "top.zip")
        self.assertEqual((self.out / "top.zip").read_bytes(), b"kept")

    def test_link_at_the_target(self):
        self.standard()
        victim = self.tmp / "victim"
        victim.write_bytes(b"kept")
        (self.out / "top.zip").symlink_to(victim)
        (self.out / "packages" / "base").mkdir(parents=True)
        (self.out / "packages" / "base" / "a.zip").symlink_to(self.tmp / "missing")
        self.refused("already exists", "top.zip")
        self.refused("already exists", "packages/base/a.zip")
        self.assertEqual(victim.read_bytes(), b"kept")
        self.assertFalse((self.tmp / "missing").exists())

    def test_link_in_the_output_path(self):
        self.standard()
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (self.out / "packages").symlink_to(elsewhere)
        self.refused("packages in", "packages/base/a.zip")
        self.assertEqual(self.files(elsewhere), [])
        (self.out / "packages").unlink()
        (self.out / "packages").mkdir()
        (self.out / "packages" / "base").symlink_to(self.tmp / "missing")
        self.refused("base in", "packages/base/a.zip")
        self.assertFalse((self.tmp / "missing").exists())

    def test_output_directory_that_is_a_link(self):
        self.standard()
        link = self.tmp / "link"
        link.symlink_to(self.out)
        self.refused("not a directory", "top.zip", out=link)
        self.assertEqual(self.files(), [])

    def test_extent_past_the_end(self):
        image = Image()
        image.write(self.iso, image.directory([record(b"PKG.ZIP;1", 1000, 100)]))
        self.refused("extent past the end of the image", "pkg.zip")
        self.assertEqual(self.files(), [])

    def test_file_over_the_limit(self):
        image = Image()
        image.write(self.iso, image.directory([record(b"BIG.ZIP;1", 18, MAX_FILE + 1)]))
        self.refused(f"{MAX_FILE + 1} bytes, more than {MAX_FILE}", "big.zip")
        self.assertEqual(self.files(), [])

    def test_name_longer_than_its_record(self):
        image = Image()
        image.write(self.iso, image.directory([record(b"PKG.ZIP;1", 18, 4, name_length=200)]))
        self.refused("the name runs past the record", "pkg.zip")

    def test_name_not_in_the_image(self):
        self.standard()
        self.refused("not in the image", "packages/base/b.zip")
        self.assertEqual(self.files(), [])


if __name__ == "__main__":
    unittest.main()
