# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests of lib/recipes.py, offline, and of this repository's own recipe
files: what the pipeline reads of them, and the facts unemu's check cannot
see (the archive script's pins, the locks and bundled inputs on disk).

    python3 -I tests/recipes_test.py
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import stat
import sys
import tempfile
import tomllib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPT = ROOT / "lib" / "recipes.py"

_spec = importlib.util.spec_from_file_location("recipes", SCRIPT)
recipes = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = recipes
_spec.loader.exec_module(recipes)

PIN = "0123456789abcdef0123456789abcdef01234567"
PIN_TOML = f"""commit = "{PIN}"
tag = "v9.9.9"
version = "9.9.9"

[archive.x86_64-unknown-linux-gnu]
file = "unemu-9.9.9-x86_64-unknown-linux-gnu.tar.gz"
sha256 = "{'a' * 64}"
size = 1
unemu_sha256 = "{'b' * 64}"
unemu_size = 1
"""
DIGEST = "sha256:" + "d" * 64

MEDIA = f"""recipe = "1.0"
work = "demo"

[media]
name = "unemu/media/ibm-pc/demo"
platform = "ibm-pc"
release = "1.0-720k"
revision = 2
digest = "{DIGEST}"

[license]
expression = "GPL-2.0-or-later"

[input.licenses]
name = "licenses.txt"
size = 1
sha256 = "{'a' * 64}"
bundle = true

[input.archive]
name = "DEMO.ZIP"
size = 1
sha256 = "{'b' * 64}"
"""

FIRMWARE = """recipe = "1.0"

[firmware]
name = "unemu/firmware/ibm-pc/demo-bios"
platform = "ibm-pc"
revision = 1

[license]
expression = "GPL-2.0-or-later"
"""

PRIVATE = MEDIA.replace("unemu/media/", "private/media/").replace(
    '[license]\nexpression = "GPL-2.0-or-later"\n\n', "")
PRIVATE_PATH = "recipes/private/media/demo/demo-1.0-720k.media.toml"

# A fake unemu for `check`: its --version names PIN, and `recipe check`
# prints what the test's configuration says.
FAKE_CLI = r'''#!@PYTHON@ -I
import json, sys
with open(@CONFIG@) as handle:
    config = json.load(handle)
with open(@LOG@, "a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\n")
if sys.argv[1:] == ["--version"]:
    print("unemu 9.9.9\ncommit: " + config.get("commit", "@PIN@"))
    sys.exit(0)
if sys.argv[1:3] == ["recipe", "check"]:
    for line in config.get("lines", []):
        print(line)
    print("N files checked")
    sys.exit(config.get("status", 0))
sys.exit(2)
'''


class Parse(unittest.TestCase):
    def test_kind_of(self):
        for path, kind in (("recipes/media/demo/demo-1.0.media.toml", "media"),
                           ("recipes/firmware/demo-bios.firmware.toml", "firmware"),
                           ("recipes/x.media.toml", "media"),
                           ("recipes/media/demo/demo.work.toml", None),
                           ("recipes/media/demo/.media.toml", None),
                           ("recipes/media/.old/demo.media.toml", None),
                           ("recipes/media/demo/demo.media.toml.lock/x.media.toml", None),
                           ("recipes/media/demo/demo.media.toml.bak", None),
                           ("recipes/media/demo/demo.toml", None),
                           ("other/demo.media.toml", None),
                           ("demo.media.toml", None),
                           ("recipes//demo.media.toml", None)):
            self.assertEqual(recipes.kind_of(path), kind, path)

    def test_a_recipe(self):
        recipe = recipes.parse("recipes/media/demo/demo-1.0-720k.media.toml", MEDIA.encode())
        self.assertEqual((recipe.kind, recipe.repository, recipe.release, recipe.revision, recipe.digest),
                         ("media", "unemu/media/ibm-pc/demo", "1.0-720k", 2, DIGEST))
        self.assertEqual((recipe.tag, recipe.alias, recipe.ref),
                         ("1.0-720k-r2", "1.0-720k", "unemu/media/ibm-pc/demo:1.0-720k-r2"))
        self.assertEqual((recipe.work, recipe.bundled, recipe.license), ("demo", ("licenses.txt",), "GPL-2.0-or-later"))
        self.assertEqual((recipe.folder, recipe.lock),
                         ("recipes/media/demo", "recipes/media/demo/demo-1.0-720k.media.toml.lock/"))
        package = recipes.parse("recipes/firmware/demo-bios.firmware.toml", FIRMWARE.encode())
        self.assertEqual((package.tag, package.alias, package.ref, package.digest, package.license, package.work),
                         ("r1", None, "unemu/firmware/ibm-pc/demo-bios:r1", None, "GPL-2.0-or-later", None))
        self.assertFalse(recipe.private)
        private = recipes.parse(PRIVATE_PATH, PRIVATE.encode())
        self.assertTrue(private.private)
        self.assertIsNone(private.license)
        private_firmware = FIRMWARE.replace("unemu/firmware/", "private/firmware/").replace(
            '\n[license]\nexpression = "GPL-2.0-or-later"\n', "")
        self.assertTrue(recipes.parse("recipes/private/firmware/demo.firmware.toml", private_firmware.encode()).private)

    def test_refusals(self):
        for path, text, fragment in (
            ("recipes/x.media.toml", b"\xff", "not a TOML recipe"),
            ("recipes/x.media.toml", b"[media", "not a TOML recipe"),
            ("recipes/x.work.toml", MEDIA.encode(), "not a recipe file"),
            ("recipes/x.firmware.toml", MEDIA.encode(), "one identity table, [firmware]"),
            ("recipes/x.media.toml", (MEDIA + "\n[firmware]\n").encode(), "one identity table"),
            ("recipes/x.media.toml", MEDIA.replace("unemu/media/ibm-pc/demo", "unemu/firmware/ibm-pc/demo").encode(),
             "not under unemu/media/"),
            ("recipes/x.media.toml", MEDIA.replace("unemu/media/ibm-pc/demo", "private/media/ibm-pc/demo").encode(),
             "a private recipe must have no [license]"),
            ("recipes/x.media.toml", MEDIA.replace("unemu/media/ibm-pc/demo", "unemu/media/ibm-pc/Demo").encode(),
             "not a repository name"),
            ("recipes/x.media.toml", MEDIA.replace('release = "1.0-720k"', 'release = "1.0-r1"').encode(),
             "not a release"),
            ("recipes/x.media.toml", MEDIA.replace('release = "1.0-720k"', 'release = "1.0_"').encode(),
             "not a release"),
            ("recipes/x.media.toml", MEDIA.replace('release = "1.0-720k"', "release = 1").encode(), "not a release"),
            ("recipes/x.media.toml", MEDIA.replace("revision = 2", "revision = 0").encode(), "not a whole number"),
            ("recipes/x.media.toml", MEDIA.replace("revision = 2", 'revision = "2"').encode(), "not a whole number"),
            ("recipes/x.media.toml", MEDIA.replace("revision = 2", "revision = true").encode(), "not a whole number"),
            ("recipes/x.media.toml", MEDIA.replace(DIGEST, "sha256:" + "D" * 64).encode(), "is not sha256:"),
            ("recipes/x.media.toml", MEDIA.replace('release = "1.0-720k"', f'release = "{"1" * 126}"').encode(),
             "longer than 128 characters"),
        ):
            with self.subTest(fragment=fragment, path=path):
                with self.assertRaises(recipes.Refusal) as caught:
                    recipes.parse(path, text)
                self.assertIn(fragment, str(caught.exception))

    def test_only_frozen_recipe_format_is_accepted(self):
        for text in (MEDIA, PRIVATE, FIRMWARE):
            path = "recipes/x.firmware.toml" if text == FIRMWARE else "recipes/x.media.toml"
            for declaration in ('recipe = "0.1"', 'recipe = "2.0"', 'recipe = 1.0', ''):
                with self.subTest(path=path, declaration=declaration):
                    with self.assertRaisesRegex(recipes.Refusal, 'recipe must be "1.0"'):
                        recipes.parse(path, text.replace('recipe = "1.0"', declaration).encode())

    def test_private_policy_refusals(self):
        for text, fragment in (
            (PRIVATE + '\n[license]\n', "must have no [license]"),
            (PRIVATE.replace('[input.archive]', '[input.archive]\nurls = ["https://example.invalid/demo"]'),
             "must have no urls"),
            (PRIVATE.replace('[input.licenses]', '[input.licenses]\nurls = []'), "must have no urls"),
            (PRIVATE.replace('name = "licenses.txt"', 'name = "../licenses.txt"'), "beside the recipe"),
            (PRIVATE.replace('name = "licenses.txt"', 'name = "/licenses.txt"'), "beside the recipe"),
            (PRIVATE.replace('name = "licenses.txt"', 'name = "sub/notes.txt"'), "beside the recipe"),
            (PRIVATE.replace('name = "licenses.txt"', 'name = ".."'), "beside the recipe"),
            (PRIVATE.replace('name = "licenses.txt"', 'name = 1'), "beside the recipe"),
            (PRIVATE.replace("size = 1", f"size = {recipes.MAX_BUNDLED_TEXT + 1}"), "text file of at most"),
            (PRIVATE.replace("size = 1", "size = true"), "text file of at most"),
            (PRIVATE.replace("bundle = true", "bundle = true\nfiles = []"), "text file of at most"),
            (MEDIA.replace('[license]\nexpression = "GPL-2.0-or-later"\n\n', ""), "must have [license]"),
        ):
            with self.subTest(fragment=fragment, text=text):
                with self.assertRaisesRegex(recipes.Refusal, re.escape(fragment)):
                    recipes.parse(PRIVATE_PATH, text.encode())

    def test_parse_revision(self):
        for tag, expected in (("1.4-r1", ("1.4", 1)), ("1.4-720k-r12", ("1.4-720k", 12)), ("r3", (None, 3)),
                              ("2.01-ci42-r1", ("2.01-ci42", 1)), ("1.4", None), ("r0", None), ("1.4-r01", None),
                              ("-r1", None), ("1.4--r1", None), ("1.4-r1-r2", None), ("latest", None), ("R1", None)):
            self.assertEqual(recipes.parse_revision(tag), expected, tag)


class Tree(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = pathlib.Path(self._directory.name) / "repo"
        for path, text in (("recipes/media/demo/demo-1.0-720k.media.toml", MEDIA),
                           ("recipes/media/demo/demo.work.toml", 'recipe = "1.0"\n'),
                           ("recipes/media/demo/demo-1.0-720k.media.toml.lock/sha256/x.media.toml", MEDIA),
                           ("recipes/media/.hidden/x.media.toml", MEDIA),
                           ("recipes/firmware/demo-bios.firmware.toml", FIRMWARE)):
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text, encoding="utf-8")
        (self.root / "unemu.toml").write_text(PIN_TOML, encoding="ascii")

    def private_recipe(self, text=PRIVATE):
        path = self.root / PRIVATE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        (path.parent / "licenses.txt").write_bytes(b"x")  # Our synthetic text.
        (path.parent / "demo.work.toml").write_text('recipe = "1.0"\n')
        return path

    def test_private_bundle_bytes(self):
        path = self.private_recipe()
        self.assertEqual(len(recipes.load(self.root)), 3)
        bundle = path.parent / "licenses.txt"
        for data in (b"\xff", b"DOS\0binary", b"x" * (recipes.MAX_BUNDLED_TEXT + 1)):
            with self.subTest(data=data[:10]):
                bundle.write_bytes(data)
                with self.assertRaisesRegex(recipes.Refusal, "UTF-8 text without NUL"):
                    recipes.load(self.root)
        bundle.unlink()
        bundle.symlink_to(self.root / "unemu.toml")
        with self.assertRaisesRegex(recipes.Refusal, "regular text file beside"):
            recipes.load(self.root)
        bundle.unlink()
        with self.assertRaisesRegex(recipes.Refusal, "regular text file beside"):
            recipes.load(self.root)

    def test_private_check_uses_the_same_static_rules(self):
        path = self.private_recipe()
        # An open recipe beside a private one, both pinned.
        firmware = self.root / "recipes/firmware/demo-bios.firmware.toml"
        firmware.write_text(FIRMWARE.replace("revision = 1", f'revision = 1\ndigest = "{DIGEST}"'))
        ok = [f"{p}: ok, recipe" for p in recipes.files_of(self.root, recipes.load(self.root))]
        code, _, stderr, calls = self.check(lines=ok)
        self.assertEqual(code, 0, stderr)
        self.assertIn("3 pinned recipes", stderr)
        self.assertEqual([c[:2] for c in calls], [["--version"], ["recipe", "check"]])
        for lines, status, fragment in (
            (ok + ["  drift: title.synopsis: changed"], 0, "1 drift lines"),
            ([line for line in ok if not line.startswith(str(path) + ":")], 0, "no ok line"),
            ([line for line in ok if not line.startswith(str(path.parent / "demo.work.toml"))], 0, "no ok line"),
            (ok + ["error: rule 8: missing or invalid lock"], 1, "exited with status 1"),
        ):
            code, _, stderr, _ = self.check(lines=lines, status=status)
            self.assertEqual(code, 1, stderr)
            self.assertIn(fragment, stderr)
        path.write_text(PRIVATE.replace(f'digest = "{DIGEST}"\n', ""))
        code, _, stderr, _ = self.check(lines=ok)
        self.assertEqual(code, 1)
        self.assertIn("drafts, which are never published: " + PRIVATE_PATH, stderr)

    def test_find_and_load(self):
        self.assertEqual(recipes.find(self.root), ["recipes/firmware/demo-bios.firmware.toml",
                                                   "recipes/media/demo/demo-1.0-720k.media.toml"])
        self.assertEqual([r.ref for r in recipes.load(self.root)],
                         ["unemu/firmware/ibm-pc/demo-bios:r1", "unemu/media/ibm-pc/demo:1.0-720k-r2"])

    def test_two_recipes_of_one_tag_or_alias(self):
        (self.root / "recipes/media/demo/again.media.toml").write_text(MEDIA, encoding="utf-8")
        with self.assertRaises(recipes.Refusal) as caught:
            recipes.load(self.root)
        self.assertIn("both name unemu/media/ibm-pc/demo:1.0-720k-r2", str(caught.exception))
        # Another revision of one release shares its alias: one release, one
        # recipe, as the newest revision replaces the old one in its file.
        (self.root / "recipes/media/demo/again.media.toml").write_text(MEDIA.replace("revision = 2", "revision = 3"),
                                                                        encoding="utf-8")
        with self.assertRaises(recipes.Refusal) as caught:
            recipes.load(self.root)
        self.assertIn("both name unemu/media/ibm-pc/demo:1.0-720k", str(caught.exception))

    def check(self, *operands, **config):
        bin_dir = pathlib.Path(self._directory.name) / "bin"
        bin_dir.mkdir(exist_ok=True)
        log = bin_dir / "log"
        log.unlink(missing_ok=True)
        (bin_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        cli = bin_dir / "unemu"
        cli.write_text(FAKE_CLI.replace("@PYTHON@", sys.executable).replace("@PIN@", PIN)
                       .replace("@CONFIG@", repr(str(bin_dir / "config.json"))).replace("@LOG@", repr(str(log))),
                       encoding="utf-8")
        cli.chmod(stat.S_IRWXU)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = recipes.main(["check", "--cli", str(cli), *operands], root=self.root,
                                environ={"PATH": os.environ.get("PATH", os.defpath)})
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return code, stdout.getvalue(), stderr.getvalue(), calls

    def test_check(self):
        firmware = "recipes/firmware/demo-bios.firmware.toml"
        # The firmware recipe is a draft.
        code, _, stderr, calls = self.check()
        self.assertEqual(code, 1)
        self.assertIn(f"refused: drafts, which are never published: {firmware}", stderr)
        self.assertEqual(calls, [["--version"], ["recipe", "check", str(self.root / "recipes")]])
        # Only the media recipe: pinned, and unemu passes it and its work
        # file.
        media = str(self.root / "recipes/media/demo")
        ok = [f"{media}/demo-1.0-720k.media.toml: ok, media recipe; required inputs: archive, licenses",
              f"{media}/demo.work.toml: ok, work file"]
        code, stdout, stderr, calls = self.check(media, lines=ok)
        self.assertEqual(code, 0, stderr)
        self.assertIn("recipes: 1 pinned recipes, no finding, no drift", stderr)
        self.assertEqual(calls[1], ["recipe", "check", media])
        # Every recipe and the work file it names needs its own ok line.
        for lines in ([ok[0]], [ok[1]], [], [ok[0].replace(": ok,", ": 1 finding,"), ok[1]],
                      [ok[0].replace("demo-1.0-720k", "other"), ok[1]]):
            code, _, stderr, _ = self.check(media, lines=lines)
            self.assertEqual(code, 1, lines)
            self.assertIn("refused: unemu printed no ok line for recipes/media/demo/", stderr)
        # One recipe given as a file: its work file is given to unemu too,
        # which reports only the files it is given, and needs its ok line.
        recipe = f"{media}/demo-1.0-720k.media.toml"
        code, _, stderr, calls = self.check(recipe, lines=ok)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(calls[1], ["recipe", "check", recipe, f"{media}/demo.work.toml"])
        code, _, stderr, _ = self.check(recipe, lines=ok[:1])
        self.assertEqual(code, 1)
        self.assertIn("refused: unemu printed no ok line for recipes/media/demo/demo.work.toml", stderr)
        # A work file alone names the recipes that name it; they are given
        # to unemu too.
        code, _, stderr, calls = self.check(f"{media}/demo.work.toml", lines=ok)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(calls[1], ["recipe", "check", f"{media}/demo.work.toml", recipe])
        # Given as well, the work file is not given twice.
        code, _, stderr, calls = self.check(recipe, f"{media}/demo.work.toml", lines=ok)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(calls[1], ["recipe", "check", recipe, f"{media}/demo.work.toml"])
        # A finding, drift, another commit.
        code, _, stderr, _ = self.check(media, status=1, lines=ok)
        self.assertEqual(code, 1)
        self.assertIn("unemu recipe check exited with status 1", stderr)
        code, stdout, stderr, _ = self.check(media, lines=ok + ["  drift: title.synopsis: x"])
        self.assertEqual(code, 1)
        self.assertIn("refused: 1 drift lines", stderr)
        self.assertIn("drift: title.synopsis", stdout)
        code, _, stderr, calls = self.check(media, commit="f" * 40)
        self.assertEqual(code, 1)
        self.assertIn("refused: ", stderr)
        self.assertEqual(calls, [["--version"]])
        # Operands that name no recipe, or lie outside the repository.
        for operand in (str(self.root / "docs"), self._directory.name):
            code, _, stderr, calls = self.check(operand)
            self.assertEqual((code, calls), (1, []), operand)
            self.assertIn("refused: ", stderr)


class Repository(unittest.TestCase):
    """This repository's own recipes."""

    def setUp(self):
        self.recipes = recipes.load(ROOT)

    def test_every_recipe_is_pinned_with_its_lock_and_bundled_inputs(self):
        self.assertTrue(self.recipes)
        for recipe in self.recipes:
            with self.subTest(recipe.path):
                self.assertIsNotNone(recipe.digest, "a draft")
                manifest = ROOT / recipe.lock / "sha256" / recipe.digest.split(":")[1]
                self.assertTrue(manifest.is_file(), manifest)
                self.assertEqual(hashlib.sha256(manifest.read_bytes()).hexdigest(), recipe.digest.split(":")[1])
                document = tomllib.loads((ROOT / recipe.path).read_text(encoding="utf-8"))
                for value in document.get("input", {}).values():
                    if value.get("bundle"):
                        data = (ROOT / recipe.folder / value["name"]).read_bytes()
                        self.assertEqual((len(data), hashlib.sha256(data).hexdigest()),
                                         (value["size"], value["sha256"]), value["name"])
                if recipe.work:
                    self.assertTrue((ROOT / recipe.folder / f"{recipe.work}.work.toml").is_file())
                    self.assertEqual(recipe.repository.rsplit("/", 1)[1], recipe.work)
                self.assertEqual(bool(recipe.license), not recipe.private)

    def test_the_archive_script_pins_what_the_freedos_recipes_take(self):
        script = (ROOT / "recipes/media/freedos/source/build.sh").read_text(encoding="utf-8")
        sha256 = re.search(r'^SOURCE_SHA256="([0-9a-f]{64})"$', script, re.M).group(1)
        size = int(re.search(r"^SOURCE_BYTES=([0-9]+)$", script, re.M).group(1))
        name = re.search(r"^SOURCE_NAME=(\S+)$", script, re.M).group(1) + ".tar"
        freedos = [r for r in self.recipes if r.repository == "unemu/media/ibm-pc/freedos"]
        self.assertEqual(len(freedos), 3)
        for recipe in freedos:
            with self.subTest(recipe.path):
                source = tomllib.loads((ROOT / recipe.path).read_text(encoding="utf-8"))["input"]["source"]
                self.assertEqual((source["name"], source["size"], source["sha256"]), (name, size, sha256))
                self.assertEqual(source["urls"], [
                    f"https://github.com/unemu-org/software/releases/download/freedos-1.4-floppy/{name}"])

    def test_the_media_work_files_and_needs_state_only_cited_kinds(self):
        # The maintainer's rule (2026-11-19): needs only as the upstream
        # documents state them. These are the values cited in the work files.
        expected = {
            "freedos": {"video": ["vga", "ega"]},
            "kenslab": {"cpu": "286+", "memory": "512K", "video": ["vga"], "sound": ["sb", "adlib", "none"],
                        "input": ["joystick", "mouse", "none"]},
        }
        for work, needs in expected.items():
            document = tomllib.loads((ROOT / f"recipes/media/{work}/{work}.work.toml").read_text(encoding="utf-8"))
            self.assertEqual(document["needs"], needs, work)


if __name__ == "__main__":
    unittest.main()
