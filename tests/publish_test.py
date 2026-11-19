# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests of lib/publish.py, offline: a throwaway repository with small
recipes, a fake upcli that logs every call it gets, a fake registry read
for the pipeline, and a plain-HTTP registry on 127.0.0.1 for the
anonymous read itself. Nothing here reaches another host or a real
registry.

    python3 -I tests/publish_test.py
"""

import contextlib
import hashlib
import http.server
import importlib.util
import io
import json
import os
import pathlib
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / "lib" / "publish.py"

_spec = importlib.util.spec_from_file_location("publish", SCRIPT)
publish = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = publish
_spec.loader.exec_module(publish)
manifest = publish.manifest

PIN = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"
# A pin of a release that does not exist (lib/upcli.py checks the format).
PIN_TOML = f"""# a comment
commit = "{PIN}"
tag = "v9.9.9"
version = "9.9.9"

[archive.x86_64-unknown-linux-gnu]
file = "unemupc-9.9.9-x86_64-unknown-linux-gnu.tar.gz"
sha256 = "{'a' * 64}"
size = 1
upcli_sha256 = "{'b' * 64}"
upcli_size = 1
"""
# What a release build of upcli prints for --version, in the shape of
# unemupc's crates/upcli/src/about.rs, its commit line as
# crates/platforms/src/lib.rs (commit_line) writes it: the third-party
# notice names a commit of its own.
VERSION = f"""upcli 9.9.9
emulator of period IBM-compatible PCs, command line
licence: AGPL-3.0-or-later
source: https://github.com/unemu-org/unemupc/tree/v9.9.9
commit: {PIN}
full licence texts: LICENSE, and THIRD-PARTY-LICENSES.md

third-party components:

Example 1.0 (upstream commit {OTHER})
source: https://example.invalid/{OTHER}"""
HOST = "registry.invalid"
USER = "synthetic-user"
SECRET = "synthetic-secret-value"
USER_ENV, PASSWORD_ENV, HOST_ENV = publish.USER_ENV, publish.PASSWORD_ENV, publish.HOST_ENV

# The fake upcli: it appends every call (arguments, environment and the
# registries file it was given) to a log, answers as upcli does on the lines
# the pipeline reads (or as the test's configuration says, to check that the
# pipeline notices), and on a push records the target in a JSON file the
# fake registry reads. A firmware import records the statement it was given
# in another JSON file, from which `image firmware show` answers as upcli at
# the pinned commit prints a package (crates/cli-pc/src/image/firmware.rs).
FAKE_CLI = r'''#!@PYTHON@ -I
import hashlib, json, os, sys

with open(@CONFIG@) as handle:
    config = json.load(handle)
argv = sys.argv[1:]
entry = {"argv": argv, "env": dict(os.environ)}
if "--config" in argv:
    with open(argv[argv.index("--config") + 1]) as handle:
        entry["config"] = handle.read()
with open(@LOG@, "a") as handle:
    handle.write(json.dumps(entry) + "\n")


def digest_of(ref):
    return config["digests"].get(ref) or "sha256:" + hashlib.sha256(ref.encode()).hexdigest()


if argv == ["--version"]:
    if config.get("version") is None:
        print("unknown or extra arguments", file=sys.stderr)
        sys.exit(2)
    print(config["version"])
    sys.exit(0)
if argv[:3] == ["image", "media", "import"]:
    ref = argv[argv.index("--tag") + 1]
    if ref in config.get("fail_import", []):
        print("import refused: synthetic", file=sys.stderr)
        sys.exit(1)
    print("media order:")
    if config.get("import_raw"):
        sys.stdout.flush()
        sys.stdout.buffer.write(config["import_raw"].encode("latin-1"))
        sys.exit(0)
    print(config.get("import_line", ref + "\t" + digest_of(ref)))
    sys.exit(0)
if argv[:3] == ["image", "media", "show"]:
    print(config.get("show_first", argv[3] + "\t" + digest_of(argv[3]) + "\tibm-pc"))
    print(config.get("push_line", "push\tany configured registry, including unemu/"))
    for line in config.get("show_after", []):
        print(line)
    sys.exit(0)


def value(flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


def sha256_of(path):
    with open(path, "rb") as handle:
        data = handle.read()
    return hashlib.sha256(data).hexdigest(), len(data)


def debug(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


if argv[:3] == ["image", "firmware", "import"]:
    tag = "private/firmware/ibm-pc/" + value("--for") + ":r1"
    if tag in config.get("fail_import", []):
        print("selected catalogue entry has no complete unambiguous package", file=sys.stderr)
        sys.exit(1)
    packages = {}
    if os.path.exists(@FIRMWARE@):
        with open(@FIRMWARE@) as handle:
            packages = json.load(handle)
    packages[tag] = {
        "rom": argv[3], "source": value("--source"), "title": value("--title"),
        "description": value("--description"), "vendor": value("--vendor"), "version": value("--version"),
        "labels": [argv[i + 1] for i, a in enumerate(argv) if a == "--label"],
        "license": value("--license") if "--open" in argv else None,
    }
    with open(@FIRMWARE@, "w") as handle:
        json.dump(packages, handle)
    if "firmware_import" in config:
        for line in config["firmware_import"]:
            print(line)
        sys.exit(0)
    print("complete\t" + tag + "\t" + sha256_of(argv[3])[0])
    print("imported\t" + tag + "\t" + digest_of(tag))
    sys.exit(0)
if argv[:3] == ["image", "firmware", "show"]:
    tag = argv[3]
    with open(@FIRMWARE@) as handle:
        package = json.load(handle)[tag]
    rom, rom_size = sha256_of(package["rom"])
    source, source_size = sha256_of(package["source"])
    open_license ="open\t" + package["license"] if package["license"] else "user-supplied\t-"
    lines = {
        "first": tag + "\t" + digest_of(tag) + "\t" + debug(package["title"]),
        "fits": 'fits\t{"boards": Array([String("ibm-5170")]), "machine_classes": Array([String("at")])}',
        "rom": "system-bios\tsystem-bios\t" + str(rom_size) + "\t" + rom,
        "requires": 'requires\tsystem-bios\t["kbc-8042"]',
        "vendor": "vendor\t" + debug(package["vendor"]),
        "version": "version\t" + debug(package["version"]),
        "description": "description\t" + debug(package["description"]),
        "labels": "labels\t[" + ", ".join(debug(label) for label in sorted(package["labels"])) + "]",
        "provenance": 'provenance\tunstated\t"synthetic"\trevision:-\turl:-\tdate:-',
        "source": "source\t" + debug(os.path.basename(package["source"])) + "\ttar\t" + str(source_size)
                  + "\tsha256:" + source + '\t["system-bios"]',
        "readme": "readme\tsha256:" + "e" * 64 + "\t1000",
        "layer config": "layer\tconfig\tsha256:" + "f" * 64 + "\topen\tCC0-1.0",
        "layer rom": "layer\trom\tsha256:" + rom + "\t" + open_license,
        "layer source": "layer\tsource\tsha256:" + source + "\t" + open_license,
        "layer readme": "layer\treadme\tsha256:" + "e" * 64 + "\topen\tCC0-1.0",
    }
    for key, line in config.get("firmware_show", {}).items():
        if line is None:
            del lines[key]
        else:
            lines[key] = line
    for line in lines.values():
        print(line)
    print(config.get("push_line", "push\tany configured registry, including unemu/"))
    sys.exit(0)
if argv[:2] == ["image", "push"]:
    ref, target = argv[2], argv[3]
    if "--dry-run" in argv:
        print("target:" + config.get("dry_target", target))
        print("image:" + config.get("dry_image", digest_of(ref)))
        if config.get("guard") == "refused":
            print("guard:refused")
            print("push refused: synthetic", file=sys.stderr)
            sys.exit(1)
        print(config.get("guard_line", "guard:allowed"))
        sys.exit(config.get("dry_status", 0))
    if target in config.get("fail_push", []):
        print("push refused: synthetic", file=sys.stderr)
        sys.exit(1)
    if not config.get("push_noop"):
        held = {}
        if os.path.exists(@REGISTRY@):
            with open(@REGISTRY@) as handle:
                held = json.load(handle)
        held[target] = digest_of(ref)
        with open(@REGISTRY@, "w") as handle:
            json.dump(held, handle)
    print("uploaded\t" + digest_of(ref))
    word = config.get("push_word", "pushed")
    print(word + "\t" + config.get("push_target", target) + "\t" + config.get("push_digest", digest_of(ref)))
    sys.exit(0)
print("unknown or extra arguments", file=sys.stderr)
sys.exit(2)
'''

# A recipe whose build only records its environment; its outputs are
# written by the test.
BUILD_SH = """#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env > "$here/build-env.txt"
if [ -e "$here/fail" ]; then
\techo "synthetic build failure" >&2
\texit 3
fi
echo "every output matches its pin"
"""

MANIFEST = """[release]
work = "@WORK@"
version = "@VERSION@"
default_format = "1.44M"
publishers = ["Test Publisher"]
developers = ["Test Developer"]
year = 1990
genres = ["utility"]
platform = "DOS"
labels = ["utility", "open-source"]
license = "GPL-2.0-or-later"
terms = "LICENSE.TXT"
source = "src.tar"

[[release.extra]]
path = "LICENSE.TXT"
sha256 = "@LICENSE@"
dos_name = "LICENSE.TXT"
kind = "document"

[[release.extra]]
path = "src.tar"
sha256 = "@SOURCE@"
dos_name = "SRC.TAR"
kind = "archive"

[[set]]
title = "Test"
description = "A test set on 1.44M disks."
format = "1.44M"
@DIGEST1@
[[set.disk]]
path = "144m/a.img"
sha256 = "@A@"
label = "Disk 1 of 1"

[[set]]
title = "Test"
description = "A test set on 720K disks."
format = "720K"
@DIGEST2@
[[set.disk]]
path = "720k/b.img"
sha256 = "@B@"
label = "Disk 1 of 1"
"""

OUTPUTS = {
    "LICENSE.TXT": b"licence text\r\n",
    "src.tar": b"source archive bytes",
    "144m/a.img": b"disk a",
    "720k/b.img": b"disk b",
}

# A package manifest; the description holds a double quote and a backslash,
# which upcli's show escapes, and the labels are not sorted, as the show
# lists them.
FIRMWARE_MANIFEST = r"""[package]
catalogue_id = "@ID@"
revision = @REVISION@
@DIGEST@title = "Demo BIOS"
description = "A synthetic \"demo\" BIOS with a back\\slash."
vendor = "Test Vendor"
version = "1.0"
labels = ["system-bios", "open-source"]
license = "GPL-2.0-or-later"

[rom]
path = "demo.bin"
sha256 = "@ROM@"

[source]
path = "demo-src.tar"
sha256 = "@SOURCE@"
"""

FIRMWARE_OUTPUTS = {
    "demo.bin": b"\x55\xaa" + b"\x00" * 62,
    "demo-src.tar": b"firmware source archive bytes",
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def fake_digest(ref):
    """The digest the fake upcli gives REF unless told otherwise."""
    return "sha256:" + sha256(ref.encode())


# The demo recipe's repository, its two sets' revision tags and their
# release aliases, and the demo package's repository and store tag.
MEDIA = "unemu/media/ibm-pc/demo"
T1, T2 = "2.0-r1", "2.0-720k-r1"
A1, A2 = "2.0", "2.0-720k"
FIRMWARE_REPOSITORY = "unemu/firmware/ibm-pc/demo-bios"
FIRMWARE_STORE_TAG = "private/firmware/ibm-pc/demo-bios:r1"


class FakeRegistry:
    """The anonymous registry read: what the test put there, plus what the
    fake upcli pushed."""

    def __init__(self, pushed):
        self.pushed = pushed
        self.held = {}
        self.calls = []
        self.error = None
        # Errors to raise on the Nth read (from 1), whatever the tag.
        self.errors = {}

    def __call__(self, host, repository, tag):
        self.calls.append((host, repository, tag))
        if len(self.calls) in self.errors:
            raise self.errors[len(self.calls)]
        if self.error:
            raise publish.RegistryError(self.error)
        target = f"{host}/{repository}:{tag}"
        pushed = json.loads(self.pushed.read_text()) if self.pushed.exists() else {}
        return pushed.get(target, self.held.get(target))


class Repository:
    """A throwaway repository root with recipes, outputs and a fake CLI."""

    def __init__(self, directory):
        self.base = pathlib.Path(directory)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / publish.PIN_FILE).write_text(PIN_TOML, encoding="ascii")
        self.log = self.base / "cli-log.jsonl"
        self.config = self.base / "cli-config.json"
        self.pushed = self.base / "pushed.json"
        self.packages = self.base / "packages.json"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.cli = self.bin / "upcli"
        self.configure()
        text = FAKE_CLI
        for name, value in (("@PYTHON@", sys.executable), ("@CONFIG@", repr(str(self.config))),
                            ("@LOG@", repr(str(self.log))), ("@REGISTRY@", repr(str(self.pushed))),
                            ("@FIRMWARE@", repr(str(self.packages)))):
            text = text.replace(name, value)
        self.cli.write_text(text, encoding="utf-8")
        self.cli.chmod(stat.S_IRWXU)
        self.registry = FakeRegistry(self.pushed)

    def configure(self, **settings):
        settings.setdefault("digests", {})
        settings.setdefault("version", VERSION)
        self.config.write_text(json.dumps(settings), encoding="utf-8")

    def recipe(self, name, work="demo", pins=("match", "match"), version="2.0"):
        """A recipe for WORK with two sets, :VERSION-r1 and :VERSION-720k-r1;
        each pin is 'match' (what the fake CLI gives), None or a digest."""
        recipe_dir = self.root / "recipes" / name
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "build.sh").write_text(BUILD_SH, encoding="utf-8")
        text = MANIFEST.replace("@WORK@", work).replace("@VERSION@", version)
        for key, path in (("@LICENSE@", "LICENSE.TXT"), ("@SOURCE@", "src.tar"),
                          ("@A@", "144m/a.img"), ("@B@", "720k/b.img")):
            text = text.replace(key, sha256(OUTPUTS[path]))
        for key, pin, tag in (("@DIGEST1@", pins[0], f"{version}-r1"), ("@DIGEST2@", pins[1], f"{version}-720k-r1")):
            if pin == "match":
                pin = fake_digest(f"unemu/media/ibm-pc/{work}:{tag}")
            text = text.replace(key, f'digest = "{pin}"\n' if pin else "")
        (recipe_dir / "media.toml").write_text(text, encoding="utf-8")
        out = self.root / "out" / name
        for path, data in OUTPUTS.items():
            (out / path).parent.mkdir(parents=True, exist_ok=True)
            (out / path).write_bytes(data)
        return recipe_dir

    def firmware_recipe(self, name, catalogue_id="demo-bios", revision=1, pin="match"):
        """A recipe for the package unemu/firmware/ibm-pc/CATALOGUE_ID:rREVISION;
        PIN is 'match' (what the fake CLI gives), None or a digest."""
        recipe_dir = self.root / "recipes" / name
        recipe_dir.mkdir(parents=True)
        (recipe_dir / "build.sh").write_text(BUILD_SH, encoding="utf-8")
        if pin == "match":
            pin = fake_digest(f"private/firmware/ibm-pc/{catalogue_id}:r1")
        text = (FIRMWARE_MANIFEST.replace("@ID@", catalogue_id).replace("@REVISION@", str(revision))
                .replace("@DIGEST@", f'digest = "{pin}"\n' if pin else "")
                .replace("@ROM@", sha256(FIRMWARE_OUTPUTS["demo.bin"]))
                .replace("@SOURCE@", sha256(FIRMWARE_OUTPUTS["demo-src.tar"])))
        (recipe_dir / "firmware.toml").write_text(text, encoding="utf-8")
        out = self.root / "out" / name
        out.mkdir(parents=True)
        for path, data in FIRMWARE_OUTPUTS.items():
            (out / path).write_bytes(data)
        return recipe_dir

    def environ(self, push=False, **extra):
        env = {"PATH": os.environ.get("PATH", os.defpath), HOST_ENV: HOST, "KEPT_FOR_RECIPES": "yes"}
        if push:
            env.update({USER_ENV: USER, PASSWORD_ENV: SECRET})
        env.update(extra)
        return {k: v for k, v in env.items() if v is not None}

    def run(self, *args, environ=None, push=False, cli=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = publish.main(
                ["--cli", cli or str(self.cli), *args],
                root=self.root,
                registry=self.registry,
                environ=environ if environ is not None else self.environ(push=push or "--push" in args),
            )
        return code, stdout.getvalue(), stderr.getvalue()

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def pushes(self):
        return [c for c in self.calls() if c["argv"][:2] == ["image", "push"] and "--dry-run" not in c["argv"]]


class Case(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.repo = Repository(self._directory.name)

    def tearDown(self):
        self._directory.cleanup()

    def row(self, stdout, repository, tag):
        """The summary row of REPOSITORY:TAG, split at white space:
        repository, tag, alias, digest, outcome and licence."""
        for line in stdout.splitlines():
            cells = line.split()
            if cells[:2] == [repository, tag]:
                return cells
        self.fail(f"no row for {repository}:{tag} in\n{stdout}")

    def outcome(self, stdout, tag, repository=MEDIA):
        return self.row(stdout, repository, tag)[4]


class Arguments(Case):
    def test_calls_follow_the_manifest(self):
        self.repo.recipe("demo")
        code, stdout, stderr = self.repo.run()
        self.assertEqual(code, 0, stderr)
        calls = self.repo.calls()
        self.assertEqual(calls[0]["argv"], ["--version"])
        imports = [c["argv"] for c in calls if c["argv"][:3] == ["image", "media", "import"]]
        self.assertEqual(len(imports), 2)
        store = imports[0][imports[0].index("--store") + 1]
        self.assertTrue(store.startswith(str(self.repo.root / "build" / "publish.")), store)
        checked = manifest.load(self.repo.root / "recipes" / "demo" / "media.toml")
        statement = manifest.open_statement(checked)
        out_dir = str(self.repo.root / "out" / "demo")
        for argv, tag in zip(imports, (T1, T2)):
            request = manifest.import_request(checked, tag, out_dir)
            self.assertEqual(argv, manifest.import_arguments(request, statement) + ["--store", store, "--create"])
        shows = [c["argv"] for c in calls if c["argv"][:3] == ["image", "media", "show"]]
        self.assertEqual(shows, [["image", "media", "show", f"{MEDIA}:{t}", "--store", store]
                                 for t in (T1, T2)])
        # A dry run of each set's revision tag, then of its alias.
        dry = [c for c in calls if "--dry-run" in c["argv"]]
        self.assertEqual([c["argv"][2:4] for c in dry], [
            [f"{MEDIA}:{T1}", f"{HOST}/{MEDIA}:{T1}"], [f"{MEDIA}:{T1}", f"{HOST}/{MEDIA}:{A1}"],
            [f"{MEDIA}:{T2}", f"{HOST}/{MEDIA}:{T2}"], [f"{MEDIA}:{T2}", f"{HOST}/{MEDIA}:{A2}"],
        ])
        argv = dry[0]["argv"]
        self.assertEqual(argv[:8], ["image", "push", f"{MEDIA}:{T1}", f"{HOST}/{MEDIA}:{T1}",
                                    "--store", store, "--dry-run", "--config"])
        self.assertEqual(
            dry[0]["config"],
            "# lib/publish.py: the target registry for one run, public, unemu/ only.\n"
            "[[registries]]\n"
            f'host = "{HOST}"\n'
            'namespaces = [{ name = "unemu", private = false }]\n',
        )
        # No registry is read and nothing is pushed without --push.
        self.assertEqual(self.repo.registry.calls, [])
        self.assertEqual(self.repo.pushes(), [])
        # The work directory, store included, is gone.
        self.assertEqual(list((self.repo.root / "build").iterdir()), [])

    def test_upcli_environment_is_its_own(self):
        self.repo.recipe("demo")
        code, _, stderr = self.repo.run("--push")
        self.assertEqual(code, 0, stderr)
        pushes = self.repo.pushes()
        for call in self.repo.calls():
            # macOS adds its own __CF_USER_TEXT_ENCODING to every process.
            names = sorted(k for k in call["env"] if not k.startswith("__CF_"))
            expected = ["HOME", "LC_ALL", "PATH", "TMPDIR", "TZ"]
            if call in pushes:
                expected = sorted(expected + [USER_ENV, PASSWORD_ENV, HOST_ENV])
            self.assertEqual(names, expected, call["argv"])
            self.assertEqual((call["env"]["LC_ALL"], call["env"]["TZ"]), ("C", "UTC"))
            self.assertTrue(call["env"]["HOME"].startswith(str(self.repo.root / "build" / "publish.")))

    def test_recipe_environment_hides_registry_and_secret_variables(self):
        self.repo.recipe("demo")
        hidden = {
            "UNEMU_REGISTRY": "registry.invalid",
            "UNEMU_EXPERIMENTAL_PASSWORD": "x",
            "UNEMU_PROMOTE_PASSWORD": "x",
            "UNEMUPC_USER_ROMS_DIR": "x",
            "unemu_lower": "x",
            "ACTIONS_RUNTIME_TOKEN": "x",
            "GITHUB_TOKEN": "x",
            "my_secret_value": "x",
            "GIT_CREDENTIAL_HELPER": "x",
            "Db_Password": "x",
        }
        environ = self.repo.environ(push=True, **hidden)
        code, _, stderr = self.repo.run("--push", environ=environ)
        self.assertEqual(code, 0, stderr)
        build_env = (self.repo.root / "recipes" / "demo" / "build-env.txt").read_text()
        names = {line.split("=", 1)[0] for line in build_env.splitlines() if "=" in line}
        self.assertIn("KEPT_FOR_RECIPES", names)
        self.assertIn("PATH", names)
        for name in [USER_ENV, PASSWORD_ENV, HOST_ENV, *hidden]:
            self.assertNotIn(name, names)
        self.assertNotIn(SECRET, build_env)
        self.assertEqual(
            sorted(publish.recipe_environment({"A": "1", "UNEMU_X": "2", "B_TOKEN_C": "3", "Kept": "4"})),
            ["A", "Kept"],
        )

    def test_recipe_environment_hides_the_ci_runner(self):
        # What would let a recipe reach past its own run on a GitHub runner:
        # the files later steps read (GITHUB_ENV, GITHUB_PATH, GITHUB_OUTPUT,
        # GITHUB_STATE), the runner's directories and settings, and the
        # start-up files bash and sh read (BASH_ENV, ENV).
        self.repo.recipe("demo")
        marker = self.repo.base / "sourced"
        startup = self.repo.base / "startup.sh"
        startup.write_text(f"touch '{marker}'\n", encoding="utf-8")
        hidden = {
            "GITHUB_ENV": "x", "GITHUB_PATH": "x", "GITHUB_OUTPUT": "x", "GITHUB_STATE": "x",
            "GITHUB_STEP_SUMMARY": "x", "GITHUB_WORKSPACE": "x", "github_lower": "x",
            "RUNNER_TEMP": "x", "RUNNER_TOOL_CACHE": "x", "ACTIONS_ID_TOKEN_REQUEST_URL": "x",
            "ACTIONS_CACHE_URL": "x", "BASH_ENV": str(startup), "ENV": str(startup),
        }
        kept = {"MY_GITHUB_NOTE": "x", "ENVIRONMENT": "x", "XENV": "x", "BASH_ENVX": "x", "CI": "true"}
        code, _, stderr = self.repo.run(environ=self.repo.environ(**hidden, **kept))
        self.assertEqual(code, 0, stderr)
        build_env = (self.repo.root / "recipes" / "demo" / "build-env.txt").read_text()
        names = {line.split("=", 1)[0] for line in build_env.splitlines() if "=" in line}
        for name in hidden:
            self.assertNotIn(name, names)
        for name in kept:
            self.assertIn(name, names)
        # bash never read the start-up file.
        self.assertFalse(marker.exists())
        self.assertEqual(sorted(publish.recipe_environment({**hidden, **kept})), sorted(kept))

    def test_selection(self):
        self.repo.recipe("demo")
        self.repo.recipe("other", work="other")
        code, stdout, stderr = self.repo.run(f"{MEDIA}:{T2}")
        self.assertEqual(code, 0, stderr)
        imports = [c["argv"] for c in self.repo.calls() if c["argv"][:3] == ["image", "media", "import"]]
        self.assertEqual([a[a.index("--tag") + 1] for a in imports], [f"{MEDIA}:{T2}"])
        self.assertFalse((self.repo.root / "recipes" / "other" / "build-env.txt").exists())

        self.repo.log.unlink(missing_ok=True)
        code, stdout, stderr = self.repo.run("other")
        self.assertEqual(code, 0, stderr)
        imports = [c["argv"] for c in self.repo.calls() if c["argv"][:3] == ["image", "media", "import"]]
        self.assertEqual([a[a.index("--tag") + 1] for a in imports],
                         ["unemu/media/ibm-pc/other:2.0-r1", "unemu/media/ibm-pc/other:2.0-720k-r1"])

        self.repo.log.unlink(missing_ok=True)
        code, stdout, stderr = self.repo.run()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(len([c for c in self.repo.calls() if "import" in c["argv"][:3]]), 4)

        # An alias, a path without the platform and the tags of earlier
        # releases select nothing.
        for selector in ("nothing", f"{MEDIA}:9.9-r1", "demo:2.0-r1", f"{MEDIA}:{A1}", "unemu/media/demo:2.0-r1",
                         "unemu/media/demo:2.0"):
            self.repo.log.unlink(missing_ok=True)
            code, _, stderr = self.repo.run(selector)
            self.assertEqual(code, 2, selector)
            self.assertIn("neither a recipe nor a ref (unemu/media/ibm-pc/<work>:<release>-rN or "
                          "unemu/firmware/ibm-pc/<id>:rN)", stderr)
            self.assertEqual(self.repo.calls(), [])

    def test_usage_errors(self):
        self.repo.recipe("demo")
        existing = self.repo.base / "existing"
        existing.mkdir()
        for args in (("--store", str(existing)), ("--experimental", "4a"), ("--experimental", ""),
                     ("--experimental", "-1"), ("--experimental", "0"), ("--experimental", "01"),
                     ("--experimental", "1" * 21), ("--push", "--allow-unpinned"), ("--bogus",)):
            code, _, _ = self.repo.run(*args)
            self.assertEqual(code, 2, args)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = publish.main([], root=self.repo.root, registry=self.repo.registry,
                                environ=self.repo.environ())
        self.assertEqual(code, 2)
        self.assertIn("--cli UPCLI", stderr.getvalue())
        self.assertEqual(self.repo.calls(), [])
        for run_number in ("1", "9", "10", "9" * 20):
            self.assertTrue(publish.RUN.fullmatch(run_number), run_number)

    def test_registry_host_is_checked_as_upcli_checks_it(self):
        self.repo.recipe("demo")
        for text in ("registry", "https://registry.invalid", "registry.invalid/x", "registry.invalid:",
                     "registry.invalid:65536", "registry.invalid:99999", "registry.invalid:x",
                     ":5000", "-registry.invalid", "registry-.invalid", "registry..invalid",
                     "reg_istry.invalid", "[::1", "[]:5000", "[::1]x", "[::g]", "r\u00e9gistry.invalid"):
            self.assertIsNone(publish.registry_host(text), text)
            code, _, stderr = self.repo.run(environ=self.repo.environ(**{HOST_ENV: text}))
            self.assertEqual(code, 2, text)
            self.assertIn(HOST_ENV, stderr)
        for text, host in (("registry.invalid", "registry.invalid"), (" Registry.Invalid ", "registry.invalid"),
                           ("localhost", "localhost"), ("localhost:5000", "localhost:5000"),
                           ("registry:5000", "registry:5000"), ("registry.invalid:65535", "registry.invalid:65535"),
                           ("127.0.0.1:5000", "127.0.0.1:5000"), ("[::1]:5000", "[::1]:5000"), ("[::1]", "[::1]")):
            self.assertEqual(publish.registry_host(text), host, text)
        self.assertEqual(self.repo.calls(), [])

    def test_a_cli_name_is_looked_up_on_path(self):
        self.repo.recipe("demo")
        environ = self.repo.environ(PATH=f"{self.repo.bin}{os.pathsep}{os.environ.get('PATH', '')}")
        code, _, stderr = self.repo.run(environ=environ, cli="upcli")
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"upcli {self.repo.cli}", stderr)
        self.repo.log.unlink()
        code, _, stderr = self.repo.run(cli="upcli-not-on-path")
        self.assertEqual(code, 2)
        self.assertIn("not found on PATH", stderr)
        self.assertEqual(self.repo.calls(), [])

    def test_given_store_is_kept(self):
        self.repo.recipe("demo")
        store = self.repo.base / "kept-store"
        code, _, stderr = self.repo.run("--store", str(store))
        self.assertEqual(code, 0, stderr)
        imports = [c["argv"] for c in self.repo.calls() if c["argv"][:3] == ["image", "media", "import"]]
        self.assertEqual({a[a.index("--store") + 1] for a in imports}, {str(store.resolve())})
        self.assertEqual(list((self.repo.root / "build").iterdir()), [])


class Digests(Case):
    def test_pinned_digests_match(self):
        self.repo.recipe("demo")
        code, stdout, stderr = self.repo.run()
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"registry {HOST} (dry run)", stdout)
        self.assertEqual(stdout.splitlines()[1].split(), ["REPOSITORY", "TAG", "ALIAS", "DIGEST", "OUTCOME", "LICENCE"])
        for tag, alias in ((T1, A1), (T2, A2)):
            row = self.row(stdout, MEDIA, tag)
            self.assertEqual(row[2:4], [alias, fake_digest(f"{MEDIA}:{tag}")])
            self.assertEqual(row[4:], ["dry", "run", "GPL-2.0-or-later"])
        self.assertIn(f"upcli pin {PIN} (unemupc v9.9.9, upcli.toml)", stderr)
        self.assertIn(f"upcli --version reports commit {PIN}, as pinned", stderr)

    def test_mismatch_fails(self):
        self.repo.recipe("demo", pins=("match", "sha256:" + "e" * 64))
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "dry")
        self.assertEqual(self.outcome(stdout, T2), "mismatch")
        self.assertIn(f"the manifest pins sha256:{'e' * 64}", stdout)

    def test_unpinned_fails_unless_allowed_and_is_never_pushed(self):
        self.repo.recipe("demo", pins=("match", None))
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        row = self.row(stdout, MEDIA, T2)
        self.assertEqual(row[3:5], [fake_digest(f"{MEDIA}:{T2}"), "unpinned"])

        code, stdout, stderr = self.repo.run("--allow-unpinned")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T2), "unpinned")
        self.assertEqual(self.outcome(stdout, T1), "dry")

        # With --push, one unpinned set holds every push.
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "held")
        self.assertEqual(self.outcome(stdout, T2), "unpinned")
        self.assertEqual(self.repo.pushes(), [])
        self.assertEqual(self.repo.registry.calls, [])

    def test_outputs_are_checked_against_their_pins(self):
        self.repo.recipe("demo")
        (self.repo.root / "out" / "demo" / "720k" / "b.img").write_bytes(b"changed")
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertIn("output 720k/b.img: SHA-256", stdout)
        self.assertEqual([c for c in self.repo.calls() if c["argv"][:1] == ["image"]], [])

    def test_a_failed_build_fails_its_sets(self):
        recipe_dir = self.repo.recipe("demo")
        (recipe_dir / "fail").touch()
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertIn("build.sh exited with status 3", stdout)

    def test_guard_and_media_show_must_allow_unemu(self):
        self.repo.recipe("demo")
        self.repo.configure(guard="refused")
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertIn("push --dry-run", stdout)
        self.repo.configure(push_line="push\tprivate registries outside unemu/ only: user-supplied layers")
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertIn("media show: the last line is 'push\\tprivate registries", stdout)
        # The pushable line must be the last one.
        self.repo.configure(show_after=["push\tprivate registries outside unemu/ only: user-supplied layers"])
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertIn("media show: the last line", stdout)
        self.repo.configure(fail_import=[f"{MEDIA}:{T1}"])
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertIn("import: import refused: synthetic", stdout)

    def test_the_cli_must_name_the_same_ref_target_and_digest(self):
        self.repo.recipe("demo")
        ref = f"{MEDIA}:{T1}"
        digest = fake_digest(ref)
        other = "sha256:" + "c" * 64
        for settings, fragment in (
            ({"import_line": f"{MEDIA}:9.9-r1\t{digest}"}, "import: unexpected last line"),
            # The alias the store moved with the revision is not the ref.
            ({"import_line": f"{MEDIA}:{A1}\t{digest}"}, "import: unexpected last line"),
            ({"import_line": f"{ref}\tsha256:{'C' * 64}"}, "import: unexpected last line"),
            ({"import_line": f"{ref}\t{digest}\textra"}, "import: unexpected last line"),
            ({"show_first": f"{MEDIA}:9.9-r1\t{digest}\tibm-pc"}, "media show: the first line"),
            ({"show_first": f"{ref}\t{other}\tibm-pc"}, "media show: the first line"),
            ({"show_first": f"{ref}\t{digest}"}, "media show: the first line"),
            ({"dry_target": f"{HOST}/{MEDIA}:9.9-r1"}, "the plan names another target or image"),
            # One target for both dry runs: the alias's names another.
            ({"dry_target": f"{HOST}/{ref}"}, f"push --dry-run {HOST}/{MEDIA}:{A1}: the plan names another"),
            ({"dry_image": other}, "the plan names another target or image"),
            ({"dry_status": 1}, "push --dry-run"),
            # A guard that does not say allowed fails even when upcli exits 0.
            ({"guard_line": "guard:refused"}, "push --dry-run"),
            ({"guard_line": "guard:allowed but"}, "push --dry-run"),
        ):
            self.repo.configure(**settings)
            code, stdout, _ = self.repo.run(ref)
            self.assertEqual(code, 1, settings)
            self.assertEqual(self.outcome(stdout, T1), "failed", settings)
            self.assertIn(fragment, stdout, settings)

    def test_manifests_are_checked_first(self):
        self.repo.recipe("demo")
        self.repo.recipe("again", work="demo")
        code, _, stderr = self.repo.run("demo")
        self.assertEqual(code, 1)
        self.assertIn(f"{MEDIA}:{T1} is also", stderr)
        self.assertEqual(self.repo.calls(), [])


class Pin(Case):
    def test_pin_file(self):
        root = self.repo.root
        pin = publish.read_pin(root)
        self.assertEqual((pin.commit, pin.tag, pin.version), (PIN, "v9.9.9", "9.9.9"))
        # tests/upcli_test.py tests the format; here, that a refused pin
        # stops the run before upcli runs.
        for text in ("", "# only a comment\n", PIN_TOML.replace(PIN, PIN[:12]),
                     PIN_TOML.replace(PIN, PIN.upper()), f'commit = "{PIN}"\n' + PIN_TOML,
                     PIN_TOML.replace("tag = ", "branch = "), PIN + "\n"):
            (root / publish.PIN_FILE).write_text(text, encoding="ascii")
            with self.assertRaises(publish.Failure):
                publish.read_pin(root)
        self.repo.recipe("demo")
        code, _, stderr = self.repo.run()
        self.assertEqual(code, 1)
        self.assertIn(f"refused: {publish.PIN_FILE}: ", stderr)
        self.assertEqual(self.repo.calls(), [])
        (root / publish.PIN_FILE).unlink()
        code, _, stderr = self.repo.run()
        self.assertEqual(code, 1)
        self.assertIn(f"refused: {publish.PIN_FILE}: cannot read", stderr)

    def test_the_commit_line_names_the_pinned_commit(self):
        recipe_dir = self.repo.recipe("demo")
        code, _, stderr = self.repo.run()
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"reports commit {PIN}, as pinned", stderr)
        (recipe_dir / "build-env.txt").unlink()
        self.repo.log.unlink()
        refused = (
            # Another commit.
            (VERSION.replace(f"commit: {PIN}", f"commit: {OTHER}"), f"reports commit {OTHER}; upcli.toml pins {PIN}"),
            # No commit line: a source build without UNEMUPC_SOURCE_COMMIT,
            # or upcli before 0.0.2; a hash anywhere else does not count.
            (VERSION.replace(f"commit: {PIN}\n", ""), "prints no commit line"),
            (f"upcli 0.1.0 ({PIN})", "prints no commit line"),
            (f"source: https://github.com/unemu-org/unemupc/tree/{PIN}", "prints no commit line"),
            (f" commit: {PIN}", "prints no commit line"),
            (f"Commit: {PIN}", "prints no commit line"),
            # A malformed or repeated commit line.
            (f"commit: {PIN.upper()}", "prints the commit line"),
            (f"commit: {PIN[:12]}", "prints the commit line"),
            (f"commit: {PIN}0", "prints the commit line"),
            (f"commit:{PIN}", "prints the commit line"),
            (f"commit:  {PIN}", "prints the commit line"),
            (f"commit: {PIN} ", "prints the commit line"),
            (f"commit: {PIN} (v9.9.9)", "prints the commit line"),
            (f"commit: {PIN}\ncommit: {PIN}", "prints 2 commit lines"),
            (f"commit: {PIN}\ncommit: {OTHER}", "prints 2 commit lines"),
            # --version fails.
            (None, "--version failed: unknown or extra arguments"),
        )
        for version, fragment in refused:
            self.repo.configure(version=version)
            for args in ((), ("--push",)):
                code, stdout, stderr = self.repo.run(*args)
                self.assertEqual(code, 1, (version, args))
                self.assertIn("refused: ", stderr, version)
                self.assertIn(fragment, stderr, version)
                self.assertEqual(stdout, "", version)
                # Nothing is built, imported or pushed, and no registry is read.
                self.assertEqual([c["argv"] for c in self.repo.calls()], [["--version"]], version)
                self.assertFalse((recipe_dir / "build-env.txt").exists(), version)
                self.assertEqual(self.repo.registry.calls, [])
                self.assertEqual(list((self.repo.root / "build").iterdir()), [])
                self.repo.log.unlink()

    def test_reported_commit(self):
        self.assertEqual(publish.upcli.reported_commit(VERSION), PIN)
        self.assertEqual(publish.upcli.reported_commit(VERSION + "\r\n"), PIN)
        self.assertIsNone(publish.upcli.reported_commit(""))
        self.assertIsNone(publish.upcli.reported_commit(f"upcli 0.0.1\nsource: x/tree/{PIN}\n"))


class Push(Case):
    def test_pushes_a_missing_tag_with_credentials_only_in_its_environment(self):
        self.repo.recipe("demo")
        code, stdout, stderr = self.repo.run("--push")
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"registry {HOST} (push)", stdout)
        pushes = self.repo.pushes()
        # Each set's revision tag, then its alias, both from the revision's
        # store tag.
        self.assertEqual([p["argv"] for p in pushes], [
            ["image", "push", f"{MEDIA}:{t}", f"{HOST}/{MEDIA}:{target}",
             "--store", pushes[0]["argv"][5], "--config", pushes[0]["argv"][7]]
            for t, target in ((T1, T1), (T1, A1), (T2, T2), (T2, A2))
        ])
        for push in pushes:
            self.assertEqual((push["env"][USER_ENV], push["env"][PASSWORD_ENV], push["env"][HOST_ENV]),
                             (USER, SECRET, HOST))
        for call in self.repo.calls():
            self.assertNotIn("--move", call["argv"])
            self.assertFalse(any(USER in a or SECRET in a for a in call["argv"]), call["argv"])
            if call not in pushes:
                self.assertFalse({USER_ENV, PASSWORD_ENV, HOST_ENV} & set(call["env"]), call["argv"])
        self.assertNotIn(SECRET, stdout + stderr)
        # Both targets read before the first push, then each read back.
        self.assertEqual(self.repo.registry.calls, [
            (HOST, MEDIA, tag) for tag in (T1, A1, T1, A1, T2, A2, T2, A2)
        ])
        for tag in (T1, T2):
            self.assertEqual(self.outcome(stdout, tag), "pushed")
        # Never a latest tag.
        self.assertFalse(any("latest" in str(c["argv"]) for c in self.repo.calls()))

    def test_the_same_digest_is_skipped(self):
        self.repo.recipe("demo")
        for tag in (T1, A1):
            self.repo.registry.held[f"{HOST}/{MEDIA}:{tag}"] = fake_digest(f"{MEDIA}:{T1}")
        code, stdout, stderr = self.repo.run("--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T1), "present")
        self.assertEqual(self.outcome(stdout, T2), "pushed")
        self.assertEqual([p["argv"][3] for p in self.repo.pushes()], [f"{HOST}/{MEDIA}:{T2}", f"{HOST}/{MEDIA}:{A2}"])

    def test_a_missing_alias_is_pushed_after_its_revision(self):
        self.repo.recipe("demo")
        self.repo.registry.held[f"{HOST}/{MEDIA}:{T1}"] = fake_digest(f"{MEDIA}:{T1}")
        code, stdout, stderr = self.repo.run("--push", f"{MEDIA}:{T1}")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T1), "pushed")
        self.assertEqual([p["argv"][2:4] for p in self.repo.pushes()], [[f"{MEDIA}:{T1}", f"{HOST}/{MEDIA}:{A1}"]])
        self.assertEqual(self.repo.registry.calls, [(HOST, MEDIA, T1), (HOST, MEDIA, A1), (HOST, MEDIA, A1)])

    def test_another_digest_is_refused_and_stops_the_push(self):
        self.repo.recipe("demo")
        self.repo.registry.held[f"{HOST}/{MEDIA}:{T1}"] = "sha256:" + "d" * 64
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "refused")
        self.assertEqual(self.outcome(stdout, T2), "held")
        self.assertIn(f"already holds sha256:{'d' * 64} under this tag; a revision tag never moves: "
                      "stop and ask the maintainer", stdout)
        self.assertNotIn("next revision", stdout)
        self.assertEqual(self.repo.pushes(), [])

    def test_an_alias_that_names_another_digest_is_never_moved(self):
        # An alias at another image (an older revision's, say) would need
        # --move; nothing of the set is pushed, its revision neither.
        self.repo.recipe("demo")
        self.repo.registry.held[f"{HOST}/{MEDIA}:{A1}"] = "sha256:" + "d" * 64
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "refused")
        self.assertEqual(self.outcome(stdout, T2), "held")
        self.assertIn(f"the registry's alias {A1} names sha256:{'d' * 64}; this pipeline never moves an alias: "
                      "stop and ask the maintainer", stdout)
        self.assertEqual(self.repo.pushes(), [])
        for call in self.repo.calls():
            self.assertNotIn("--move", call["argv"])

    def test_a_confirmed_revision_with_a_failed_alias_is_partial(self):
        # The revision tag is pushed and read back, then its alias push
        # fails: the row says the revision is there, not just "failed".
        self.repo.recipe("demo")
        digest = fake_digest(f"{MEDIA}:{T1}")
        self.repo.configure(fail_push=[f"{HOST}/{MEDIA}:{A1}"])
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "partial")
        self.assertIn(f"{MEDIA}:{T1}: partial: push {HOST}/{MEDIA}:{A1}: push refused: synthetic; the revision "
                      f"tag {T1} is on the registry with {digest}, its alias {A1} is not confirmed", stdout)
        # Nothing after it is pushed.
        self.assertEqual(self.outcome(stdout, T2), "held")
        self.assertEqual([p["argv"][3] for p in self.repo.pushes()], [f"{HOST}/{MEDIA}:{T1}", f"{HOST}/{MEDIA}:{A1}"])
        self.assertEqual(self.repo.registry.calls, [(HOST, MEDIA, T1), (HOST, MEDIA, A1), (HOST, MEDIA, T1)])
        # A revision already on the registry counts as confirmed too.
        self.repo.pushed.unlink()
        self.repo.log.unlink()
        self.repo.registry.held[f"{HOST}/{MEDIA}:{T1}"] = digest
        code, stdout, _ = self.repo.run("--push", f"{MEDIA}:{T1}")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "partial")
        self.assertEqual([p["argv"][3] for p in self.repo.pushes()], [f"{HOST}/{MEDIA}:{A1}"])
        # A failed revision push is no partial one.
        self.repo.log.unlink()
        self.repo.registry.held.clear()
        self.repo.configure(fail_push=[f"{HOST}/{MEDIA}:{T1}"])
        code, stdout, _ = self.repo.run("--push", f"{MEDIA}:{T1}")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "failed")
        self.assertNotIn("is on the registry", stdout)

    def test_a_failed_check_holds_every_push(self):
        self.repo.recipe("demo", pins=("match", "sha256:" + "e" * 64))
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "held")
        self.assertEqual(self.repo.pushes(), [])
        self.assertEqual(self.repo.registry.calls, [])

    def test_an_unreadable_registry_fails_closed(self):
        self.repo.recipe("demo")
        self.repo.registry.error = "HTTP 401"
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "failed")
        self.assertEqual(self.repo.pushes(), [])
        # The alias's read failing fails it too.
        self.repo.registry.error = None
        self.repo.registry.calls.clear()
        self.repo.registry.errors = {2: publish.RegistryError("HTTP 401")}
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "failed")
        self.assertEqual(self.repo.registry.calls, [(HOST, MEDIA, T1), (HOST, MEDIA, A1)])
        self.assertEqual(self.repo.pushes(), [])

    def test_the_push_is_read_back(self):
        self.repo.recipe("demo")
        self.repo.configure(push_noop=True)
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        digest = fake_digest(f"{MEDIA}:{T1}")
        self.assertEqual(self.outcome(stdout, T1), "unconfirmed")
        self.assertIn(f"unconfirmed: upcli pushed {digest} to :{T1}; the registry serves None", stdout)
        self.assertEqual(self.outcome(stdout, T2), "held")
        # The alias waits for its revision.
        self.assertEqual(len(self.repo.pushes()), 1)

    def test_a_failed_read_back_still_says_pushed(self):
        self.repo.recipe("demo")
        digest = fake_digest(f"{MEDIA}:{T1}")
        # Reads 1 and 2 before the pushes, 3 the revision's read-back, 4 the
        # alias's: with the revision confirmed, a failed alias read-back is
        # partial.
        for error, shown in ((publish.RegistryError("HTTP 503"), "HTTP 503"),
                             (RuntimeError("synthetic"), "unexpected RuntimeError: synthetic")):
            for read, tag, pushes, outcome in ((3, T1, 1, "unconfirmed"), (4, A1, 2, "partial")):
                self.repo.pushed.unlink(missing_ok=True)
                self.repo.log.unlink(missing_ok=True)
                self.repo.registry.calls.clear()
                self.repo.registry.errors = {read: error}
                code, stdout, _ = self.repo.run("--push", f"{MEDIA}:{T1}")
                self.assertEqual(code, 1)
                self.assertEqual(self.outcome(stdout, T1), outcome)
                self.assertIn(f"upcli pushed {digest} to :{tag}; the read-back failed: {shown}", stdout)
                self.assertEqual(len(self.repo.pushes()), pushes)
                self.assertEqual(f"the revision tag {T1} is on the registry" in stdout, outcome == "partial")

    def test_any_error_marks_its_set_and_the_summary_prints(self):
        self.repo.recipe("demo")
        # In the read before a push.
        self.repo.registry.errors = {1: RuntimeError("synthetic")}
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "failed")
        self.assertEqual(self.outcome(stdout, T2), "held")
        self.assertIn("unexpected RuntimeError: synthetic", stdout)
        self.assertEqual(self.repo.pushes(), [])
        # In the checks: upcli output that is not UTF-8.
        self.repo.registry.errors = {}
        self.repo.configure(import_raw=f"{MEDIA}:{T1}\t\xff\n")
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "failed")
        self.assertIn("unexpected UnicodeDecodeError", stdout)

    def test_the_push_line_must_name_the_target_and_digest(self):
        self.repo.recipe("demo")
        for settings, fragment in (
            ({"push_word": "moved"}, "unexpected last line"),
            ({"push_word": "uploaded"}, "unexpected last line"),
            ({"push_target": f"{HOST}/{MEDIA}:9.9-r1"}, "unexpected last line"),
            ({"push_digest": "sha256:" + "c" * 64}, f"upcli pushed sha256:{'c' * 64} to :{T1}"),
        ):
            self.repo.pushed.unlink(missing_ok=True)
            self.repo.configure(**settings)
            code, stdout, _ = self.repo.run("--push", f"{MEDIA}:{T1}")
            self.assertEqual(code, 1, settings)
            self.assertEqual(self.outcome(stdout, T1), "failed", settings)
            self.assertIn(fragment, stdout, settings)

    def test_an_unchanged_push_is_present(self):
        self.repo.recipe("demo")
        self.repo.configure(push_word="unchanged")
        code, stdout, stderr = self.repo.run("--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T1), "present")
        self.assertEqual(self.outcome(stdout, T2), "present")

    def test_push_needs_credentials_and_a_host(self):
        self.repo.recipe("demo")
        for missing in (USER_ENV, PASSWORD_ENV, HOST_ENV):
            environ = self.repo.environ(push=True, **{missing: None})
            code, stdout, stderr = self.repo.run("--push", environ=environ)
            self.assertEqual(code, 2, missing)
            self.assertIn(missing, stderr)
            self.assertNotIn(SECRET, stdout + stderr)
        environ = self.repo.environ(push=True, **{HOST_ENV: "https://registry.invalid/x"})
        self.assertEqual(self.repo.run("--push", environ=environ)[0], 2)
        self.assertEqual(self.repo.calls(), [])
        self.assertFalse((self.repo.root / "recipes" / "demo" / "build-env.txt").exists())

    def test_dry_run_without_a_host_names_the_family_default(self):
        self.repo.recipe("demo")
        code, stdout, stderr = self.repo.run(environ=self.repo.environ(**{HOST_ENV: None}))
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"registry {publish.DEFAULT_HOST} (dry run)", stdout)
        self.assertEqual(self.repo.registry.calls, [])


class Experimental(Case):
    EXPERIMENTAL_MEDIA = "unemu/experimental/media/ibm-pc/demo"
    EXPERIMENTAL_FIRMWARE = "unemu/experimental/firmware/ibm-pc/demo-bios"

    def test_experimental_ref_and_tag(self):
        self.repo.recipe("demo")
        code, stdout, stderr = self.repo.run("--experimental", "42")
        self.assertEqual(code, 0, stderr)
        targets = [c["argv"][3] for c in self.repo.calls() if "--dry-run" in c["argv"]]
        self.assertEqual(targets, [f"{HOST}/{self.EXPERIMENTAL_MEDIA}:{tag}"
                                   for tag in ("2.0-ci42-r1", "2.0-ci42", "2.0-720k-ci42-r1", "2.0-720k-ci42")])
        self.assertEqual(self.outcome(stdout, "2.0-ci42-r1", self.EXPERIMENTAL_MEDIA), "dry")
        self.assertEqual(self.row(stdout, self.EXPERIMENTAL_MEDIA, "2.0-720k-ci42-r1")[2], "2.0-720k-ci42")
        # The set is imported under its own ref; only the target changes.
        imports = [c["argv"] for c in self.repo.calls() if c["argv"][:3] == ["image", "media", "import"]]
        self.assertEqual(imports[0][imports[0].index("--tag") + 1], f"{MEDIA}:{T1}")

        code, stdout, stderr = self.repo.run("--experimental", "42", "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.repo.registry.calls[:2], [(HOST, self.EXPERIMENTAL_MEDIA, "2.0-ci42-r1"),
                                                        (HOST, self.EXPERIMENTAL_MEDIA, "2.0-ci42")])
        self.assertEqual([p["argv"][2:4] for p in self.repo.pushes()], [
            [f"{MEDIA}:{T1}", f"{HOST}/{self.EXPERIMENTAL_MEDIA}:2.0-ci42-r1"],
            [f"{MEDIA}:{T1}", f"{HOST}/{self.EXPERIMENTAL_MEDIA}:2.0-ci42"],
            [f"{MEDIA}:{T2}", f"{HOST}/{self.EXPERIMENTAL_MEDIA}:2.0-720k-ci42-r1"],
            [f"{MEDIA}:{T2}", f"{HOST}/{self.EXPERIMENTAL_MEDIA}:2.0-720k-ci42"],
        ])

    def test_experimental_firmware_ref_and_tag(self):
        self.repo.firmware_recipe("bios")
        code, stdout, stderr = self.repo.run("--experimental", "42", "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, "ci42-r1", self.EXPERIMENTAL_FIRMWARE), "pushed")
        # A package has no alias, in the experimental namespace neither.
        self.assertEqual(self.row(stdout, self.EXPERIMENTAL_FIRMWARE, "ci42-r1")[2], "-")
        self.assertEqual(self.repo.registry.calls[0], (HOST, self.EXPERIMENTAL_FIRMWARE, "ci42-r1"))
        # The package is imported under its store tag; only the target changes.
        self.assertEqual([p["argv"][2:4] for p in self.repo.pushes()], [
            [FIRMWARE_STORE_TAG, f"{HOST}/{self.EXPERIMENTAL_FIRMWARE}:ci42-r1"],
        ])

    def test_experimental_tags_are_revision_tags_of_their_own(self):
        # ci<RUN> as the last part of the release: a revision tag that
        # never moves, and the release, its alias.
        for tag, run, expected in (("1.4-r1", "42", ("1.4-ci42-r1", "1.4-ci42")),
                                   ("1.4-720k-r2", "7", ("1.4-720k-ci7-r2", "1.4-720k-ci7")),
                                   ("r1", "42", ("ci42-r1", "ci42")), ("r3", "1", ("ci1-r3", "ci1"))):
            self.assertEqual(publish.experimental_tag(tag, run), expected)
            self.assertEqual(manifest.parse_revision(expected[0]), (expected[1], int(tag.rsplit("r", 1)[1])))
        for tag in ("1.4", "latest", "1"):
            with self.assertRaises(publish.Failure):
                publish.experimental_tag(tag, "42")

    def test_the_experimental_repository_fits_a_repository_name(self):
        # The manifests keep unemu/firmware/ibm-pc/<id> and
        # unemu/media/ibm-pc/<work> within 255 bytes; the experimental
        # repository adds experimental/. Each kind at the limit (255 bytes)
        # and one byte past it.
        prefix = {"firmware": "unemu/experimental/firmware/ibm-pc/", "media": "unemu/experimental/media/ibm-pc/"}
        cases = []
        for kind in ("firmware", "media"):
            cases += [(kind, 255 - len(prefix[kind]), True), (kind, 256 - len(prefix[kind]), False)]
        for kind, length, accepted in cases:
            name = "a" * length
            repository = prefix[kind] + name
            self.assertEqual(len(repository), 255 if accepted else 256)
            with self.subTest(kind=kind, length=length), tempfile.TemporaryDirectory() as directory:
                repo = Repository(directory)
                if kind == "firmware":
                    repo.firmware_recipe("bios", catalogue_id=name)
                    ref = f"unemu/firmware/ibm-pc/{name}:r1"
                else:
                    repo.recipe("demo", work=name)
                    ref = f"unemu/media/ibm-pc/{name}:2.0-r1"
                code, stdout, stderr = repo.run("--experimental", "5")
                if accepted:
                    self.assertEqual(code, 0, stderr)
                    self.assertIn(repository, stdout)
                    continue
                self.assertEqual(code, 1)
                self.assertIn(f"refused: the experimental repository {repository} of {ref} is 256 bytes, "
                              "longer than 255", stderr)
                # Refused before any build or upcli call.
                self.assertEqual(repo.calls(), [])
                self.assertFalse(any((repo.root / "recipes").glob("*/build-env.txt")))
                # Its own repository is within the limit.
                self.assertEqual(repo.run()[0], 0)

    def test_the_experimental_tag_fits_a_tag(self):
        # A 114-character version: :<v>-720k-r1 is 122 characters, so
        # -ci123 makes 128 and -ci1234 one too many.
        version = "1" * 114
        self.repo.recipe("demo", version=version)
        code, _, stderr = self.repo.run("--experimental", "123")
        self.assertEqual(code, 0, stderr)
        self.repo.log.unlink()
        code, _, stderr = self.repo.run("--experimental", "1234")
        self.assertEqual(code, 1)
        self.assertIn(f"the target tag '{version}-720k-ci1234-r1' is longer than 128 characters", stderr)
        self.assertEqual(self.repo.calls(), [])


class SkipPublished(Case):
    """--skip-published: what the registry already serves under its own tag
    is neither built, imported nor pushed."""

    def publish(self, ref, digest=None, alias=True):
        """Put REF on the fake registry, with its pinned digest unless
        DIGEST is given, and a set's release alias with it unless ALIAS is
        false."""
        repository, tag = ref.rsplit(":", 1)
        if digest is None:
            firmware = f"private/firmware/ibm-pc/{repository.rsplit('/', 1)[1]}:r1"
            digest = fake_digest(firmware if "/firmware/" in ref else ref)
        self.repo.registry.held[f"{HOST}/{ref}"] = digest
        if alias and "/media/" in ref:
            self.repo.registry.held[f"{HOST}/{repository}:{tag.rsplit('-', 1)[0]}"] = digest

    def imports(self):
        return [c["argv"] for c in self.repo.calls() if c["argv"][1:3] in (["media", "import"],
                                                                          ["firmware", "import"])]

    def built(self, recipe):
        return (self.repo.root / "recipes" / recipe / "build-env.txt").exists()

    def test_published_is_neither_built_imported_nor_pushed(self):
        self.repo.recipe("demo")
        self.publish(f"{MEDIA}:{T1}")
        self.publish(f"{MEDIA}:{T2}")
        for args in (("--skip-published",), ("--skip-published", "--push")):
            self.repo.log.unlink(missing_ok=True)
            self.repo.registry.calls.clear()
            code, stdout, stderr = self.repo.run(*args)
            self.assertEqual(code, 0, stderr)
            for tag, alias in ((T1, A1), (T2, A2)):
                row = self.row(stdout, MEDIA, tag)
                self.assertEqual(row[2:5], [alias, fake_digest(f"{MEDIA}:{tag}"), "published"], args)
            self.assertFalse(self.built("demo"), args)
            # upcli only checks its version: no import, no dry run, no push.
            self.assertEqual([c["argv"] for c in self.repo.calls()], [["--version"]], args)
            # One read of each tag and alias, and none for a push.
            self.assertEqual(self.repo.registry.calls, [(HOST, MEDIA, tag) for tag in (T1, A1, T2, A2)], args)
            self.assertIn("== skip demo: published, not built", stderr)
            self.assertEqual(list((self.repo.root / "build").iterdir()), [])

    def test_another_digest_is_refused(self):
        self.repo.recipe("demo")
        self.publish(f"{MEDIA}:{T1}", "sha256:" + "d" * 64)
        code, stdout, _ = self.repo.run("--skip-published")
        self.assertEqual(code, 1)
        row = self.row(stdout, MEDIA, T1)
        self.assertEqual(row[3:5], ["sha256:" + "d" * 64, "refused"])
        # No next revision is advised: the maintainer decides.
        self.assertIn(f"{HOST} already serves sha256:{'d' * 64} under this tag, the manifest pins "
                      f"{fake_digest(f'{MEDIA}:{T1}')}; a revision tag never moves: stop and ask the maintainer",
                      stdout)
        self.assertNotIn("next revision", stdout)
        self.assertNotIn("2.0-r2", stdout)
        # The other set is still built and checked; the refused one is not
        # imported.
        self.assertEqual(self.outcome(stdout, T2), "dry")
        self.assertEqual([a[a.index("--tag") + 1] for a in self.imports()], [f"{MEDIA}:{T2}"])
        code, stdout, _ = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "refused")
        self.assertEqual(self.outcome(stdout, T2), "held")
        self.assertEqual(self.repo.pushes(), [])

    def test_a_missing_alias_is_built_and_pushed(self):
        # The revision is published, its alias is not: built and imported
        # again, and only the alias is pushed.
        self.repo.recipe("demo")
        self.publish(f"{MEDIA}:{T1}", alias=False)
        self.publish(f"{MEDIA}:{T2}")
        code, stdout, stderr = self.repo.run("--skip-published")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T1), "dry")
        self.assertEqual(self.outcome(stdout, T2), "published")
        self.assertIn(f"but not under its alias {A1} yet", stderr)
        self.assertEqual([a[a.index("--tag") + 1] for a in self.imports()], [f"{MEDIA}:{T1}"])
        code, stdout, stderr = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T1), "pushed")
        self.assertEqual([p["argv"][2:4] for p in self.repo.pushes()], [[f"{MEDIA}:{T1}", f"{HOST}/{MEDIA}:{A1}"]])

    def test_an_alias_that_names_another_digest_is_refused(self):
        self.repo.recipe("demo")
        self.publish(f"{MEDIA}:{T1}")
        self.repo.registry.held[f"{HOST}/{MEDIA}:{A1}"] = "sha256:" + "d" * 64
        code, stdout, stderr = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "refused")
        self.assertIn(f"under this tag, but its alias {A1} names sha256:{'d' * 64}; this pipeline never moves "
                      "an alias: stop and ask the maintainer", stdout)
        self.assertEqual(self.outcome(stdout, T2), "held")
        self.assertEqual(self.repo.pushes(), [])

    def test_a_missing_tag_is_built_and_pushed_as_before(self):
        self.repo.recipe("demo")
        code, stdout, stderr = self.repo.run("--skip-published")
        self.assertEqual(code, 0, stderr)
        self.assertTrue(self.built("demo"))
        self.assertEqual(len(self.imports()), 2)
        self.assertEqual(self.outcome(stdout, T1), "dry")
        # A missing revision: its alias is not read.
        self.assertEqual(self.repo.registry.calls, [(HOST, MEDIA, T1), (HOST, MEDIA, T2)])
        self.assertEqual(self.repo.pushes(), [])
        self.repo.registry.calls.clear()
        code, stdout, stderr = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 0, stderr)
        for tag in (T1, T2):
            self.assertEqual(self.outcome(stdout, tag), "pushed")
        # The skip reads, then the reads before and after the pushes.
        self.assertEqual(self.repo.registry.calls, [(HOST, MEDIA, t) for t in
                                                    (T1, T2, T1, A1, T1, A1, T2, A2, T2, A2)])

    def test_an_unpinned_item_is_not_compared(self):
        self.repo.recipe("demo", pins=("match", None))
        self.publish(f"{MEDIA}:{T1}")
        self.publish(f"{MEDIA}:{T2}", fake_digest(f"{MEDIA}:{T2}"))
        code, stdout, _ = self.repo.run("--skip-published")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "published")
        self.assertEqual(self.outcome(stdout, T2), "unpinned")
        # Only the pinned set was read; the unpinned one was built and
        # imported to measure its digest.
        self.assertEqual(self.repo.registry.calls, [(HOST, MEDIA, T1), (HOST, MEDIA, A1)])
        self.assertTrue(self.built("demo"))
        self.assertEqual([a[a.index("--tag") + 1] for a in self.imports()], [f"{MEDIA}:{T2}"])
        code, stdout, stderr = self.repo.run("--skip-published", "--allow-unpinned")
        self.assertEqual(code, 0, stderr)
        code, stdout, _ = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T2), "unpinned")
        self.assertEqual(self.repo.pushes(), [])

    def test_mixed(self):
        other = "unemu/media/ibm-pc/other"
        self.repo.recipe("demo")
        self.repo.recipe("other", work="other")
        self.repo.firmware_recipe("bios")
        self.publish(f"{MEDIA}:{T1}")
        self.publish(f"{FIRMWARE_REPOSITORY}:r1")
        code, stdout, stderr = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T1), "published")
        self.assertEqual(self.outcome(stdout, T2), "pushed")
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "published")
        for tag in (T1, T2):
            self.assertEqual(self.outcome(stdout, tag, other), "pushed")
        self.assertEqual((self.built("bios"), self.built("demo"), self.built("other")), (False, True, True))
        self.assertEqual([a[a.index("--tag") + 1] for a in self.imports()],
                         [f"{MEDIA}:{T2}", f"{other}:{T1}", f"{other}:{T2}"])
        self.assertEqual([p["argv"][3] for p in self.repo.pushes()], [
            f"{HOST}/{MEDIA}:{T2}", f"{HOST}/{MEDIA}:{A2}", f"{HOST}/{other}:{T1}", f"{HOST}/{other}:{A1}",
            f"{HOST}/{other}:{T2}", f"{HOST}/{other}:{A2}",
        ])

    def test_a_package(self):
        self.repo.firmware_recipe("bios")
        self.publish(f"{FIRMWARE_REPOSITORY}:r1")
        code, stdout, stderr = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "published")
        # A package has no alias to read.
        self.assertEqual(self.repo.registry.calls, [(HOST, FIRMWARE_REPOSITORY, "r1")])
        self.assertFalse(self.built("bios"))
        self.assertEqual(self.repo.pushes(), [])
        self.publish(f"{FIRMWARE_REPOSITORY}:r1", "sha256:" + "d" * 64)
        code, stdout, stderr = self.repo.run("--skip-published")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "refused")
        self.assertIn("a revision tag never moves: stop and ask the maintainer", stdout)
        self.assertNotIn("next revision", stdout)
        self.assertIn("== skip bios: nothing of it is left to build", stderr)
        self.assertFalse(self.built("bios"))

    def test_a_failed_read_fails_closed(self):
        self.repo.recipe("demo")
        self.repo.firmware_recipe("bios")
        self.repo.registry.error = "HTTP 503"
        code, stdout, _ = self.repo.run("--skip-published")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "failed")
        self.assertIn(f"reading {MEDIA}:{T1} on {HOST}: HTTP 503", stdout)
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "failed")
        self.assertEqual((self.built("demo"), self.built("bios")), (False, False))
        self.assertEqual(self.imports(), [])
        # Only the first read (the package's: recipes in name order) fails:
        # the package fails, the sets are built and checked, and nothing is
        # pushed.
        self.repo.registry.error = None
        self.repo.registry.errors = {1: RuntimeError("synthetic")}
        self.repo.registry.calls.clear()
        code, stdout, _ = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 1)
        self.assertIn("unexpected RuntimeError: synthetic", stdout)
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "failed")
        self.assertEqual(self.outcome(stdout, T1), "held")
        self.assertEqual(self.outcome(stdout, T2), "held")
        self.assertEqual((self.built("demo"), self.built("bios")), (True, False))
        self.assertEqual(self.repo.pushes(), [])

    def test_a_failed_read_of_the_alias_fails_closed(self):
        self.repo.recipe("demo")
        self.publish(f"{MEDIA}:{T1}")
        # Read 1 is the revision's, which serves the pin; read 2 its alias's.
        self.repo.registry.errors = {2: publish.RegistryError("HTTP 503")}
        code, stdout, _ = self.repo.run("--skip-published", f"{MEDIA}:{T1}")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "failed")
        self.assertIn(f"reading {MEDIA}:{A1} on {HOST}: HTTP 503", stdout)
        self.assertFalse(self.built("demo"))

    def test_experimental_reads_the_published_tag_and_pushes_the_rest(self):
        experimental_media = "unemu/experimental/media/ibm-pc/demo"
        experimental_firmware = "unemu/experimental/firmware/ibm-pc/demo-bios"
        self.repo.recipe("demo")
        self.repo.firmware_recipe("bios")
        self.publish(f"{MEDIA}:{T1}")
        code, stdout, stderr = self.repo.run("--skip-published", "--experimental", "42", "--push")
        self.assertEqual(code, 0, stderr)
        # The published set's row names the tag that was read.
        self.assertEqual(self.outcome(stdout, T1), "published")
        self.assertEqual(self.outcome(stdout, "2.0-720k-ci42-r1", experimental_media), "pushed")
        self.assertEqual(self.outcome(stdout, "ci42-r1", experimental_firmware), "pushed")
        self.assertEqual(self.repo.registry.calls[:4], [(HOST, FIRMWARE_REPOSITORY, "r1"),
                                                        (HOST, MEDIA, T1), (HOST, MEDIA, A1), (HOST, MEDIA, T2)])
        # Never pushed to the experimental targets again.
        self.assertEqual([p["argv"][2:4] for p in self.repo.pushes()], [
            [FIRMWARE_STORE_TAG, f"{HOST}/{experimental_firmware}:ci42-r1"],
            [f"{MEDIA}:{T2}", f"{HOST}/{experimental_media}:2.0-720k-ci42-r1"],
            [f"{MEDIA}:{T2}", f"{HOST}/{experimental_media}:2.0-720k-ci42"],
        ])
        self.assertNotIn((HOST, experimental_media, "2.0-ci42-r1"), self.repo.registry.calls)


class NoBuild(Case):
    """--no-build: the outputs already in out/<recipe>/, checked against
    their pins, and no build.sh."""

    def built(self, recipe):
        return (self.repo.root / "recipes" / recipe / "build-env.txt").exists()

    @staticmethod
    def steps(calls):
        """Each upcli call as (what, store tag[, target])."""
        result = []
        for argv in (c["argv"] for c in calls):
            if argv == ["--version"]:
                result.append(("version",))
            elif argv[1:3] == ["media", "import"]:
                result.append(("import", argv[argv.index("--tag") + 1]))
            elif argv[1:3] == ["firmware", "import"]:
                result.append(("import", f"private/firmware/ibm-pc/{argv[argv.index('--for') + 1]}:r1"))
            elif argv[2:3] == ["show"]:
                result.append(("show", argv[3]))
            else:
                result.append(("dry run" if "--dry-run" in argv else "push", argv[2], argv[3]))
        return result

    def test_the_outputs_are_used_as_they_are(self):
        self.repo.recipe("demo")
        self.repo.firmware_recipe("bios")
        code, stdout, stderr = self.repo.run("--no-build")
        self.assertEqual(code, 0, stderr)
        self.assertFalse(self.built("demo") or self.built("bios"))
        self.assertIn("== check the outputs of demo (--no-build)", stderr)
        self.assertEqual(self.outcome(stdout, T1), "dry")
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "dry")
        # Only the build is left out: every set and package is imported,
        # shown and dry-run pushed as without --no-build.
        checks = []
        for tag, targets in ((FIRMWARE_STORE_TAG, [f"{FIRMWARE_REPOSITORY}:r1"]),
                             (f"{MEDIA}:{T1}", [f"{MEDIA}:{T1}", f"{MEDIA}:{A1}"]),
                             (f"{MEDIA}:{T2}", [f"{MEDIA}:{T2}", f"{MEDIA}:{A2}"])):
            checks += [("import", tag), ("show", tag)] + [("dry run", tag, f"{HOST}/{t}") for t in targets]
        self.assertEqual(self.steps(self.repo.calls()), [("version",), *checks])
        self.repo.log.unlink()
        code, stdout, stderr = self.repo.run("--no-build", "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T2), "pushed")
        pushes = [("push", c[1], c[2]) for c in checks if c[0] == "dry run"]
        self.assertEqual(self.steps(self.repo.calls()), [("version",), *checks, *pushes])
        self.assertFalse(self.built("demo") or self.built("bios"))

    def test_missing_or_changed_outputs_fail(self):
        self.repo.recipe("demo")
        out = self.repo.root / "out" / "demo"
        image = out / "720k" / "b.img"
        hint = "; build the recipe first (bash recipes/demo/build.sh, or this pipeline without --no-build)"
        image.unlink()
        for args in (("--no-build",), ("--no-build", "--push")):
            code, stdout, _ = self.repo.run(*args)
            self.assertEqual(code, 1, args)
            self.assertEqual(self.outcome(stdout, T1), "failed", args)
            self.assertIn(f"--no-build: missing output 720k/b.img in {out}{hint}", stdout)
        image.write_bytes(b"changed")
        code, stdout, _ = self.repo.run("--no-build")
        self.assertEqual(code, 1)
        self.assertIn("--no-build: output 720k/b.img: SHA-256", stdout)
        shutil.rmtree(out)
        code, stdout, _ = self.repo.run("--no-build")
        self.assertEqual(code, 1)
        self.assertIn("--no-build: missing output 144m/a.img", stdout)
        self.assertFalse(self.built("demo"))
        self.assertEqual([c["argv"] for c in self.repo.calls()], [["--version"]] * 4)
        self.assertEqual(self.repo.pushes(), [])

    def test_a_published_recipe_needs_no_outputs(self):
        self.repo.recipe("demo")
        self.repo.firmware_recipe("bios")
        self.repo.registry.held[f"{HOST}/{FIRMWARE_REPOSITORY}:r1"] = fake_digest(FIRMWARE_STORE_TAG)
        shutil.rmtree(self.repo.root / "out" / "bios")
        code, stdout, stderr = self.repo.run("--skip-published", "--no-build", "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "published")
        self.assertEqual(self.outcome(stdout, T1), "pushed")
        self.assertFalse(self.built("demo") or self.built("bios"))


class Firmware(Case):
    """A firmware package goes the media sets' way, with its own import
    and show checks."""

    REPOSITORY = FIRMWARE_REPOSITORY
    STORE_TAG = FIRMWARE_STORE_TAG

    def firmware_outcome(self, stdout, tag="r1"):
        return self.row(stdout, self.REPOSITORY, tag)[4]

    def imports(self, kind="firmware"):
        return [c["argv"] for c in self.repo.calls() if c["argv"][:3] == ["image", kind, "import"]]

    def test_calls_follow_the_manifest(self):
        self.repo.firmware_recipe("bios")
        code, stdout, stderr = self.repo.run()
        self.assertEqual(code, 0, stderr)
        imports = self.imports()
        self.assertEqual(len(imports), 1)
        store = imports[0][imports[0].index("--store") + 1]
        self.assertTrue(store.startswith(str(self.repo.root / "build" / "publish.")), store)
        checked = manifest.load(self.repo.root / "recipes" / "bios" / "firmware.toml")
        out_dir = str(self.repo.root / "out" / "bios")
        self.assertEqual(imports[0], manifest.firmware_import_arguments(checked, out_dir) + ["--store", store, "--create"])
        self.assertIn("--open", imports[0])
        shows = [c["argv"] for c in self.repo.calls() if c["argv"][:3] == ["image", "firmware", "show"]]
        self.assertEqual(shows, [["image", "firmware", "show", self.STORE_TAG, "--store", store]])
        # One dry run: a package has no alias.
        dry = [c["argv"] for c in self.repo.calls() if "--dry-run" in c["argv"]]
        self.assertEqual(len(dry), 1)
        self.assertEqual(dry[0][:7], ["image", "push", self.STORE_TAG, f"{HOST}/{self.REPOSITORY}:r1",
                                      "--store", store, "--dry-run"])
        row = self.row(stdout, self.REPOSITORY, "r1")
        self.assertEqual(row[2:], ["-", fake_digest(self.STORE_TAG), "dry", "run", "GPL-2.0-or-later"])
        self.assertIn("recipes/bios/firmware.toml: ok, firmware package", stderr)
        self.assertEqual(self.repo.registry.calls, [])
        self.assertEqual(self.repo.pushes(), [])
        self.assertEqual(list((self.repo.root / "build").iterdir()), [])

    def test_sets_and_packages_together_and_selection(self):
        self.repo.recipe("demo")
        self.repo.firmware_recipe("bios")
        code, stdout, stderr = self.repo.run()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.firmware_outcome(stdout), "dry")
        self.assertEqual(self.outcome(stdout, T2), "dry")
        # In recipe order, one store.
        imports = [c["argv"] for c in self.repo.calls() if c["argv"][1:3] in (["media", "import"],
                                                                              ["firmware", "import"])]
        self.assertEqual([a[1] for a in imports], ["firmware", "media", "media"])
        self.assertEqual(len({a[a.index("--store") + 1] for a in imports}), 1)

        for selector, packages, sets in (("bios", 1, 0), (f"{self.REPOSITORY}:r1", 1, 0),
                                         (f"{MEDIA}:{T1}", 0, 1), ("demo", 0, 2)):
            self.repo.log.unlink(missing_ok=True)
            code, _, stderr = self.repo.run(selector)
            self.assertEqual(code, 0, stderr)
            self.assertEqual((len(self.imports()), len(self.imports("media"))), (packages, sets), selector)
        for selector in (f"{self.REPOSITORY}:r2", self.STORE_TAG, self.REPOSITORY, f"{self.REPOSITORY}:1",
                         "unemu/firmware/demo-bios:1", "unemu/firmware/demo-bios:r1"):
            self.repo.log.unlink(missing_ok=True)
            code, _, stderr = self.repo.run(selector)
            self.assertEqual(code, 2, selector)
            self.assertIn("neither a recipe nor a ref", stderr)
            self.assertEqual(self.repo.calls(), [])

    def test_the_show_must_state_the_manifest(self):
        self.repo.firmware_recipe("bios")
        tag, digest = self.STORE_TAG, fake_digest(self.STORE_TAG)
        rom, source = sha256(FIRMWARE_OUTPUTS["demo.bin"]), sha256(FIRMWARE_OUTPUTS["demo-src.tar"])
        size = len(FIRMWARE_OUTPUTS["demo-src.tar"])
        other = "c" * 64
        for change, fragment in (
            ({"first": f'{tag}\t{digest}\t"Other BIOS"'}, "the first line"),
            ({"first": f'private/firmware/ibm-pc/other:r1\t{digest}\t"Demo BIOS"'}, "the first line"),
            ({"first": f'{tag}\tsha256:{other}\t"Demo BIOS"'}, "the first line"),
            ({"first": f"{tag}\t{digest}\tDemo BIOS"}, "the first line"),
            ({"rom": f"system-bios\tsystem-bios\t64\t{other}"}, "the ROM lines"),
            ({"rom": f"system-bios\tsystem-bios\t65\t{rom}"}, "the ROM lines"),
            ({"rom": None}, "the ROM lines"),
            ({"second rom": f"option-rom\toption-rom\t64\t{other}"}, "the ROM lines"),
            ({"vendor": 'vendor\t"Other Vendor"'}, "vendor is"),
            ({"vendor": None}, "vendor is"),
            ({"second vendor": 'vendor\t"Test Vendor"'}, "vendor is"),
            ({"version": 'version\t"2.0"'}, "version is"),
            # The backslash unescaped.
            ({"description": 'description\t"A synthetic \\"demo\\" BIOS with a back\\slash."'}, "description is"),
            # Not sorted, as upcli stores and shows them.
            ({"labels": 'labels\t["system-bios", "open-source"]'}, "labels is"),
            ({"source": f'source\t"other-src.tar"\ttar\t{size}\tsha256:{source}\t[]'}, "the source lines"),
            ({"source": f'source\t"demo-src.tar"\tzip\t{size}\tsha256:{source}\t[]'}, "the source lines"),
            ({"source": f'source\t"demo-src.tar"\ttar\t{size}\tsha256:{other}\t[]'}, "the source lines"),
            ({"source": None}, "the source lines"),
            ({"layer rom": f"layer\trom\tsha256:{rom}\topen\tGPL-3.0-or-later"}, "the rom layers"),
            ({"layer rom": f"layer\trom\tsha256:{rom}\tuser-supplied\t-"}, "the rom layers"),
            ({"layer rom": None}, "the rom layers"),
            ({"layer source": f"layer\tsource\tsha256:{source}\tuser-supplied\t-"}, "the source layers"),
            ({"layer readme": f"layer\treadme\tsha256:{'e' * 64}\tuser-supplied\t-"}, "the readme layer is"),
            ({"layer config": f"layer\tconfig\tsha256:{'f' * 64}\topen\tMIT"}, "the config layer is"),
            ({"layer extra": f"layer\textra\tsha256:{other}\topen\tMIT"}, "the extra layer is"),
        ):
            self.repo.configure(firmware_show=change)
            code, stdout, _ = self.repo.run()
            self.assertEqual(code, 1, change)
            self.assertEqual(self.firmware_outcome(stdout), "failed", change)
            self.assertIn(f"firmware show: {fragment}", stdout, change)
        self.repo.configure(push_line="push\tprivate registries outside unemu/ only: copyleft ROM without its "
                                      "source archive")
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertIn("firmware show: the last line is", stdout)
        self.repo.configure()
        self.assertEqual(self.repo.run()[0], 0)

    def test_the_import_output_is_strict(self):
        tag, digest = self.STORE_TAG, fake_digest(self.STORE_TAG)
        rom = sha256(FIRMWARE_OUTPUTS["demo.bin"])
        self.repo.firmware_recipe("bios")
        for lines, fragment in (
            ([f"complete\t{tag}\t{rom}", f"note\t{tag}\tkept user-supplied: synthetic",
              f"imported\t{tag}\t{digest}"], "import: unexpected output"),
            ([f"already imported\t{tag}"], "import: unexpected output"),
            ([f"complete\t{tag}\t{'0' * 64}", f"imported\t{tag}\t{digest}"], "import: unexpected output"),
            (["unmatched: 1 files (1 size mismatches, 0 SHA-256 mismatches)", f"complete\t{tag}\t{rom}",
              f"imported\t{tag}\t{digest}"], "import: unexpected output"),
            ([f"complete\t{tag}\t{rom}"], "import: unexpected output"),
            ([f"complete\t{tag}\t{rom}", f"imported\tprivate/firmware/ibm-pc/other:r1\t{digest}"],
             "import: unexpected last line"),
            ([f"complete\t{tag}\t{rom}", f"imported\t{tag}\tsha256:{'C' * 64}"], "import: unexpected last line"),
            ([f"complete\t{tag}\t{rom}", f"imported\t{tag}\t{digest}\textra"], "import: unexpected last line"),
        ):
            self.repo.configure(firmware_import=lines)
            code, stdout, _ = self.repo.run()
            self.assertEqual(code, 1, lines)
            self.assertEqual(self.firmware_outcome(stdout), "failed", lines)
            self.assertIn(fragment, stdout, lines)
        self.repo.configure(fail_import=[tag])
        self.repo.log.unlink()
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertIn("import: selected catalogue entry has no complete unambiguous package", stdout)
        # A failed import is taken no further.
        self.assertEqual([c["argv"][:3] for c in self.repo.calls()],
                         [["--version"], ["image", "firmware", "import"]])

    def test_digest_pins(self):
        self.repo.firmware_recipe("bios", pin=None)
        self.repo.firmware_recipe("other", catalogue_id="other-bios", pin="sha256:" + "e" * 64)
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertEqual(self.firmware_outcome(stdout), "unpinned")
        self.assertEqual(self.row(stdout, "unemu/firmware/ibm-pc/other-bios", "r1")[4], "mismatch")
        self.assertIn(f"upcli gives {fake_digest('private/firmware/ibm-pc/other-bios:r1')}, the manifest pins "
                      f"sha256:{'e' * 64}", stdout)
        code, stdout, _ = self.repo.run("--allow-unpinned", "bios")
        self.assertEqual(code, 0)
        code, stdout, _ = self.repo.run("--push", "bios")
        self.assertEqual(code, 1)
        self.assertEqual(self.firmware_outcome(stdout), "unpinned")
        self.assertEqual(self.repo.pushes(), [])

    def test_push(self):
        self.repo.firmware_recipe("bios")
        code, stdout, stderr = self.repo.run("--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.firmware_outcome(stdout), "pushed")
        pushes = self.repo.pushes()
        self.assertEqual([p["argv"] for p in pushes], [[
            "image", "push", self.STORE_TAG, f"{HOST}/{self.REPOSITORY}:r1",
            "--store", pushes[0]["argv"][5], "--config", pushes[0]["argv"][7],
        ]])
        self.assertEqual((pushes[0]["env"][USER_ENV], pushes[0]["env"][PASSWORD_ENV]), (USER, SECRET))
        for call in self.repo.calls():
            self.assertNotIn("--move", call["argv"])
            if call not in pushes:
                self.assertFalse({USER_ENV, PASSWORD_ENV, HOST_ENV} & set(call["env"]), call["argv"])
        self.assertEqual(self.repo.registry.calls, [(HOST, self.REPOSITORY, "r1"), (HOST, self.REPOSITORY, "r1")])
        # Never a latest tag.
        self.assertFalse(any("latest" in str(c["argv"]) for c in self.repo.calls()))

    def test_a_revision_never_moves(self):
        self.repo.firmware_recipe("bios")
        target = f"{HOST}/{self.REPOSITORY}:r1"
        self.repo.registry.held[target] = fake_digest(self.STORE_TAG)
        code, stdout, stderr = self.repo.run("--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.firmware_outcome(stdout), "present")
        self.assertEqual(self.repo.pushes(), [])
        self.repo.registry.held[target] = "sha256:" + "d" * 64
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.firmware_outcome(stdout), "refused")
        self.assertIn(f"already holds sha256:{'d' * 64} under this tag; a revision tag never moves: "
                      "stop and ask the maintainer", stdout)
        self.assertEqual(self.repo.pushes(), [])

    def test_manifests_are_checked_first(self):
        # One manifest per recipe, of the kind its name says.
        recipe_dir = self.repo.recipe("demo")
        (recipe_dir / "firmware.toml").write_text(
            (self.repo.firmware_recipe("bios") / "firmware.toml").read_text(encoding="utf-8"), encoding="utf-8")
        code, _, stderr = self.repo.run("bios")
        self.assertEqual(code, 1)
        self.assertIn("recipes/demo has both media.toml and firmware.toml", stderr)
        (recipe_dir / "firmware.toml").unlink()
        # A set manifest saved as firmware.toml, and a package manifest as
        # media.toml.
        (recipe_dir / "media.toml").rename(recipe_dir / "firmware.toml")
        code, _, stderr = self.repo.run("bios")
        self.assertEqual(code, 1)
        self.assertIn("recipes/demo/firmware.toml: firmware.toml holds a set manifest", stderr)
        (recipe_dir / "firmware.toml").unlink()
        package_dir = self.repo.root / "recipes" / "bios"
        (package_dir / "firmware.toml").rename(package_dir / "media.toml")
        code, _, stderr = self.repo.run("demo")
        self.assertEqual(code, 1)
        self.assertIn("recipes/bios/media.toml: media.toml holds a package manifest", stderr)
        (package_dir / "media.toml").rename(package_dir / "firmware.toml")
        # Two revisions of one catalogue id.
        self.repo.firmware_recipe("bios-r2", revision=2)
        code, _, stderr = self.repo.run("bios")
        self.assertEqual(code, 1)
        self.assertIn("keep one manifest per catalogue id", stderr)
        self.assertEqual(self.repo.calls(), [])

    def test_a_failure_of_either_kind_holds_every_push(self):
        # Each scenario in a fresh repository: a passing, pinned set or
        # package next to one of the other kind that is unpinned or fails.
        tag = self.STORE_TAG
        scenarios = (
            ("a pinned set, an unpinned package", {}, {"pin": None}, {}, "media", "unpinned"),
            ("a pinned set, a failing package", {}, {},
             {"firmware_show": {"vendor": 'vendor\t"Other Vendor"'}}, "media", "failed"),
            ("a pinned package, an unpinned set", {"pins": (None, None)}, {}, {}, "firmware", "unpinned"),
            ("a pinned package, a set with another digest", {"pins": ("sha256:" + "e" * 64, "match")}, {}, {},
             "firmware", "mismatch"),
            ("a pinned package, a failing set", {}, {}, {"fail_import": [f"{MEDIA}:{T1}"]},
             "firmware", "failed"),
        )
        for name, sets, package, settings, passing, outcome in scenarios:
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                repo = Repository(directory)
                repo.recipe("demo", **sets)
                repo.firmware_recipe("bios", **package)
                repo.configure(**settings)
                code, stdout, _ = repo.run("--push")
                self.assertEqual(code, 1)
                media = self.outcome(stdout, T1)
                firmware = self.firmware_outcome(stdout)
                if passing == "media":
                    self.assertEqual(firmware, outcome)
                    self.assertEqual(media, "held")
                    self.assertEqual(self.outcome(stdout, T2), "held")
                else:
                    self.assertEqual(firmware, "held")
                    self.assertEqual(media, outcome)
                self.assertIn("not pushed: another one failed or has no digest pin", stdout)
                # Nothing reached the registry, and nothing was read from it.
                self.assertEqual(repo.pushes(), [])
                self.assertEqual(repo.registry.calls, [])
                self.assertIn(tag, json.dumps(repo.calls()))

    def test_rust_debug(self):
        self.assertEqual(publish.rust_debug("plain text's"), '"plain text\'s"')
        self.assertEqual(publish.rust_debug('a "b" c\\d'), '"a \\"b\\" c\\\\d"')
        self.assertEqual(publish.rust_debug("t\tn\nr\r\x01\x7f"), '"t\\tn\\nr\\r\\u{1}\\u{7f}"')


# --- the anonymous read, against a plain-HTTP registry on 127.0.0.1 -----------

REPO = MEDIA
MANIFEST_BODY = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json"}).encode()
INDEX_BODY = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json"}).encode()
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"


def manifest_path(tag):
    return f"/v2/{REPO}/manifests/{tag}"


TAGS = f"/v2/{REPO}/tags/list"


def answer(body, digest=True, status=200, headers=()):
    """A route's answer: BODY with its Docker-Content-Digest."""
    sent = dict(headers)
    if digest is True:
        sent["Docker-Content-Digest"] = "sha256:" + sha256(body)
    elif digest:
        sent["Docker-Content-Digest"] = digest
    return status, sent, body


def tag_page(tags, link=None):
    return 200, ({"Link": link} if link else {}), json.dumps({"name": REPO, "tags": tags}).encode()


class Registry:
    """A registry on 127.0.0.1: ROUTES maps a request path (with its query)
    to an answer, or to a function of the request headers giving one; any
    other path answers 404. Every request is recorded."""

    def __init__(self):
        self.routes = {}
        self.requests = []
        registry = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                registry.requests.append((self.path, dict(self.headers.items())))
                route = registry.routes.get(self.path, (404, {}, b'{"errors":[]}'))
                status, headers, body = route(self.headers) if callable(route) else route
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def read(self, tag):
        return publish.anonymous_digest(self.host, REPO, tag, environ={}, plain_http=True)

    def paths(self):
        return [path for path, _ in self.requests]


NOT_FOUND = b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
HOLD = object()


def raw_ok(body, extra=b""):
    return b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n%s\r\n" % (len(body), extra) + body


class Raw:
    """A registry on 127.0.0.1 that answers each request path with raw
    bytes, a function of the connection, or HOLD (no answer until close());
    any other path gets a 404."""

    def __init__(self):
        self.routes = {}
        self.paths = []
        self.held = []
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.host = f"127.0.0.1:{self.sock.getsockname()[1]}"
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                connection, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.one, args=(connection,), daemon=True).start()

    def one(self, connection):
        data = b""
        try:
            while b"\r\n\r\n" not in data:
                chunk = connection.recv(65536)
                if not chunk:
                    connection.close()
                    return
                data += chunk
            path = data.split(b" ", 2)[1].decode("latin-1")
            self.paths.append(path)
            route = self.routes.get(path, NOT_FOUND)
            if route is HOLD:
                self.held.append(connection)
                return
            if callable(route):
                route(connection)
            else:
                connection.sendall(route)
        except OSError:
            pass
        connection.close()

    def close(self):
        self.sock.close()
        for connection in self.held:
            connection.close()

    def read(self, tag):
        return publish.anonymous_digest(self.host, REPO, tag, environ={}, plain_http=True)


class Listener:
    """A loopback listener that records the first bytes of every connection
    and closes it: what a client sent first, TLS or CONNECT or GET."""

    def __init__(self):
        self.received = []
        self.sock = socket.create_server(("127.0.0.1", 0))
        self.host = f"127.0.0.1:{self.sock.getsockname()[1]}"
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                connection, _ = self.sock.accept()
            except OSError:
                return
            with connection:
                connection.settimeout(10)
                try:
                    self.received.append(connection.recv(256))
                except OSError:
                    self.received.append(b"")

    def close(self):
        self.sock.close()


def without_proxies(environ):
    for name in [n for n in environ if n.lower().endswith("_proxy")]:
        del environ[name]


class RegistryRead(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.routes = self.registry.routes

    def tearDown(self):
        self.registry.close()

    def listener(self):
        listener = Listener()
        self.addCleanup(listener.close)
        return listener

    def raw(self):
        raw = Raw()
        self.addCleanup(raw.close)
        return raw

    def test_malformed_answers_fail_closed(self):
        raw = self.raw()
        manifest = manifest_path("1.0")
        for name, answer_bytes in (
            ("bad status line", b"garbage\r\n\r\n"),
            ("too many headers", b"HTTP/1.1 200 OK\r\n" + b"X: y\r\n" * 150 + b"\r\n"),
            ("header line too long", b"HTTP/1.1 200 OK\r\nX: " + b"a" * 70000 + b"\r\n\r\n"),
            ("body shorter than Content-Length",
             b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\nshort"),
            ("bad Content-Length", b"HTTP/1.1 200 OK\r\nContent-Length: x\r\nConnection: close\r\n\r\nbody"),
            ("bad chunk size", b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                               b"5\r\nhello\r\nZZ\r\n"),
        ):
            raw.routes[manifest] = answer_bytes
            with self.assertRaises(publish.RegistryError, msg=name):
                raw.read("1.0")
        raw.routes[manifest] = raw_ok(MANIFEST_BODY)
        self.assertEqual(raw.read("1.0"), "sha256:" + sha256(MANIFEST_BODY))
        # The tag list, after a 404: malformed, and a Link with a space.
        raw.routes[manifest] = NOT_FOUND
        raw.routes[TAGS] = b"garbage\r\n\r\n"
        with self.assertRaises(publish.RegistryError):
            raw.read("1.0")
        raw.routes[TAGS] = raw_ok(json.dumps({"tags": ["0.9"]}).encode(),
                                  f'Link: <{TAGS}?last=x y>; rel="next"\r\n'.encode())
        with self.assertRaises(publish.RegistryError):
            raw.read("1.0")

    def test_a_silent_registry_times_out(self):
        raw = self.raw()
        raw.routes[manifest_path("1.0")] = HOLD
        outcome = []

        def read():
            try:
                raw.read("1.0")
                outcome.append("returned")
            except Exception as error:  # noqa: BLE001 - recorded for the assertion
                outcome.append(error)

        with unittest.mock.patch.object(publish, "SOCKET_TIMEOUT", 0.5):
            thread = threading.Thread(target=read, daemon=True)
            thread.start()
            thread.join(10)
        self.assertFalse(thread.is_alive(), "the read did not time out")
        self.assertIsInstance(outcome[0], publish.RegistryError)

    def test_a_slow_body_meets_the_deadline(self):
        raw = self.raw()

        def drip(connection):
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 40\r\nConnection: close\r\n\r\n")
            for _ in range(40):
                time.sleep(0.2)
                connection.sendall(b"x")

        raw.routes[manifest_path("1.0")] = drip
        start = time.monotonic()
        with unittest.mock.patch.object(publish, "REQUEST_DEADLINE", 1), \
                unittest.mock.patch.object(publish, "SOCKET_TIMEOUT", 5):
            with self.assertRaises(publish.RegistryError) as caught:
                raw.read("1.0")
        self.assertIn("no complete answer within 1 seconds", str(caught.exception))
        self.assertLess(time.monotonic() - start, 4)

    def test_plain_http_never_goes_through_a_proxy(self):
        proxy = self.listener()
        self.routes[manifest_path("1.0")] = answer(MANIFEST_BODY)
        with unittest.mock.patch.dict(os.environ):
            without_proxies(os.environ)
            os.environ.update(http_proxy=f"http://{proxy.host}", HTTP_PROXY=f"http://{proxy.host}")
            self.assertEqual(self.registry.read("1.0"), "sha256:" + sha256(MANIFEST_BODY))
        self.assertEqual(proxy.received, [])

    def test_the_https_proxy_comes_from_the_given_environment(self):
        target, proxy = self.listener(), self.listener()
        # ENVIRON names a proxy: the read asks it for a tunnel.
        with self.assertRaises(publish.RegistryError):
            publish.anonymous_digest(target.host, REPO, "1.0", environ={"HTTPS_PROXY": f"http://{proxy.host}"})
        self.assertEqual(len(proxy.received), 1)
        self.assertTrue(proxy.received[0].startswith(f"CONNECT {target.host} ".encode()), proxy.received)
        self.assertEqual(target.received, [])
        # Its NO_PROXY bypasses it, and the process's own proxy never counts.
        with self.assertRaises(publish.RegistryError):
            publish.anonymous_digest(target.host, REPO, "1.0",
                                     environ={"https_proxy": f"http://{proxy.host}", "no_proxy": "127.0.0.1"})
        with unittest.mock.patch.dict(os.environ):
            without_proxies(os.environ)
            os.environ.update(https_proxy=f"http://{proxy.host}", HTTPS_PROXY=f"http://{proxy.host}")
            with self.assertRaises(publish.RegistryError):
                publish.anonymous_digest(target.host, REPO, "1.0", environ={})
        self.assertEqual(len(proxy.received), 1)
        self.assertEqual([first[:1] for first in target.received], [b"\x16", b"\x16"])
        self.assertIsNone(publish.environ_proxy({}, "registry.invalid"))
        self.assertEqual(publish.environ_proxy({"HTTPS_PROXY": "http://p:1"}, "registry.invalid"), "http://p:1")
        self.assertEqual(publish.environ_proxy({"HTTPS_PROXY": "http://p:1", "https_proxy": "http://q:2"},
                                               "registry.invalid"), "http://q:2")
        self.assertIsNone(publish.environ_proxy({"HTTPS_PROXY": "http://p:1", "NO_PROXY": ".invalid"},
                                                "registry.invalid:5000"))

    def refused(self, tag, fragment):
        with self.assertRaises(publish.RegistryError) as caught:
            self.registry.read(tag)
        self.assertIn(fragment, str(caught.exception))

    def test_a_manifest_and_an_index_are_read_by_their_bytes(self):
        self.routes[manifest_path("1.0")] = answer(MANIFEST_BODY)
        # An index is served only to a reader that accepts the type.
        self.routes[manifest_path("index")] = (
            lambda headers: answer(INDEX_BODY) if OCI_INDEX in headers.get("Accept", "") else (404, {}, b"")
        )
        # A Docker schema 2 manifest likewise, and its tag is listed.
        self.routes[manifest_path("docker")] = (
            lambda headers: answer(b'{"docker": 1}') if DOCKER_MANIFEST in headers.get("Accept", "") else (404, {}, b"")
        )
        self.routes[TAGS] = tag_page(["1.0", "index", "docker"])
        self.routes[manifest_path("plain")] = answer(MANIFEST_BODY, digest=None)
        self.assertEqual(self.registry.read("1.0"), "sha256:" + sha256(MANIFEST_BODY))
        self.assertEqual(self.registry.read("index"), "sha256:" + sha256(INDEX_BODY))
        self.assertEqual(self.registry.read("docker"), "sha256:" + sha256(b'{"docker": 1}'))
        self.assertEqual(self.registry.read("plain"), "sha256:" + sha256(MANIFEST_BODY))
        accept = [headers.get("Accept") for path, headers in self.registry.requests if "/manifests/" in path]
        self.assertEqual(set(accept), {publish.ACCEPT})
        self.assertEqual(
            [t.strip() for t in publish.ACCEPT.split(",")],
            ["application/vnd.oci.image.manifest.v1+json", OCI_INDEX, DOCKER_MANIFEST,
             "application/vnd.docker.distribution.manifest.list.v2+json"],
        )
        for _, headers in self.registry.requests:
            self.assertNotIn("Authorization", headers)
            self.assertEqual(headers.get("User-Agent"), publish.USER_AGENT)

    def test_an_absent_tag_is_confirmed_by_the_tag_list(self):
        # No such repository: both reads say 404.
        self.assertIsNone(self.registry.read("1.0"))
        self.assertEqual(self.registry.paths(), [manifest_path("1.0"), TAGS])
        # The repository exists without the tag; a null tag list too.
        for page in (tag_page(["0.9", "1.0-720k"]), tag_page(None), tag_page([])):
            self.routes[TAGS] = page
            self.assertIsNone(self.registry.read("1.0"))

    def test_a_listed_tag_the_read_cannot_see_is_refused(self):
        self.routes[TAGS] = tag_page(["0.9", "1.0"])
        self.refused("1.0", "the tag list names '1.0'")
        # Also a near match: case or surrounding space.
        for listed, tag in (("1.0 ", "1.0"), (" 1.0", "1.0"), ("1.0-720K", "1.0-720k"), ("1.0\t", "1.0")):
            self.routes[TAGS] = tag_page(["0.9", listed])
            self.refused(tag, f"the tag list names {listed!r}")
        self.routes[TAGS] = tag_page(["1.0.1", "1.00", "11.0"])
        self.assertIsNone(self.registry.read("1.0"))

    def test_the_tag_list_is_followed_page_by_page(self):
        self.routes[TAGS] = tag_page(["0.1"], link=f'<{TAGS}?n=1&last=0.1>; rel="next"')
        self.routes[f"{TAGS}?n=1&last=0.1"] = tag_page(["0.2"], link=f'<{TAGS}?n=1&last=0.2>; rel="next"')
        self.routes[f"{TAGS}?n=1&last=0.2"] = tag_page(["1.0"])
        self.refused("1.0", "the tag list names '1.0'")
        self.assertIsNone(self.registry.read("2.0"))
        # An absolute link on the same registry is followed too.
        self.routes[TAGS] = tag_page(["0.1"], link=f'<http://{self.registry.host}{TAGS}?n=1&last=0.1>; rel=next')
        self.refused("1.0", "the tag list names '1.0'")

    def test_a_tag_list_it_cannot_finish_is_refused(self):
        # Endless pages.
        self.routes[TAGS] = tag_page(["0.1"], link=f'<{TAGS}>; rel="next"')
        self.refused("1.0", f"runs past {publish.MAX_TAG_PAGES} pages")
        self.assertEqual(self.registry.paths().count(TAGS), publish.MAX_TAG_PAGES)
        for link in (f'<http://localhost:1{TAGS}?n=1>; rel="next"', '</v2/other/tags/list?n=1>; rel="next"',
                     f'<{TAGS}?n=1>; rel="prev"', "garbage"):
            self.routes[TAGS] = tag_page(["0.1"], link=link)
            self.refused("1.0", "next page" if "garbage" not in link and "prev" not in link else "Link header")
        # Another scheme on the same host, or a fragment, is no page of this
        # list either, though the page they name exists.
        self.routes[f"{TAGS}?n=1"] = tag_page(["0.2"])
        for link in (f'<https://{self.registry.host}{TAGS}?n=1>; rel="next"', f'<{TAGS}?n=1#frag>; rel="next"'):
            self.routes[TAGS] = tag_page(["0.1"], link=link)
            self.refused("1.0", "is not this tag list")
        del self.routes[f"{TAGS}?n=1"]
        # Nesting too deep for the JSON reader; a repeated key.
        self.routes[TAGS] = (200, {}, b"[" * 200000 + b"]" * 200000)
        self.refused("1.0", "not a tag list")
        self.routes[TAGS] = (200, {}, b'{"tags": ["1.0"], "tags": []}')
        self.refused("1.0", "not a tag list")
        # A link no URL parser takes.
        self.routes[TAGS] = tag_page(["0.1"], link='<http://[::1/x>; rel="next"')
        self.refused("1.0", "a Link header with an invalid URL")
        # A later page that vanishes, a page that is no tag list, a refusal.
        self.routes[TAGS] = tag_page(["0.1"], link=f'<{TAGS}?n=1&last=0.1>; rel="next"')
        self.refused("1.0", "HTTP 404 in the middle of the tag list")
        for page in ((200, {}, b"not json"), (200, {}, b'{"tags": "1.0"}'), (200, {}, b'{"tags": [1]}'),
                     (200, {}, b"[]"), (401, {}, b""), (403, {}, b""), (500, {}, b"")):
            self.routes[TAGS] = page
            with self.assertRaises(publish.RegistryError, msg=page):
                self.registry.read("1.0")
        self.routes[TAGS] = (200, {}, b" " * (publish.MAX_TAG_PAGE + 1))
        self.refused("1.0", "larger than")

    def test_any_other_answer_fails_closed(self):
        for status in (401, 403, 500, 202, 204):
            self.routes[manifest_path("1.0")] = answer(MANIFEST_BODY, status=status,
                                                       headers={"WWW-Authenticate": 'Basic realm="x"'})
            self.refused("1.0", f"HTTP {status}")

    def test_a_redirect_is_never_followed(self):
        self.routes[manifest_path("elsewhere")] = answer(MANIFEST_BODY)
        for status in (301, 302, 307, 308):
            self.routes[manifest_path("1.0")] = (status, {"Location": manifest_path("elsewhere")}, b"")
            self.refused("1.0", f"HTTP {status}")
        self.assertNotIn(manifest_path("elsewhere"), self.registry.paths())

    def test_the_bytes_decide(self):
        self.routes[manifest_path("1.0")] = answer(MANIFEST_BODY, digest="sha256:" + "0" * 64)
        self.refused("1.0", "does not match the bytes")
        self.routes[manifest_path("1.0")] = answer(b"x" * (publish.MAX_MANIFEST + 1))
        self.refused("1.0", "larger than")

    def test_https_only_unless_a_test_asks_for_loopback_http(self):
        # The default is HTTPS: the first byte a loopback listener gets is a
        # TLS handshake record (0x16), never a plain "GET"; a proxy in the
        # process's environment changes nothing.
        listener, proxy = self.listener(), self.listener()
        with unittest.mock.patch.dict(os.environ):
            without_proxies(os.environ)
            os.environ.update(https_proxy=f"http://{proxy.host}", HTTPS_PROXY=f"http://{proxy.host}")
            with self.assertRaises(publish.RegistryError):
                publish.anonymous_digest(listener.host, REPO, "1.0", environ={})
        self.assertEqual([first[:1] for first in listener.received], [b"\x16"])
        self.assertEqual(proxy.received, [])
        # Plain HTTP is refused for any host but a loopback one, before any
        # connection.
        for host in ("registry.invalid", "registry.invalid:5000", "10.0.0.1:5000"):
            with self.assertRaises(publish.RegistryError) as caught:
                publish.anonymous_digest(host, REPO, "1.0", environ={}, plain_http=True)
            self.assertIn("only for a loopback registry", str(caught.exception))
        for host, loopback in (("localhost", True), ("localhost:5000", True), ("127.0.0.1:5000", True),
                               ("127.1.2.3", True), ("[::1]:5000", True), ("[::1]", True),
                               ("registry.invalid", False), ("10.0.0.1:5000", False), ("[2001:db8::1]", False)):
            self.assertEqual(publish.is_loopback(host), loopback, host)

    def test_manifest_digest(self):
        body = b'{"schemaVersion":2}'
        digest = "sha256:" + sha256(body)
        self.assertEqual(publish.manifest_digest(body, None), digest)
        self.assertEqual(publish.manifest_digest(body, f" {digest} "), digest)
        with self.assertRaises(publish.RegistryError):
            publish.manifest_digest(body, "sha256:" + "0" * 64)
        with self.assertRaises(publish.RegistryError):
            publish.manifest_digest(b"x" * (publish.MAX_MANIFEST + 1), None)


if __name__ == "__main__":
    unittest.main()
