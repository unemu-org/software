# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests of lib/publish.py, offline: a throwaway repository with small
recipe files, a fake unemu that logs every call it gets and keeps a store of
its own, a fake registry read for the pipeline, and a plain-HTTP registry on
127.0.0.1 for the anonymous read itself. Nothing here reaches another host
or a real registry.

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
import socket
import stat
import subprocess
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
recipes = publish.recipes

PIN = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"
# A pin of a release that does not exist (lib/unemu.py checks the format).
PIN_TOML = f"""# a comment
commit = "{PIN}"
tag = "v9.9.9"
version = "9.9.9"

[archive.x86_64-unknown-linux-gnu]
file = "unemu-9.9.9-x86_64-unknown-linux-gnu.tar.gz"
sha256 = "{'a' * 64}"
size = 1
unemu_sha256 = "{'b' * 64}"
unemu_size = 1
"""
# What a release build of unemu prints for --version, in the shape of
# unemu's version notice, its commit line as
# crates/platforms/src/lib.rs (commit_line) writes it: the third-party
# notice names a commit of its own.
VERSION = f"""unemu 9.9.9
emulator of period IBM-compatible PCs, command line
licence: AGPL-3.0-or-later
source: https://github.com/unemu-org/unemu/tree/v9.9.9
commit: {PIN}
full licence texts: LICENSE, and THIRD-PARTY-LICENSES.md

third-party components:

Example 1.0 (upstream commit {OTHER})
source: https://example.invalid/{OTHER}"""
HOST = "registry.invalid"
USER = "synthetic-user"
SECRET = "synthetic-secret-value"
USER_ENV, PASSWORD_ENV, HOST_ENV = publish.USER_ENV, publish.PASSWORD_ENV, publish.HOST_ENV

# The fake unemu: it appends every call (arguments, environment and the
# registries file it was given) to a log and answers as unemu 0.0.4 does on
# the lines the pipeline reads (or as the test's configuration says, to
# check that the pipeline notices). `recipe build` reads the recipe's
# identity and writes a small artifact for it into the store, an OCI layout
# as unemu's (images/index.json, images/blobs/sha256/), which `image media
# show` and `image firmware show` read; a push records the target in a JSON
# file the fake registry reads.
FAKE_CLI = r'''#!@PYTHON@ -I
import hashlib, json, os, sys, tomllib

with open(@CONFIG@) as handle:
    config = json.load(handle)
argv = sys.argv[1:]
entry = {"argv": argv, "env": dict(os.environ)}
if "--config" in argv:
    with open(argv[argv.index("--config") + 1]) as handle:
        entry["config"] = handle.read()
with open(@LOG@, "a") as handle:
    handle.write(json.dumps(entry) + "\n")


@ARTIFACT@


def digest_of(ref):
    return "sha256:" + hashlib.sha256(artifact(ref)[0]).hexdigest()


def value(flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


def tags(store):
    path = os.path.join(store, "images", "index.json")
    if not os.path.exists(path):
        return {}
    with open(path) as handle:
        index = json.load(handle)
    return {m["annotations"]["org.opencontainers.image.ref.name"]: m["digest"] for m in index["manifests"]}


def write_tag(store, ref, name):
    """Write REF's artifact into STORE and name it NAME."""
    blobs = os.path.join(store, "images", "blobs", "sha256")
    os.makedirs(blobs, exist_ok=True)
    manifest, parts = artifact(ref)
    for data in [manifest, *parts]:
        with open(os.path.join(blobs, hashlib.sha256(data).hexdigest()), "wb") as handle:
            handle.write(data)
    held = tags(store)
    held[name] = digest_of(ref)
    index = {"schemaVersion": 2, "manifests": [
        {"digest": d, "size": len(manifest), "annotations": {"org.opencontainers.image.ref.name": n}}
        for n, d in held.items()]}
    with open(os.path.join(store, "images", "index.json"), "w") as handle:
        json.dump(index, handle)


if (argv[:2] in (["image", "push"], ["recipe", "push"])
        and "--dry-run" not in argv and "push_response" in config):
    response = config["push_response"]
    print(response.get("stdout", ""))
    print(response.get("stderr", ""), file=sys.stderr)
    sys.exit(response.get("status", 1))
if argv == ["--version"]:
    if config.get("version") is None:
        print("unknown or extra arguments", file=sys.stderr)
        sys.exit(2)
    print(config["version"])
    sys.exit(0)
if argv[:2] == ["recipe", "check"]:
    for line in config.get("check_lines", []):
        print(line)
    for path in argv[2:]:
        if path not in config.get("check_silent", []):
            print(path + ": ok, recipe")
    print(str(len(argv) - 2) + " files checked, " + str(config.get("check_findings", 0)) + " findings")
    sys.exit(1 if config.get("check_findings") else 0)
if argv[:2] == ["library", "scan"]:
    os.makedirs(value("--store"))
    sys.exit(0)
if argv[:2] == ["recipe", "push"]:
    from pathlib import Path
    path, target = Path(argv[2]), argv[3]
    recipe = tomllib.loads(path.read_text())
    identity = recipe.get("media", recipe.get("firmware"))
    tag = (identity.get("release", "") + "-" if identity.get("release") else "") + "r" + str(identity["revision"])
    output = identity["name"] + ":" + tag
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    if "--dry-run" in argv:
        print("target:" + config.get("recipe_target", target))
        print("image:" + config.get("recipe_image", digest))
        print("recipe:" + path.name)
        print("output:" + config.get("recipe_output", output + "\t" + identity.get("digest", "")))
        print("guard:" + config.get("recipe_guard", "allowed"))
        sys.exit(config.get("recipe_dry_status", 0))
    with open(@REGISTRY@, "w") as handle:
        json.dump({"path": str(path), "digest": digest}, handle)
    word = config.get("recipe_word", "pushed")
    ending = config.get("recipe_digest", digest)
    if word == "moved":
        ending = "sha256:" + "f" * 64 + " -> " + ending + " (the same output " + config.get("moved_output", identity["digest"]) + ")"
    print(word + "\t" + config.get("recipe_push_target", target) + "\t" + ending)
    sys.exit(config.get("recipe_push_status", 0))
if argv[:2] == ["recipe", "pull"]:
    from pathlib import Path
    with open(@REGISTRY@) as handle:
        pushed = json.load(handle)
    path, destination = Path(pushed["path"]), Path(value("--to"))
    recipe = tomllib.loads(path.read_text())
    names = [path.name]
    if recipe.get("work"):
        names.append(recipe["work"] + ".work.toml")
    names += [v["name"] for v in recipe.get("input", {}).values() if v.get("bundle")]
    names += [p.relative_to(path.parent).as_posix() for p in Path(str(path) + ".lock").rglob("*") if p.is_file()]
    for name in names:
        if name == config.get("pull_missing"):
            continue
        dest = destination / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((path.parent / name).read_bytes() + (b"different" if name == config.get("pull_different") else b""))
    if config.get("pull_extra"):
        (destination / "extra.txt").write_text("extra")
    if config.get("pull_link"):
        (destination / "link").symlink_to(path)
    print("pulled\t" + argv[2] + "\t" + config.get("pull_digest", pushed["digest"]) + "\t" + str(destination / path.name))
    sys.exit(config.get("recipe_pull_status", 0))
if argv[:2] == ["recipe", "build"]:
    with open(argv[2], "rb") as handle:
        recipe = tomllib.load(handle)
    kind = "media" if "media" in recipe else "firmware"
    identity = recipe[kind]
    release = identity.get("release")
    tag = (release + "-" if release else "") + "r" + str(identity["revision"])
    ref = identity["name"] + ":" + tag
    if ref in config.get("fail_build", []):
        print("  edition: missing")
        print("not built: missing 1 of 2 inputs; nothing was tagged")
        sys.exit(1)
    store = value("--store")
    write_tag(store, ref, ref)
    if release and kind == "media":
        write_tag(store, ref, identity["name"] + ":" + release)
    print("  input: kept as stored")
    if config.get("build_raw"):
        sys.stdout.flush()
        sys.stdout.buffer.write(config["build_raw"].encode("latin-1"))
        sys.exit(0)
    state = "locked" if "digest" in identity else "draft"
    print(config.get("build_line", "built " + ref + "\t" + digest_of(ref) + "\t" + state))
    sys.exit(0)
if argv[:1] == ["image"] and argv[2:3] == ["show"]:
    tag = argv[3]
    held = tags(value("--store"))
    if tag not in held:
        print("tag not found: " + tag, file=sys.stderr)
        sys.exit(1)
    third = "ibm-pc" if argv[1] == "media" else '"Demo BIOS"'
    print(config.get("show_first", tag + "\t" + held[tag] + "\t" + third))
    print("min-app-version\t0.0.3")
    print(config.get("push_line", "push\tany configured registry, including unemu/"))
    for line in config.get("show_after", []):
        print(line)
    sys.exit(0)
if argv[:2] == ["image", "push"]:
    ref, target = argv[2], argv[3]
    if "--dry-run" in argv:
        print("target:" + config.get("dry_target", target))
        print("image:" + config.get("dry_image", tags(value("--store")).get(ref, digest_of(ref))))
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

MEDIA_RECIPE = """# A test set.
recipe = "1.0"
work = "@WORK@"

[media]
name = "unemu/media/ibm-pc/@WORK@"
platform = "ibm-pc"
release = "@RELEASE@"
revision = 1
@DIGEST@
[title]
version = "@VERSION@"
description = "A test set."

[license]
expression = "GPL-2.0-or-later"
terms = "licenses"
source = "source"

[input.licenses]
name = "licenses.txt"
size = 1
sha256 = "@A@"
bundle = true
"""

FIRMWARE_RECIPE = """recipe = "1.0"

[firmware]
name = "unemu/firmware/ibm-pc/@ID@"
platform = "ibm-pc"
revision = @REVISION@
@DIGEST@
[package]
title = "Demo BIOS"

[license]
expression = "GPL-2.0-or-later"
source = "source"
"""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# The artifact the fake unemu builds for a ref: a manifest naming a config
# and one layer, each a blob of its own; the same function in the fake.
ARTIFACT = '''def artifact(ref):
    parts = [("config of " + ref).encode(), ("layer of " + ref).encode()]
    descriptors = [{"digest": "sha256:" + hashlib.sha256(p).hexdigest(), "size": len(p)} for p in parts]
    manifest = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                           "config": descriptors[0], "layers": descriptors[1:]}, sort_keys=True).encode()
    return manifest, parts'''
exec(ARTIFACT)


def fake_digest(ref):
    """The digest the fake unemu gives REF: its manifest's."""
    return "sha256:" + sha256(artifact(ref)[0])


def store_tags(store):
    """What the fake store's index names: ref -> digest."""
    index = json.loads((pathlib.Path(store) / "images" / "index.json").read_text())
    return {m["annotations"]["org.opencontainers.image.ref.name"]: m["digest"] for m in index["manifests"]}


# The demo work's repository, its two sets' revision tags and their release
# aliases (recipes a.media.toml and b.media.toml of its folder), and the
# demo package's repository; a package's store tag is its own ref.
MEDIA = "unemu/media/ibm-pc/demo"
T1, T2 = "2.0-r1", "2.0-720k-r1"
A1, A2 = "2.0", "2.0-720k"
FIRMWARE_REPOSITORY = "unemu/firmware/ibm-pc/demo-bios"
FIRMWARE_REF = f"{FIRMWARE_REPOSITORY}:r1"
M1, M2 = "recipes/media/demo/a.media.toml", "recipes/media/demo/b.media.toml"
BIOS = "recipes/firmware/demo-bios.firmware.toml"


class FakeRegistry:
    """The anonymous registry read: what the test put there, plus what the
    fake unemu pushed."""

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
    """A throwaway repository root with recipe files and a fake CLI."""

    def __init__(self, directory):
        self.base = pathlib.Path(directory)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / publish.PIN_FILE).write_text(PIN_TOML, encoding="ascii")
        self.log = self.base / "cli-log.jsonl"
        self.config = self.base / "cli-config.json"
        self.pushed = self.base / "pushed.json"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.cli = self.bin / "unemu"
        self.configure()
        text = FAKE_CLI
        for name, value in (("@PYTHON@", sys.executable), ("@CONFIG@", repr(str(self.config))),
                            ("@LOG@", repr(str(self.log))), ("@REGISTRY@", repr(str(self.pushed))),
                            ("@ARTIFACT@", ARTIFACT)):
            text = text.replace(name, value)
        self.cli.write_text(text, encoding="utf-8")
        self.cli.chmod(stat.S_IRWXU)
        self.registry = FakeRegistry(self.pushed)

    def configure(self, **settings):
        settings.setdefault("version", VERSION)
        self.config.write_text(json.dumps(settings), encoding="utf-8")

    def media(self, work="demo", pins=("match", "match"), version="2.0"):
        """The work's two recipes, :VERSION-r1 (a.media.toml) and
        :VERSION-720k-r1 (b.media.toml); each pin is 'match' (what the fake
        CLI gives), None (a draft) or a digest. Their paths."""
        folder = self.root / "recipes" / "media" / work
        folder.mkdir(parents=True)
        paths = []
        for name, release, pin in (("a", version, pins[0]), ("b", f"{version}-720k", pins[1])):
            if pin == "match":
                pin = fake_digest(f"unemu/media/ibm-pc/{work}:{release}-r1")
            text = (MEDIA_RECIPE.replace("@WORK@", work).replace("@RELEASE@", release)
                    .replace("@VERSION@", version).replace("@A@", "a" * 64)
                    .replace("@DIGEST@", f'digest = "{pin}"\n' if pin else ""))
            (folder / f"{name}.media.toml").write_text(text, encoding="utf-8")
            paths.append(f"recipes/media/{work}/{name}.media.toml")
        return paths

    def firmware(self, catalogue_id="demo-bios", revision=1, pin="match", name=None):
        """The recipe of unemu/firmware/ibm-pc/CATALOGUE_ID:rREVISION; PIN is
        'match' (what the fake CLI gives), None or a digest. Its path."""
        folder = self.root / "recipes" / "firmware"
        folder.mkdir(parents=True, exist_ok=True)
        if pin == "match":
            pin = fake_digest(f"unemu/firmware/ibm-pc/{catalogue_id}:r{revision}")
        text = (FIRMWARE_RECIPE.replace("@ID@", catalogue_id).replace("@REVISION@", str(revision))
                .replace("@DIGEST@", f'digest = "{pin}"\n' if pin else ""))
        path = f"recipes/firmware/{name or catalogue_id}.firmware.toml"
        (self.root / path).write_text(text, encoding="utf-8")
        return path

    def environ(self, push=False, **extra):
        env = {"PATH": os.environ.get("PATH", os.defpath), HOST_ENV: HOST}
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

    def builds(self):
        """The recipe files `unemu recipe build` was given, relative to the
        root."""
        return [pathlib.Path(c["argv"][2]).relative_to(self.root).as_posix()
                for c in self.calls() if c["argv"][:2] == ["recipe", "build"]]

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
    def test_calls_follow_the_recipes(self):
        self.repo.media()
        code, stdout, stderr = self.repo.run()
        self.assertEqual(code, 0, stderr)
        calls = self.repo.calls()
        self.assertEqual(calls[0]["argv"], ["--version"])
        # Every recipe checked by unemu once, before anything is built.
        self.assertEqual(calls[1]["argv"], ["recipe", "check", str(self.repo.root / M1), str(self.repo.root / M2)])
        builds = [c["argv"] for c in calls if c["argv"][:2] == ["recipe", "build"]]
        store = builds[0][builds[0].index("--store") + 1]
        self.assertTrue(store.startswith(str(self.repo.root / "build" / "publish.")), store)
        self.assertEqual(builds, [["recipe", "build", str(self.repo.root / m), "--store", store] for m in (M1, M2)])
        shows = [c["argv"] for c in calls if c["argv"][:3] == ["image", "media", "show"]]
        self.assertEqual(shows, [["image", "media", "show", f"{MEDIA}:{t}", "--store", store] for t in (T1, T2)])
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
        # The order: build, show and dry runs of one set before the next.
        steps = [c["argv"][:2] for c in calls[2:]]
        self.assertEqual(steps, [["recipe", "build"], ["image", "media"], ["image", "push"], ["image", "push"]] * 2)
        # No registry is read and nothing is pushed without --push.
        self.assertEqual(self.repo.registry.calls, [])
        self.assertEqual(self.repo.pushes(), [])
        # The work directory, store included, is gone.
        self.assertEqual(list((self.repo.root / "build").iterdir()), [])

    def test_build_options_reach_the_build_only(self):
        self.repo.media()
        first, second = self.repo.base / "library-1", self.repo.base / "library-2"
        first.mkdir()
        second.mkdir()
        code, _, stderr = self.repo.run("--fetch", "--library", str(first), "--library", str(second))
        self.assertEqual(code, 0, stderr)
        for call in self.repo.calls():
            argv = call["argv"]
            if argv[:2] == ["recipe", "build"]:
                self.assertEqual(argv[5:], ["--fetch", "--library", str(first), "--library", str(second)])
            else:
                self.assertNotIn("--fetch", argv)
                self.assertNotIn("--library", argv)

    def test_unemu_environment_is_its_own(self):
        self.repo.media()
        environ = self.repo.environ(push=True, GITHUB_TOKEN="x", UNEMU_PROMOTE_PASSWORD="x", KEPT="x")
        code, _, stderr = self.repo.run("--push", environ=environ)
        self.assertEqual(code, 0, stderr)
        pushes = self.repo.pushes()
        self.assertEqual(len(pushes), 4)
        for call in self.repo.calls():
            # macOS adds its own __CF_USER_TEXT_ENCODING to every process.
            names = sorted(k for k in call["env"] if not k.startswith("__CF_"))
            expected = ["HOME", "LC_ALL", "PATH", "TMPDIR", "TZ"]
            if call in pushes:
                expected = sorted(expected + [USER_ENV, PASSWORD_ENV, HOST_ENV])
            self.assertEqual(names, expected, call["argv"])
            self.assertEqual((call["env"]["LC_ALL"], call["env"]["TZ"]), ("C", "UTC"))
            self.assertTrue(call["env"]["HOME"].startswith(str(self.repo.root / "build" / "publish.")))

    def test_selection(self):
        self.repo.media()
        other = self.repo.media(work="other")
        code, _, stderr = self.repo.run(f"{MEDIA}:{T2}")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.repo.builds(), [M2])
        for selector, expected in ((M1, [M1]), ("recipes/media/other", other), ("recipes/media/other/", other),
                                   ("recipes/media", [M1, M2, *other]), ("recipes", [M1, M2, *other])):
            self.repo.log.unlink(missing_ok=True)
            code, _, stderr = self.repo.run(selector)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(self.repo.builds(), expected, selector)
        self.repo.log.unlink(missing_ok=True)
        code, _, stderr = self.repo.run()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.repo.builds(), [M1, M2, *other])
        # An alias, a path without the platform, the tags of earlier
        # releases, a part of a name and anything else select nothing.
        for selector in ("nothing", f"{MEDIA}:9.9-r1", "demo:2.0-r1", f"{MEDIA}:{A1}", "unemu/media/demo:2.0-r1",
                         "unemu/media/demo:2.0", "recipes/media/de", "recipes/media/demo/a", "demo", "/", ""):
            self.repo.log.unlink(missing_ok=True)
            code, _, stderr = self.repo.run(selector)
            self.assertEqual(code, 2, selector)
            self.assertIn("neither a recipe, a folder of recipes nor a ref (unemu/media/ibm-pc/<work>:<release>-rN "
                          "or unemu/firmware/ibm-pc/<id>:rN)", stderr)
            self.assertEqual(self.repo.calls(), [])

    def test_usage_errors(self):
        self.repo.media()
        existing = self.repo.base / "existing"
        existing.mkdir()
        for args in (("--store", str(existing)), ("--no-build",), ("--no-build", "--store", str(self.repo.base / "x")),
                     ("--no-build", "--store", str(existing), "--fetch"),
                     ("--no-build", "--store", str(existing), "--library", str(existing)),
                     ("--library", str(self.repo.base / "no-such-library")),
                     ("--experimental", "4a"), ("--experimental", ""),
                     ("--experimental", "-1"), ("--experimental", "0"), ("--experimental", "01"),
                     ("--experimental", "1" * 21), ("--allow-unpinned",), ("--bogus",)):
            code, _, _ = self.repo.run(*args)
            self.assertEqual(code, 2, args)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = publish.main([], root=self.repo.root, registry=self.repo.registry,
                                environ=self.repo.environ())
        self.assertEqual(code, 2)
        self.assertIn("--cli UNEMU", stderr.getvalue())
        self.assertEqual(self.repo.calls(), [])
        for run_number in ("1", "9", "10", "9" * 20):
            self.assertTrue(publish.RUN.fullmatch(run_number), run_number)

    def test_registry_host_is_checked_as_unemu_checks_it(self):
        self.repo.media()
        for text in ("registry", "https://registry.invalid", "registry.invalid/x", "registry.invalid:",
                     "registry.invalid:65536", "registry.invalid:99999", "registry.invalid:x",
                     ":5000", "-registry.invalid", "registry-.invalid", "registry..invalid",
                     "reg_istry.invalid", "[::1", "[]:5000", "[::1]x", "[::g]", "régistry.invalid"):
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
        self.repo.media()
        environ = self.repo.environ(PATH=f"{self.repo.bin}{os.pathsep}{os.environ.get('PATH', '')}")
        code, _, stderr = self.repo.run(environ=environ, cli="unemu")
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"unemu {self.repo.cli}", stderr)
        self.repo.log.unlink()
        code, _, stderr = self.repo.run(cli="unemu-not-on-path")
        self.assertEqual(code, 2)
        self.assertIn("not found on PATH", stderr)
        self.assertEqual(self.repo.calls(), [])

    def test_given_store_is_kept(self):
        self.repo.media()
        store = self.repo.base / "kept-store"
        code, _, stderr = self.repo.run("--store", str(store))
        self.assertEqual(code, 0, stderr)
        builds = [c["argv"] for c in self.repo.calls() if c["argv"][:2] == ["recipe", "build"]]
        self.assertEqual({a[a.index("--store") + 1] for a in builds}, {str(store.resolve())})
        self.assertEqual(sorted(store_tags(store)),
                         sorted(f"{MEDIA}:{t}" for t in (T1, T2, A1, A2)))
        self.assertEqual(list((self.repo.root / "build").iterdir()), [])

    def test_report(self):
        self.repo.media(pins=("match", None))
        self.repo.firmware()
        report = self.repo.base / "report.json"
        code, _, _ = self.repo.run("--report", str(report))
        self.assertEqual(code, 1)
        rows = json.loads(report.read_text())
        self.assertEqual([(r["ref"], r["recipe"], r["alias"], r["outcome"]) for r in rows], [
            (FIRMWARE_REF, BIOS, None, "dry run"),
            (f"{MEDIA}:{T1}", M1, A1, "dry run"),
            (f"{MEDIA}:{T2}", M2, A2, "unpinned"),
        ])
        self.assertEqual(rows[1]["digest"], fake_digest(f"{MEDIA}:{T1}"))
        self.assertEqual((rows[0]["licence"], rows[0]["repository"], rows[0]["tag"]),
                         ("GPL-2.0-or-later", FIRMWARE_REPOSITORY, "r1"))


class Digests(Case):
    def test_pinned_digests_match(self):
        self.repo.media()
        code, stdout, stderr = self.repo.run()
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"registry {HOST} (dry run)", stdout)
        self.assertEqual(stdout.splitlines()[1].split(), ["REPOSITORY", "TAG", "ALIAS", "DIGEST", "OUTCOME", "LICENCE"])
        for tag, alias in ((T1, A1), (T2, A2)):
            row = self.row(stdout, MEDIA, tag)
            self.assertEqual(row[2:4], [alias, fake_digest(f"{MEDIA}:{tag}")])
            self.assertEqual(row[4:], ["dry", "run", "GPL-2.0-or-later"])
        self.assertIn(f"unemu pin {PIN} (unemu v9.9.9, unemu.toml)", stderr)
        self.assertIn(f"unemu --version reports commit {PIN}, as pinned", stderr)

    def test_mismatch_fails(self):
        self.repo.media(pins=("match", "sha256:" + "e" * 64))
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "dry")
        self.assertEqual(self.outcome(stdout, T2), "mismatch")
        self.assertIn(f"unemu gives {fake_digest(f'{MEDIA}:{T2}')}, the recipe pins sha256:{'e' * 64}", stdout)

    def test_a_draft_is_unpinned_and_never_pushed(self):
        self.repo.media(pins=("match", None))
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        row = self.row(stdout, MEDIA, T2)
        self.assertEqual(row[3:5], [fake_digest(f"{MEDIA}:{T2}"), "unpinned"])
        self.assertIn("a draft: the recipe has no digest pin (unemu recipe pin); never pushed", stdout)
        self.assertIn(f"{M2}: {MEDIA}:{T2} (a draft)", self.repo.run()[2])
        # A build that says draft is unpinned, whatever the recipe file says.
        self.repo.configure(build_line=f"built {MEDIA}:{T1}\t{fake_digest(f'{MEDIA}:{T1}')}\tdraft")
        code, stdout, _ = self.repo.run(f"{MEDIA}:{T1}")
        self.assertEqual((code, self.outcome(stdout, T1)), (1, "unpinned"))
        self.repo.configure()
        # With --push, one draft holds every push.
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "held")
        self.assertEqual(self.outcome(stdout, T2), "unpinned")
        self.assertEqual(self.repo.pushes(), [])
        self.assertEqual(self.repo.registry.calls, [])

    def test_a_failed_build_fails_its_set(self):
        self.repo.media()
        self.repo.configure(fail_build=[f"{MEDIA}:{T1}"])
        code, stdout, stderr = self.repo.run()
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "failed")
        self.assertIn("recipe build: not built: missing 1 of 2 inputs; nothing was tagged", stdout)
        # Its report reaches stderr; the other set goes on.
        self.assertIn("  edition: missing\n", stderr)
        self.assertEqual(self.outcome(stdout, T2), "dry")

    def test_guard_and_show_must_allow_unemu(self):
        self.repo.media()
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

    def test_the_cli_must_name_the_same_ref_target_and_digest(self):
        self.repo.media()
        ref = f"{MEDIA}:{T1}"
        digest = fake_digest(ref)
        other = "sha256:" + "c" * 64
        for settings, fragment in (
            ({"build_line": f"built {MEDIA}:9.9-r1\t{digest}\tlocked"}, "recipe build: unexpected last line"),
            # The alias the store moved with the revision is not the ref.
            ({"build_line": f"built {MEDIA}:{A1}\t{digest}\tlocked"}, "recipe build: unexpected last line"),
            ({"build_line": f"built {ref}\tsha256:{'C' * 64}\tlocked"}, "recipe build: unexpected last line"),
            ({"build_line": f"built {ref}\t{digest}\tlocked\textra"}, "recipe build: unexpected last line"),
            ({"build_line": f"built {ref}\t{digest}"}, "recipe build: unexpected last line"),
            ({"build_line": f"pinned {ref}\t{digest}\tlocked"}, "recipe build: unexpected last line"),
            ({"build_raw": f"built {ref}\t\xff\n"}, "not UTF-8"),
            ({"show_first": f"{MEDIA}:9.9-r1\t{digest}\tibm-pc"}, "media show: the first line"),
            ({"show_first": f"{ref}\t{other}\tibm-pc"}, "media show: the first line"),
            ({"show_first": f"{ref}\t{digest}"}, "media show: the first line"),
            ({"dry_target": f"{HOST}/{MEDIA}:9.9-r1"}, "the plan names another target or image"),
            # One target for both dry runs: the alias's names another.
            ({"dry_target": f"{HOST}/{ref}"}, f"push --dry-run {HOST}/{MEDIA}:{A1}: the plan names another"),
            ({"dry_image": other}, "the plan names another target or image"),
            ({"dry_status": 1}, "push --dry-run"),
            # A guard that does not say allowed fails even when unemu exits 0.
            ({"guard_line": "guard:refused"}, "push --dry-run"),
            ({"guard_line": "guard:allowed but"}, "push --dry-run"),
        ):
            self.repo.configure(**settings)
            code, stdout, _ = self.repo.run(ref)
            self.assertEqual(code, 1, settings)
            self.assertEqual(self.outcome(stdout, T1), "failed", settings)
            self.assertIn(fragment, stdout, settings)

    def test_the_recipes_are_read_and_checked_first(self):
        self.repo.media()
        # Two recipes of one tag.
        (self.repo.root / "recipes" / "media" / "demo" / "c.media.toml").write_text(
            (self.repo.root / M1).read_text(encoding="utf-8"), encoding="utf-8")
        code, _, stderr = self.repo.run(M2)
        self.assertEqual(code, 1)
        self.assertIn(f"recipes/media/demo/c.media.toml and {M1} both name {MEDIA}:{T1}", stderr)
        self.assertEqual(self.repo.calls(), [])
        # A recipe this pipeline cannot read, or that it does not publish.
        for text, fragment in (
            ("not toml [", "not a TOML recipe"),
            ('recipe = "1.0"\n[firmware]\nname = "unemu/firmware/ibm-pc/x"\nrevision = 1\n', "one identity table"),
            ('recipe = "1.0"\n[media]\nname = "other/media/ibm-pc/x"\nrevision = 1\n', "not under unemu/media/"),
            ('recipe = "1.0"\n[media]\nname = "unemu/media/ibm-pc/x"\nrelease = "1.0-r2"\nrevision = 1\n',
             "is not a release"),
            ('recipe = "1.0"\n[media]\nname = "unemu/media/ibm-pc/x"\nrevision = 0\n', "not a whole number"),
            ('recipe = "1.0"\n[media]\nname = "unemu/media/ibm-pc/x"\nrevision = 1\ndigest = "sha256:x"\n',
             "is not sha256:"),
        ):
            (self.repo.root / "recipes" / "media" / "demo" / "c.media.toml").write_text(text, encoding="utf-8")
            code, _, stderr = self.repo.run(M2)
            self.assertEqual(code, 1, text)
            self.assertIn(fragment, stderr, text)
            self.assertEqual(self.repo.calls(), [])
        (self.repo.root / "recipes" / "media" / "demo" / "c.media.toml").unlink()
        # unemu's own check over every recipe: a finding stops the run.
        self.repo.configure(check_findings=1, check_lines=[f"{M1}:3:1: error[6.11.3] x: synthetic"])
        code, stdout, stderr = self.repo.run(M2)
        self.assertEqual((code, stdout), (1, ""))
        self.assertIn("refused: unemu recipe check: 2 files checked, 1 findings", stderr)
        self.assertIn("error[6.11.3] x: synthetic", stderr)
        self.assertEqual([c["argv"][:2] for c in self.repo.calls()], [["--version"], ["recipe", "check"]])

    def test_every_recipe_needs_its_ok_line(self):
        self.repo.media()
        self.repo.configure(check_silent=[str(self.repo.root / M1)])
        code, stdout, stderr = self.repo.run(M2)
        self.assertEqual((code, stdout), (1, ""))
        self.assertIn(f"refused: unemu recipe check printed no ok line for {self.repo.root / M1}", stderr)
        self.assertEqual([c["argv"][:2] for c in self.repo.calls()], [["--version"], ["recipe", "check"]])

    def test_drift_is_reported_and_no_refusal_here(self):
        self.repo.media()
        drift = ('  drift: title.synopsis: the lock has "A", demo.work.toml has "B"; a build writes the '
                 "lock's value until a new revision is pinned")
        self.repo.configure(check_lines=[drift])
        code, _, stderr = self.repo.run()
        self.assertEqual(code, 0, stderr)
        self.assertIn("warning: drift: title.synopsis", stderr)


class Private(Case):
    def add_private(self, kind="media", folder="recipes/private/media/demo"):
        path = f"{folder}/private.{kind}.toml"
        target = self.repo.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        text = (f'recipe = "1.0"\n[{kind}]\nname = "private/{kind}/ibm-pc/demo"\n'
                f'platform = "ibm-pc"\nrevision = 1\ndigest = "sha256:{"d" * 64}"\n')
        if kind == "media":
            text += 'release = "1.0"\n'
        text += f'\n[input.game]\nname = "DEMO.IMG"\nsize = 737280\nsha256 = "{"a" * 64}"\n'
        target.write_text(text)
        return path, f"private/{kind}/ibm-pc/demo:" + ("1.0-r1" if kind == "media" else "r1")

    def test_default_skips_private_in_all_modes(self):
        self.repo.media()
        self.add_private()
        self.add_private("firmware", "recipes/private/firmware")
        report = self.repo.base / "report.json"
        for extra in ((), ("--fetch",), ("--skip-published",), ("--experimental", "42", "--fetch"), ("--push",)):
            with self.subTest(extra=extra):
                self.repo.log.unlink(missing_ok=True)
                self.repo.registry.calls.clear()
                code, _, stderr = self.repo.run(*extra, "--report", str(report))
                self.assertEqual(code, 0, stderr)
                self.assertEqual(self.repo.builds(), [M1, M2])
                self.assertEqual([r["recipe"] for r in json.loads(report.read_text())], [M1, M2])
                for call in self.repo.calls():
                    self.assertFalse(any(arg.startswith("private/") or "recipes/private/" in arg
                                         for arg in call["argv"]), call)
                self.assertTrue(all(not repository.startswith("private/")
                                    for _, repository, _ in self.repo.registry.calls))

    def test_explicit_private_file_ref_and_mixed_folder_refused(self):
        self.repo.media()
        private, ref = self.add_private(folder="recipes/media/demo")
        firmware, firmware_ref = self.add_private("firmware", "recipes/private/firmware")
        store = self.repo.base / "untrusted-store"
        store.mkdir()
        for selector in (private, ref, firmware, firmware_ref, "recipes/media/demo", "recipes"):
            for mode in ((), ("--fetch",), ("--experimental", "42"), ("--push",),
                         ("--no-build", "--store", str(store), "--push")):
                with self.subTest(selector=selector, mode=mode):
                    code, _, stderr = self.repo.run(*mode, selector)
                    self.assertEqual(code, 2, stderr)
                    self.assertIn("private recipes are static check only; never built, fetched or published", stderr)
                    self.assertEqual(self.repo.calls(), [])
                    self.assertEqual(self.repo.registry.calls, [])
                    self.assertEqual(list(store.iterdir()), [])

    def test_no_build_reads_only_the_open_store(self):
        self.repo.media()
        store = self.repo.base / "store"
        code, _, stderr = self.repo.run("--store", str(store))
        self.assertEqual(code, 0, stderr)
        self.add_private()
        self.repo.log.unlink()
        code, _, stderr = self.repo.run("--no-build", "--store", str(store), "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.repo.builds(), [])
        for call in self.repo.calls():
            self.assertFalse(any(arg.startswith("private/") or "recipes/private/" in arg
                                 for arg in call["argv"]), call)

    def test_only_private_is_an_empty_success_without_an_implicit_check(self):
        self.add_private()
        report = self.repo.base / "report.json"
        code, _, stderr = self.repo.run("--fetch", "--skip-published", "--report", str(report))
        self.assertEqual(code, 0, stderr)
        self.assertEqual([c["argv"] for c in self.repo.calls()], [["--version"]])
        self.assertEqual(json.loads(report.read_text()), [])
        self.assertEqual(self.repo.registry.calls, [])


class RecipeArtifacts(Case):
    def prepare(self, private=False):
        self.repo.media()
        self.repo.firmware()
        if private:
            Private.add_private(self)
            Private.add_private(self, "firmware", "recipes/private/firmware")
        for recipe in recipes.load(self.repo.root):
            folder = self.repo.root / recipe.folder
            if recipe.work:
                (folder / (recipe.work + ".work.toml")).write_text('recipe = "1.0"\n')
            for name in recipe.bundled:
                (folder / name).write_text("our bundled text\n")
            lock = self.repo.root / recipe.lock / "sha256"
            lock.mkdir(parents=True)
            (lock / ("a" * 64)).write_text("locked text\n")
            self.repo.registry.held[f"{HOST}/{recipe.ref}"] = recipe.digest

    def test_mapping_selection_report_and_no_output_operations(self):
        self.prepare(private=True)
        report = self.repo.base / "report.json"
        code, stdout, stderr = self.repo.run("--recipes", "--report", str(report))
        self.assertEqual(code, 0, stderr + stdout)
        rows = json.loads(report.read_text())
        self.assertEqual(len(rows), 5)
        targets = {
            FIRMWARE_REF: "unemu/recipes/firmware/ibm-pc/demo-bios:r1",
            f"{MEDIA}:{T1}": "unemu/recipes/media/ibm-pc/demo:2.0-r1",
            f"{MEDIA}:{T2}": "unemu/recipes/media/ibm-pc/demo:2.0-720k-r1",
            "private/media/ibm-pc/demo:1.0-r1": "unemu/recipes/media/ibm-pc/demo:1.0-r1",
            "private/firmware/ibm-pc/demo:r1": "unemu/recipes/firmware/ibm-pc/demo:r1",
        }
        pinned = {r.path: r.digest for r in recipes.load(self.repo.root)}
        for row in rows:
            self.assertEqual(row["target"], targets[row["ref"]])
            self.assertEqual(row["recipe_digest"], "sha256:" + sha256((self.repo.root / row["recipe"]).read_bytes()))
            self.assertEqual(row["output_digest"], pinned[row["recipe"]])
            self.assertIn(row["target"], stdout)
        self.assertEqual(self.repo.builds(), [])
        self.assertFalse(any(c["argv"][0] == "image" for c in self.repo.calls()))
        self.assertTrue(all(not repo.startswith("private/") for _, repo, _ in self.repo.registry.calls))
        # Work files participate in the static check, not only recipes.
        checked = [c["argv"] for c in self.repo.calls() if c["argv"][:2] == ["recipe", "check"]]
        self.assertTrue(any(str(self.repo.root / "recipes/media/demo/demo.work.toml") in a for a in checked))

    def test_recipe_ref_collisions_outside_selection_refuse_every_mode(self):
        self.repo.media()
        self.repo.firmware()
        private = self.repo.root / "recipes/private/collision.media.toml"
        private.parent.mkdir(parents=True)
        text = (self.repo.root / M1).read_text().replace("unemu/media/", "private/media/")
        text = text[:text.index("[license]")]
        private.write_text(text)
        for args in (("--recipes",), ("--recipes", "--guard-only"), ("--recipes", "--push")):
            with self.subTest(args=args):
                code, stdout, stderr = self.repo.run(*args, FIRMWARE_REF)
                self.assertEqual(code, 1, stdout + stderr)
                self.assertIn("both derive recipe REF unemu/recipes/media/ibm-pc/demo:2.0-r1", stderr)
                self.assertIn(M1, stderr)
                self.assertIn("recipes/private/collision.media.toml", stderr)
                self.assertEqual(self.repo.calls(), [])
                self.assertEqual(self.repo.registry.calls, [])

    def test_static_refusals_and_missing_ok_prevent_push(self):
        self.prepare()
        for settings in ({"check_findings": 1}, {"check_lines": ["drift: work changed"]},
                         {"check_silent": [str(self.repo.root / M1)]}):
            with self.subTest(settings=settings):
                self.repo.configure(**settings)
                code, stdout, stderr = self.repo.run("--recipes", "--push", M1)
                self.assertEqual(code, 1, stderr)
                self.assertIn("static check refused", stdout)
                self.assertFalse(self.repo.pushed.exists())
        self.repo.configure()
        path = self.repo.root / M1
        path.write_text(path.read_text().replace(f'digest = "{fake_digest(f"{MEDIA}:{T1}")}"', ""))
        code, stdout, _ = self.repo.run("--recipes", M1)
        self.assertEqual(code, 1)
        self.assertIn("static check refused", stdout)

    def test_open_output_gate_and_guard_only(self):
        self.prepare()
        for served in (None, "sha256:" + "f" * 64):
            self.repo.registry.held[f"{HOST}/{MEDIA}:{T1}"] = served
            code, stdout, stderr = self.repo.run("--recipes", "--push", M1)
            self.assertEqual(code, 1, stderr)
            self.assertIn("open output gate refused", stdout)
            self.assertIn("guard:allowed", stderr)
            self.assertFalse(self.repo.pushed.exists())
        self.repo.registry.error = "offline"
        code, stdout, _ = self.repo.run("--recipes", M1)
        self.assertEqual(code, 1)
        self.assertIn("offline", stdout)
        self.repo.registry.calls.clear()
        code, stdout, stderr = self.repo.run("--recipes", "--guard-only")
        self.assertEqual(code, 0, stderr)
        self.assertIn("output gate deferred", stdout)
        self.assertEqual(self.repo.registry.calls, [])

    def test_guard_and_plan_must_match(self):
        self.prepare()
        for settings in ({"recipe_guard": "refused"}, {"recipe_target": "wrong.invalid/ref:r1"},
                         {"recipe_output": f"{MEDIA}:{T2}\t{fake_digest(f'{MEDIA}:{T1}')}"},
                         {"recipe_output": f"{MEDIA}:{T1}\tsha256:{'f' * 64}"},
                         {"recipe_image": "bad"}, {"recipe_dry_status": 1}):
            with self.subTest(settings=settings):
                self.repo.configure(**settings)
                code, _, stderr = self.repo.run("--recipes", "--push", M1)
                self.assertEqual(code, 1, stderr)
                self.assertFalse(self.repo.pushed.exists())

    def test_accepted_pushes_are_read_back_without_credentials(self):
        self.prepare()
        for word in ("pushed", "unchanged", "moved"):
            with self.subTest(word=word):
                self.repo.configure(recipe_word=word)
                code, stdout, stderr = self.repo.run("--recipes", "--push", M1)
                self.assertEqual(code, 0, stderr + stdout)
                self.assertIn(word, stdout)
        calls = self.repo.calls()
        for call in calls:
            argv, env = call["argv"], call["env"]
            push = argv[:2] == ["recipe", "push"] and "--dry-run" not in argv
            self.assertEqual(env.get(PASSWORD_ENV), SECRET if push else None)
            self.assertEqual(env.get(USER_ENV), USER if push else None)
            self.assertEqual(env["LC_ALL"], "C")
            self.assertEqual(env["TZ"], "UTC")
        self.assertEqual(sum(c["argv"][:2] == ["recipe", "pull"] for c in calls), 3)
        self.assertNotIn(SECRET, stdout + stderr)

    def test_bad_push_outcomes_fail_and_stop(self):
        self.prepare()
        for settings in ({"recipe_word": "sent"}, {"recipe_word": "unknown"},
                         {"recipe_push_target": "unemu/recipes/media/ibm-pc/demo:2.0-r1"},
                         {"recipe_push_target": "other"},
                         {"recipe_digest": "sha256:" + "f" * 64}, {"recipe_push_status": 1},
                         {"recipe_word": "moved", "moved_output": "sha256:" + "f" * 64}):
            with self.subTest(settings=settings):
                self.repo.configure(**settings)
                self.repo.log.unlink(missing_ok=True)
                code, stdout, stderr = self.repo.run("--recipes", "--push")
                self.assertEqual(code, 1, stderr)
                self.assertIn("held", stdout)
                self.assertEqual(sum(c["argv"][:2] == ["recipe", "push"] and "--dry-run" not in c["argv"]
                                     for c in self.repo.calls()), 1)

    def test_push_cli_credentials_are_scrubbed_from_errors_and_reports(self):
        self.prepare()
        text = f"CLI diagnostic: password={SECRET}; user={USER}"
        report = self.repo.base / "report.json"
        for mode in ((), ("--recipes",)):
            for response in ({"stderr": text}, {"stdout": text}, {"stdout": text, "status": 0}):
                with self.subTest(mode=mode, response=response):
                    self.repo.configure(push_response=response)
                    code, stdout, stderr = self.repo.run(*mode, "--push", "--report", str(report), M1)
                    self.assertEqual(code, 1, stdout + stderr)
                    relayed = stdout + stderr + report.read_text()
                    self.assertIn("password=***; user=***", relayed)
                    self.assertNotIn(SECRET, relayed)
                    self.assertNotIn(USER, relayed)
                    self.assertFalse(self.repo.pushed.exists())

    def test_scrubbing_empty_and_overlapping_credentials(self):
        self.assertEqual(publish.scrub_cli_text("unchanged", {USER_ENV: "", PASSWORD_ENV: ""}), "unchanged")
        self.assertEqual(publish.scrub_cli_text("unchanged", {}), "unchanged")
        self.assertEqual(publish.scrub_cli_text("user-password user", {
            USER_ENV: "user", PASSWORD_ENV: "user-password",
        }), "*** ***")

    def test_readback_requires_exact_files_and_bytes(self):
        self.prepare()
        for settings in ({"pull_different": "a.media.toml"}, {"pull_different": "demo.work.toml"},
                         {"pull_different": "licenses.txt"}, {"pull_different": "a.media.toml.lock/sha256/" + "a" * 64},
                         {"pull_extra": True}, {"pull_missing": "licenses.txt"}, {"pull_link": True},
                         {"pull_digest": "sha256:" + "f" * 64}, {"recipe_pull_status": 1}):
            with self.subTest(settings=settings):
                self.repo.configure(**settings)
                code, stdout, stderr = self.repo.run("--recipes", "--push", M1)
                self.assertEqual(code, 1, stderr)
                self.assertIn("unconfirmed", stdout)

    def test_private_artifact_has_no_output_gate_and_can_push(self):
        path, _ = Private.add_private(self)
        lock = self.repo.root / (path + ".lock/sha256")
        lock.mkdir(parents=True)
        (lock / ("a" * 64)).write_text("text")
        self.repo.registry.error = "must never read a private output"
        code, stdout, stderr = self.repo.run("--recipes", "--push", path)
        self.assertEqual(code, 0, stderr + stdout)
        self.assertIn("private: static + guard", stdout)
        self.assertEqual(self.repo.registry.calls, [])
        self.assertEqual(self.repo.builds(), [])

    def test_incompatible_options_and_reused_store_are_usage_errors(self):
        self.prepare()
        for args in (("--guard-only",), ("--recipes", "--guard-only", "--push"),
                     ("--recipes", "--fetch"), ("--recipes", "--experimental", "42"),
                     ("--recipes", "--skip-published"), ("--recipes", "--no-build"),
                     ("--recipes", "--store", str(self.repo.root))):
            code, _, stderr = self.repo.run(*args)
            self.assertEqual(code, 2, stderr)
        self.assertEqual(self.repo.calls(), [])

    def test_explicit_ca_bundle_reaches_recipe_registry_calls_only(self):
        self.prepare()
        ca = self.repo.base / "ca.pem"
        ca.write_text("synthetic CA bundle")
        env = self.repo.environ(push=True, SSL_CERT_FILE=str(ca), UNRELATED="must not escape")
        code, stdout, stderr = self.repo.run("--recipes", "--push", M1, environ=env)
        self.assertEqual(code, 0, stderr + stdout)
        calls = self.repo.calls()
        for call in calls:
            self.assertNotIn("UNRELATED", call["env"])
            if call["argv"][:2] in (["recipe", "push"], ["recipe", "pull"]):
                self.assertEqual(call["env"]["SSL_CERT_FILE"], str(ca))


class Pin(Case):
    def test_pin_file(self):
        root = self.repo.root
        pin = publish.read_pin(root)
        self.assertEqual((pin.commit, pin.tag, pin.version), (PIN, "v9.9.9", "9.9.9"))
        # tests/unemu_test.py tests the format; here, that a refused pin
        # stops the run before unemu runs.
        for text in ("", "# only a comment\n", PIN_TOML.replace(PIN, PIN[:12]),
                     PIN_TOML.replace(PIN, PIN.upper()), f'commit = "{PIN}"\n' + PIN_TOML,
                     PIN_TOML.replace("tag = ", "branch = "), PIN + "\n"):
            (root / publish.PIN_FILE).write_text(text, encoding="ascii")
            with self.assertRaises(publish.Failure):
                publish.read_pin(root)
        self.repo.media()
        code, _, stderr = self.repo.run()
        self.assertEqual(code, 1)
        self.assertIn(f"refused: {publish.PIN_FILE}: ", stderr)
        self.assertEqual(self.repo.calls(), [])
        (root / publish.PIN_FILE).unlink()
        code, _, stderr = self.repo.run()
        self.assertEqual(code, 1)
        self.assertIn(f"refused: {publish.PIN_FILE}: cannot read", stderr)

    def test_the_commit_line_names_the_pinned_commit(self):
        self.repo.media()
        code, _, stderr = self.repo.run()
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"reports commit {PIN}, as pinned", stderr)
        self.repo.log.unlink()
        refused = (
            # Another commit.
            (VERSION.replace(f"commit: {PIN}", f"commit: {OTHER}"), f"reports commit {OTHER}; unemu.toml pins {PIN}"),
            # No commit line: a source build without UNEMUPC_SOURCE_COMMIT,
            # or unemu before 0.0.2; a hash anywhere else does not count.
            (VERSION.replace(f"commit: {PIN}\n", ""), "prints no commit line"),
            (f"unemu 0.1.0 ({PIN})", "prints no commit line"),
            (f"source: https://github.com/unemu-org/unemu/tree/{PIN}", "prints no commit line"),
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
                # Nothing is checked, built or pushed, and no registry is read.
                self.assertEqual([c["argv"] for c in self.repo.calls()], [["--version"]], version)
                self.assertEqual(self.repo.registry.calls, [])
                self.assertEqual(list((self.repo.root / "build").iterdir()), [])
                self.repo.log.unlink()

    def test_reported_commit(self):
        self.assertEqual(publish.unemu.reported_commit(VERSION), PIN)
        self.assertEqual(publish.unemu.reported_commit(VERSION + "\r\n"), PIN)
        self.assertIsNone(publish.unemu.reported_commit(""))
        self.assertIsNone(publish.unemu.reported_commit(f"unemu 0.0.1\nsource: x/tree/{PIN}\n"))


class Push(Case):
    def test_pushes_a_missing_tag_with_credentials_only_in_its_environment(self):
        self.repo.media()
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
        self.repo.media()
        for tag in (T1, A1):
            self.repo.registry.held[f"{HOST}/{MEDIA}:{tag}"] = fake_digest(f"{MEDIA}:{T1}")
        code, stdout, stderr = self.repo.run("--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T1), "present")
        self.assertEqual(self.outcome(stdout, T2), "pushed")
        self.assertEqual([p["argv"][3] for p in self.repo.pushes()], [f"{HOST}/{MEDIA}:{T2}", f"{HOST}/{MEDIA}:{A2}"])

    def test_a_missing_alias_is_pushed_after_its_revision(self):
        self.repo.media()
        self.repo.registry.held[f"{HOST}/{MEDIA}:{T1}"] = fake_digest(f"{MEDIA}:{T1}")
        code, stdout, stderr = self.repo.run("--push", f"{MEDIA}:{T1}")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T1), "pushed")
        self.assertEqual([p["argv"][2:4] for p in self.repo.pushes()], [[f"{MEDIA}:{T1}", f"{HOST}/{MEDIA}:{A1}"]])
        self.assertEqual(self.repo.registry.calls, [(HOST, MEDIA, T1), (HOST, MEDIA, A1), (HOST, MEDIA, A1)])

    def test_another_digest_is_refused_and_stops_the_push(self):
        self.repo.media()
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
        self.repo.media()
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
        self.repo.media()
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
        self.repo.media(pins=("match", "sha256:" + "e" * 64))
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "held")
        self.assertEqual(self.repo.pushes(), [])
        self.assertEqual(self.repo.registry.calls, [])

    def test_an_unreadable_registry_fails_closed(self):
        self.repo.media()
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
        self.repo.media()
        self.repo.configure(push_noop=True)
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        digest = fake_digest(f"{MEDIA}:{T1}")
        self.assertEqual(self.outcome(stdout, T1), "unconfirmed")
        self.assertIn(f"unconfirmed: unemu pushed {digest} to :{T1}; the registry serves None", stdout)
        self.assertEqual(self.outcome(stdout, T2), "held")
        # The alias waits for its revision.
        self.assertEqual(len(self.repo.pushes()), 1)

    def test_a_failed_read_back_still_says_pushed(self):
        self.repo.media()
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
                self.assertIn(f"unemu pushed {digest} to :{tag}; the read-back failed: {shown}", stdout)
                self.assertEqual(len(self.repo.pushes()), pushes)
                self.assertEqual(f"the revision tag {T1} is on the registry" in stdout, outcome == "partial")

    def test_any_error_marks_its_set_and_the_summary_prints(self):
        self.repo.media()
        # In the read before a push.
        self.repo.registry.errors = {1: RuntimeError("synthetic")}
        code, stdout, _ = self.repo.run("--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "failed")
        self.assertEqual(self.outcome(stdout, T2), "held")
        self.assertIn("unexpected RuntimeError: synthetic", stdout)
        self.assertEqual(self.repo.pushes(), [])

    def test_the_push_line_must_name_the_target_and_digest(self):
        self.repo.media()
        for settings, fragment in (
            ({"push_word": "moved"}, "unexpected last line"),
            ({"push_word": "uploaded"}, "unexpected last line"),
            ({"push_target": f"{HOST}/{MEDIA}:9.9-r1"}, "unexpected last line"),
            ({"push_digest": "sha256:" + "c" * 64}, f"unemu pushed sha256:{'c' * 64} to :{T1}"),
        ):
            self.repo.pushed.unlink(missing_ok=True)
            self.repo.configure(**settings)
            code, stdout, _ = self.repo.run("--push", f"{MEDIA}:{T1}")
            self.assertEqual(code, 1, settings)
            self.assertEqual(self.outcome(stdout, T1), "failed", settings)
            self.assertIn(fragment, stdout, settings)

    def test_an_unchanged_push_is_present(self):
        self.repo.media()
        self.repo.configure(push_word="unchanged")
        code, stdout, stderr = self.repo.run("--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T1), "present")
        self.assertEqual(self.outcome(stdout, T2), "present")

    def test_push_needs_credentials_and_a_host(self):
        self.repo.media()
        for missing in (USER_ENV, PASSWORD_ENV, HOST_ENV):
            environ = self.repo.environ(push=True, **{missing: None})
            code, stdout, stderr = self.repo.run("--push", environ=environ)
            self.assertEqual(code, 2, missing)
            self.assertIn(missing, stderr)
            self.assertNotIn(SECRET, stdout + stderr)
        environ = self.repo.environ(push=True, **{HOST_ENV: "https://registry.invalid/x"})
        self.assertEqual(self.repo.run("--push", environ=environ)[0], 2)
        self.assertEqual(self.repo.calls(), [])

    def test_dry_run_without_a_host_names_the_family_default(self):
        self.repo.media()
        code, stdout, stderr = self.repo.run(environ=self.repo.environ(**{HOST_ENV: None}))
        self.assertEqual(code, 0, stderr)
        self.assertIn(f"registry {publish.DEFAULT_HOST} (dry run)", stdout)
        self.assertEqual(self.repo.registry.calls, [])


class Experimental(Case):
    EXPERIMENTAL_MEDIA = "unemu/experimental/media/ibm-pc/demo"
    EXPERIMENTAL_FIRMWARE = "unemu/experimental/firmware/ibm-pc/demo-bios"

    def test_experimental_ref_and_tag(self):
        self.repo.media()
        code, stdout, stderr = self.repo.run("--experimental", "42")
        self.assertEqual(code, 0, stderr)
        targets = [c["argv"][3] for c in self.repo.calls() if "--dry-run" in c["argv"]]
        self.assertEqual(targets, [f"{HOST}/{self.EXPERIMENTAL_MEDIA}:{tag}"
                                   for tag in ("2.0-ci42-r1", "2.0-ci42", "2.0-720k-ci42-r1", "2.0-720k-ci42")])
        self.assertEqual(self.outcome(stdout, "2.0-ci42-r1", self.EXPERIMENTAL_MEDIA), "dry")
        self.assertEqual(self.row(stdout, self.EXPERIMENTAL_MEDIA, "2.0-720k-ci42-r1")[2], "2.0-720k-ci42")
        # The set is built under its own ref; only the target changes.
        shows = [c["argv"][3] for c in self.repo.calls() if c["argv"][2:3] == ["show"]]
        self.assertEqual(shows, [f"{MEDIA}:{T1}", f"{MEDIA}:{T2}"])

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
        self.repo.firmware()
        code, stdout, stderr = self.repo.run("--experimental", "42", "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, "ci42-r1", self.EXPERIMENTAL_FIRMWARE), "pushed")
        # A package has no alias, in the experimental namespace neither.
        self.assertEqual(self.row(stdout, self.EXPERIMENTAL_FIRMWARE, "ci42-r1")[2], "-")
        self.assertEqual(self.repo.registry.calls[0], (HOST, self.EXPERIMENTAL_FIRMWARE, "ci42-r1"))
        # The package is built under its own ref; only the target changes.
        self.assertEqual([p["argv"][2:4] for p in self.repo.pushes()], [
            [FIRMWARE_REF, f"{HOST}/{self.EXPERIMENTAL_FIRMWARE}:ci42-r1"],
        ])

    def test_experimental_tags_are_revision_tags_of_their_own(self):
        # ci<RUN> as the last part of the release: a revision tag that
        # never moves, and the release, its alias.
        for tag, run, expected in (("1.4-r1", "42", ("1.4-ci42-r1", "1.4-ci42")),
                                   ("1.4-720k-r2", "7", ("1.4-720k-ci7-r2", "1.4-720k-ci7")),
                                   ("r1", "42", ("ci42-r1", "ci42")), ("r3", "1", ("ci1-r3", "ci1"))):
            self.assertEqual(publish.experimental_tag(tag, run), expected)
            self.assertEqual(recipes.parse_revision(expected[0]), (expected[1], int(tag.rsplit("r", 1)[1])))
        for tag in ("1.4", "latest", "1", "r0", "1.4-r01", "-r1", "1.4--r1", "1.4-r1-r2"):
            with self.assertRaises(publish.Failure, msg=tag):
                publish.experimental_tag(tag, "42")

    def test_the_experimental_repository_fits_a_repository_name(self):
        # The recipes keep unemu/firmware/ibm-pc/<id> and
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
                    repo.firmware(catalogue_id=name, name="bios")
                    ref = f"unemu/firmware/ibm-pc/{name}:r1"
                else:
                    repo.media(work=name)
                    ref = f"unemu/media/ibm-pc/{name}:2.0-r1"
                code, stdout, stderr = repo.run("--experimental", "5")
                if accepted:
                    self.assertEqual(code, 0, stderr)
                    self.assertIn(repository, stdout)
                    continue
                self.assertEqual(code, 1)
                self.assertIn(f"refused: the experimental repository {repository} of {ref} is 256 bytes, "
                              "longer than 255", stderr)
                # Refused before any unemu call.
                self.assertEqual(repo.calls(), [])
                # Its own repository is within the limit.
                self.assertEqual(repo.run()[0], 0)

    def test_the_experimental_tag_fits_a_tag(self):
        # A 114-character version: :<v>-720k-r1 is 122 characters, so
        # -ci123 makes 128 and -ci1234 one too many.
        version = "1" * 114
        self.repo.media(version=version)
        code, _, stderr = self.repo.run("--experimental", "123")
        self.assertEqual(code, 0, stderr)
        self.repo.log.unlink()
        code, _, stderr = self.repo.run("--experimental", "1234")
        self.assertEqual(code, 1)
        self.assertIn(f"the target tag '{version}-720k-ci1234-r1' is longer than 128 characters", stderr)
        self.assertEqual(self.repo.calls(), [])


class SkipPublished(Case):
    """--skip-published: what the registry already serves under its own tag
    is neither built nor pushed."""

    def publish(self, ref, digest=None, alias=True):
        """Put REF on the fake registry, with its pinned digest unless
        DIGEST is given, and a set's release alias with it unless ALIAS is
        false."""
        repository, tag = ref.rsplit(":", 1)
        self.repo.registry.held[f"{HOST}/{ref}"] = digest or fake_digest(ref)
        if alias and "/media/" in ref:
            self.repo.registry.held[f"{HOST}/{repository}:{tag.rsplit('-', 1)[0]}"] = digest or fake_digest(ref)

    def test_published_is_neither_built_nor_pushed(self):
        self.repo.media()
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
            # unemu only checks its version and the recipes: no build, no
            # show, no dry run, no push.
            self.assertEqual([c["argv"][:2] for c in self.repo.calls()], [["--version"], ["recipe", "check"]], args)
            # One read of each tag and alias, and none for a push.
            self.assertEqual(self.repo.registry.calls, [(HOST, MEDIA, tag) for tag in (T1, A1, T2, A2)], args)
            self.assertIn(f"== skip {MEDIA}:{T1}: published, not built", stderr)
            self.assertEqual(list((self.repo.root / "build").iterdir()), [])

    def test_another_digest_is_refused(self):
        self.repo.media()
        self.publish(f"{MEDIA}:{T1}", "sha256:" + "d" * 64)
        code, stdout, _ = self.repo.run("--skip-published")
        self.assertEqual(code, 1)
        row = self.row(stdout, MEDIA, T1)
        self.assertEqual(row[3:5], ["sha256:" + "d" * 64, "refused"])
        # No next revision is advised: the maintainer decides.
        self.assertIn(f"{HOST} already serves sha256:{'d' * 64} under this tag, the recipe pins "
                      f"{fake_digest(f'{MEDIA}:{T1}')}; a revision tag never moves: stop and ask the maintainer",
                      stdout)
        self.assertNotIn("next revision", stdout)
        self.assertNotIn("2.0-r2", stdout)
        # The other set is still built and checked; the refused one is not.
        self.assertEqual(self.outcome(stdout, T2), "dry")
        self.assertEqual(self.repo.builds(), [M2])
        code, stdout, _ = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "refused")
        self.assertEqual(self.outcome(stdout, T2), "held")
        self.assertEqual(self.repo.pushes(), [])

    def test_a_missing_alias_is_built_and_pushed(self):
        # The revision is published, its alias is not: built again, and only
        # the alias is pushed.
        self.repo.media()
        self.publish(f"{MEDIA}:{T1}", alias=False)
        self.publish(f"{MEDIA}:{T2}")
        code, stdout, stderr = self.repo.run("--skip-published")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T1), "dry")
        self.assertEqual(self.outcome(stdout, T2), "published")
        self.assertIn(f"but not under its alias {A1} yet", stderr)
        self.assertEqual(self.repo.builds(), [M1])
        code, stdout, stderr = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T1), "pushed")
        self.assertEqual([p["argv"][2:4] for p in self.repo.pushes()], [[f"{MEDIA}:{T1}", f"{HOST}/{MEDIA}:{A1}"]])

    def test_an_alias_that_names_another_digest_is_refused(self):
        self.repo.media()
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
        self.repo.media()
        code, stdout, stderr = self.repo.run("--skip-published")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.repo.builds(), [M1, M2])
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

    def test_a_draft_is_not_compared(self):
        self.repo.media(pins=("match", None))
        self.publish(f"{MEDIA}:{T1}")
        self.publish(f"{MEDIA}:{T2}")
        code, stdout, _ = self.repo.run("--skip-published")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "published")
        self.assertEqual(self.outcome(stdout, T2), "unpinned")
        # Only the pinned set was read; the draft was built to measure it.
        self.assertEqual(self.repo.registry.calls, [(HOST, MEDIA, T1), (HOST, MEDIA, A1)])
        self.assertEqual(self.repo.builds(), [M2])
        code, stdout, _ = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T2), "unpinned")
        self.assertEqual(self.repo.pushes(), [])

    def test_mixed(self):
        other = "unemu/media/ibm-pc/other"
        self.repo.media()
        other_paths = self.repo.media(work="other")
        self.repo.firmware()
        self.publish(f"{MEDIA}:{T1}")
        self.publish(FIRMWARE_REF)
        code, stdout, stderr = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T1), "published")
        self.assertEqual(self.outcome(stdout, T2), "pushed")
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "published")
        for tag in (T1, T2):
            self.assertEqual(self.outcome(stdout, tag, other), "pushed")
        self.assertEqual(self.repo.builds(), [M2, *other_paths])
        self.assertEqual([p["argv"][3] for p in self.repo.pushes()], [
            f"{HOST}/{MEDIA}:{T2}", f"{HOST}/{MEDIA}:{A2}", f"{HOST}/{other}:{T1}", f"{HOST}/{other}:{A1}",
            f"{HOST}/{other}:{T2}", f"{HOST}/{other}:{A2}",
        ])

    def test_a_package(self):
        self.repo.firmware()
        self.publish(FIRMWARE_REF)
        code, stdout, stderr = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "published")
        # A package has no alias to read.
        self.assertEqual(self.repo.registry.calls, [(HOST, FIRMWARE_REPOSITORY, "r1")])
        self.assertEqual(self.repo.builds(), [])
        self.assertEqual(self.repo.pushes(), [])
        self.publish(FIRMWARE_REF, "sha256:" + "d" * 64)
        code, stdout, stderr = self.repo.run("--skip-published")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "refused")
        self.assertIn("a revision tag never moves: stop and ask the maintainer", stdout)
        self.assertNotIn("next revision", stdout)
        self.assertEqual(self.repo.builds(), [])

    def test_a_failed_read_fails_closed(self):
        self.repo.media()
        self.repo.firmware()
        self.repo.registry.error = "HTTP 503"
        code, stdout, _ = self.repo.run("--skip-published")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "failed")
        self.assertIn(f"reading {MEDIA}:{T1} on {HOST}: HTTP 503", stdout)
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "failed")
        self.assertEqual(self.repo.builds(), [])
        # Only the first read (the package's: recipes in path order) fails:
        # the package fails, the sets are built and checked, and nothing is
        # pushed.
        self.repo.registry.error = None
        self.repo.registry.errors = {1: RuntimeError("synthetic")}
        self.repo.registry.calls.clear()
        self.repo.log.unlink()
        code, stdout, _ = self.repo.run("--skip-published", "--push")
        self.assertEqual(code, 1)
        self.assertIn("unexpected RuntimeError: synthetic", stdout)
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "failed")
        self.assertEqual(self.outcome(stdout, T1), "held")
        self.assertEqual(self.outcome(stdout, T2), "held")
        self.assertEqual(self.repo.builds(), [M1, M2])
        self.assertEqual(self.repo.pushes(), [])

    def test_a_failed_read_of_the_alias_fails_closed(self):
        self.repo.media()
        self.publish(f"{MEDIA}:{T1}")
        # Read 1 is the revision's, which serves the pin; read 2 its alias's.
        self.repo.registry.errors = {2: publish.RegistryError("HTTP 503")}
        code, stdout, _ = self.repo.run("--skip-published", f"{MEDIA}:{T1}")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "failed")
        self.assertIn(f"reading {MEDIA}:{A1} on {HOST}: HTTP 503", stdout)
        self.assertEqual(self.repo.builds(), [])

    def test_experimental_notes_another_official_digest_and_goes_on(self):
        # A replacement is tested on unemu/experimental/ before the old
        # official tags are deleted: another digest under the official tag
        # or alias is a note there, never a refusal.
        experimental_media = "unemu/experimental/media/ibm-pc/demo"
        old = "sha256:" + "d" * 64
        self.repo.media()
        self.publish(f"{MEDIA}:{T1}", old)
        self.publish(f"{MEDIA}:{T2}")
        self.repo.registry.held[f"{HOST}/{MEDIA}:{A2}"] = old
        code, stdout, stderr = self.repo.run("--skip-published", "--experimental", "42")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, "2.0-ci42-r1", experimental_media), "dry")
        self.assertEqual(self.outcome(stdout, "2.0-720k-ci42-r1", experimental_media), "dry")
        self.assertIn(f"note: {HOST}/{MEDIA}:{T1} still serves {old}, the recipe pins {fake_digest(f'{MEDIA}:{T1}')}; "
                      "replacing it needs the maintainer (delete its tag and alias, then publish)", stdout)
        self.assertIn(f"note: {HOST}/{MEDIA}:{A2}, the alias, still names {old}, the recipe pins "
                      f"{fake_digest(f'{MEDIA}:{T2}')}; moving it needs the maintainer", stdout)
        self.assertEqual(self.repo.builds(), [M1, M2])
        code, stdout, stderr = self.repo.run("--skip-published", "--experimental", "42", "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, "2.0-ci42-r1", experimental_media), "pushed")
        self.assertEqual(self.outcome(stdout, "2.0-720k-ci42-r1", experimental_media), "pushed")
        self.assertIn("still serves", stdout)
        # Only experimental targets are pushed; the official ones are left
        # as they are.
        self.assertTrue(all(p["argv"][3].startswith(f"{HOST}/{experimental_media}:") for p in self.repo.pushes()))
        self.assertEqual(self.repo.registry.held[f"{HOST}/{MEDIA}:{T1}"], old)
        # Without --experimental, the same registry refuses both.
        code, stdout, _ = self.repo.run("--skip-published")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T1), "refused")
        self.assertEqual(self.outcome(stdout, T2), "refused")
        self.assertNotIn("note:", stdout)

    def test_experimental_reads_the_published_tag_and_pushes_the_rest(self):
        experimental_media = "unemu/experimental/media/ibm-pc/demo"
        experimental_firmware = "unemu/experimental/firmware/ibm-pc/demo-bios"
        self.repo.media()
        self.repo.firmware()
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
            [FIRMWARE_REF, f"{HOST}/{experimental_firmware}:ci42-r1"],
            [f"{MEDIA}:{T2}", f"{HOST}/{experimental_media}:2.0-720k-ci42-r1"],
            [f"{MEDIA}:{T2}", f"{HOST}/{experimental_media}:2.0-720k-ci42"],
        ])
        self.assertNotIn((HOST, experimental_media, "2.0-ci42-r1"), self.repo.registry.calls)


class NoBuild(Case):
    """--no-build: the sets and packages already in the store an earlier run
    built; no recipe build. This is how a CI push step that holds the
    credentials pushes what a build step without them built."""

    @staticmethod
    def steps(calls):
        """Each unemu call as (what, ref[, target])."""
        result = []
        for argv in (c["argv"] for c in calls):
            if argv == ["--version"]:
                result.append(("version",))
            elif argv[:2] == ["recipe", "check"]:
                result.append(("check",))
            elif argv[:2] == ["recipe", "build"]:
                result.append(("build", argv[2]))
            elif argv[2:3] == ["show"]:
                result.append(("show", argv[3]))
            else:
                result.append(("dry run" if "--dry-run" in argv else "push", argv[2], argv[3]))
        return result

    def test_the_store_is_used_as_it_is(self):
        self.repo.media()
        self.repo.firmware()
        store = self.repo.base / "store"
        code, _, stderr = self.repo.run("--store", str(store))
        self.assertEqual(code, 0, stderr)
        self.repo.log.unlink()
        code, stdout, stderr = self.repo.run("--no-build", "--store", str(store))
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.repo.builds(), [])
        self.assertIn(f"== take {MEDIA}:{T1} from the store (--no-build)", stderr)
        self.assertEqual(self.outcome(stdout, T1), "dry")
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "dry")
        # Only the build is left out: every set and package is shown and
        # dry-run pushed as without --no-build.
        checks = []
        for ref, targets in ((FIRMWARE_REF, [FIRMWARE_REF]),
                             (f"{MEDIA}:{T1}", [f"{MEDIA}:{T1}", f"{MEDIA}:{A1}"]),
                             (f"{MEDIA}:{T2}", [f"{MEDIA}:{T2}", f"{MEDIA}:{A2}"])):
            checks += [("show", ref)] + [("dry run", ref, f"{HOST}/{t}") for t in targets]
        self.assertEqual(self.steps(self.repo.calls()), [("version",), ("check",), *checks])
        # unemu reads a clean store of the run's own, never the given one.
        stores = {c["argv"][c["argv"].index("--store") + 1] for c in self.repo.calls() if "--store" in c["argv"]}
        self.assertEqual(len(stores), 1)
        self.assertTrue(stores.pop().startswith(str(self.repo.root / "build" / "publish.")))
        self.repo.log.unlink()
        code, stdout, stderr = self.repo.run("--no-build", "--store", str(store), "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, T2), "pushed")
        pushes = [("push", c[1], c[2]) for c in checks if c[0] == "dry run"]
        self.assertEqual(self.steps(self.repo.calls()), [("version",), ("check",), *checks, *pushes])
        # The store is kept.
        self.assertTrue((store / "images" / "index.json").exists())

    def test_what_the_store_lacks_or_holds_otherwise_fails(self):
        self.repo.media()
        store = self.repo.base / "store"
        code, _, stderr = self.repo.run("--store", str(store), M1)
        self.assertEqual(code, 0, stderr)
        for args in (("--no-build", "--store", str(store)), ("--no-build", "--store", str(store), "--push")):
            code, stdout, _ = self.repo.run(*args)
            self.assertEqual(code, 1, args)
            self.assertEqual(self.outcome(stdout, T1), "dry" if "--push" not in args else "held", args)
            self.assertEqual(self.outcome(stdout, T2), "failed", args)
            self.assertIn(f"the store names {MEDIA}:{T2} 0 times, not once", stdout)
        self.assertEqual(self.repo.pushes(), [])
        self.assertEqual(self.repo.builds(), [M1])

    def broken(self, change):
        """The store of M1, changed by CHANGE(store, manifest digest, blob
        paths), then --no-build --push of M1: its exit status, stdout and
        the unemu calls after --version and recipe check."""
        store = self.repo.base / "store"
        if store.exists():
            publish.remove_tree(store)
        code, _, stderr = self.repo.run("--store", str(store), M1)
        self.assertEqual(code, 0, stderr)
        manifest, parts = artifact(f"{MEDIA}:{T1}")
        blobs = store / "images" / "blobs" / "sha256"
        change(store, fake_digest(f"{MEDIA}:{T1}"), [blobs / sha256(p) for p in parts])
        self.repo.log.unlink()
        code, stdout, _ = self.repo.run("--no-build", "--store", str(store), "--push", M1)
        return code, stdout, [c["argv"][:2] for c in self.repo.calls()[2:]]

    def test_every_byte_of_the_store_is_checked_before_unemu_reads_it(self):
        self.repo.media()
        index_path = lambda store: store / "images" / "index.json"  # noqa: E731

        def another_digest(store, digest, blobs):
            index = json.loads(index_path(store).read_text())
            for entry in index["manifests"]:
                entry["digest"] = "sha256:" + "c" * 64
            index_path(store).write_text(json.dumps(index))

        def changed_layer(store, digest, blobs):
            blobs[1].write_bytes(b"X" * blobs[1].stat().st_size)

        def longer_layer(store, digest, blobs):
            blobs[1].write_bytes(blobs[1].read_bytes() + b"X")

        def missing_config(store, digest, blobs):
            blobs[0].unlink()

        def linked_layer(store, digest, blobs):
            target = store.parent / "elsewhere"
            target.write_bytes(blobs[1].read_bytes())
            blobs[1].unlink()
            blobs[1].symlink_to(target)

        def changed_manifest(store, digest, blobs):
            (store / "images" / "blobs" / "sha256" / digest.split(":")[1]).write_bytes(b"{}")

        def twice(store, digest, blobs):
            index = json.loads(index_path(store).read_text())
            index["manifests"].append(dict(index["manifests"][0]))
            index_path(store).write_text(json.dumps(index))

        def broken_index(store, digest, blobs):
            index_path(store).write_text("not json")

        def linked_index(store, digest, blobs):
            target = store.parent / "index-elsewhere.json"
            target.write_bytes(index_path(store).read_bytes())
            index_path(store).unlink()
            index_path(store).symlink_to(target)

        def large_index(store, digest, blobs):
            index_path(store).write_bytes(b" " * (publish.MAX_MANIFEST + 1))

        for change, outcome, fragment in (
            (another_digest, "mismatch", f"the store holds sha256:{'c' * 64} under {MEDIA}:{T1}, the recipe pins"),
            (changed_layer, "failed", "holds other bytes"),
            (longer_layer, "failed", "is not the 39 bytes its descriptor says"),
            (missing_config, "failed", "the store lacks the blob"),
            (linked_layer, "failed", "is not a regular file inside the store"),
            (changed_manifest, "failed", "bytes its descriptor says"),
            (twice, "failed", f"the store names {MEDIA}:{T1} 2 times, not once"),
            (broken_index, "failed", "the store's index cannot be read"),
            (linked_index, "failed", "the store's index cannot be read: not a regular file"),
            (large_index, "failed", "the store's index cannot be read: larger than"),
        ):
            with self.subTest(change.__name__):
                code, stdout, calls = self.broken(change)
                self.assertEqual(code, 1)
                self.assertEqual(self.outcome(stdout, T1), outcome)
                self.assertIn(fragment, stdout)
                # unemu never read the store, and nothing was pushed.
                self.assertEqual(calls, [])
                self.assertEqual(self.repo.pushes(), [])

    def test_a_draft_is_never_taken_from_the_store(self):
        self.repo.media(pins=("match", None))
        store = self.repo.base / "store"
        code, _, _ = self.repo.run("--store", str(store))
        self.assertEqual(code, 1)
        # The store holds the draft, built by the first run.
        self.assertIn(f"{MEDIA}:{T2}", store_tags(store))
        self.repo.log.unlink()
        code, stdout, stderr = self.repo.run("--no-build", "--store", str(store), "--push")
        self.assertEqual(code, 1)
        self.assertEqual(self.outcome(stdout, T2), "unpinned")
        self.assertIn("never taken from the store or pushed", stdout)
        self.assertEqual(self.outcome(stdout, T1), "held")
        self.assertNotIn(f"== take {MEDIA}:{T2}", stderr)
        # unemu saw only the pinned set, in a clean store that never held
        # the draft.
        refs = {c["argv"][2] if c["argv"][:2] == ["image", "push"] else c["argv"][3]
                for c in self.repo.calls() if c["argv"][:1] == ["image"]}
        self.assertEqual(refs, {f"{MEDIA}:{T1}"})
        self.assertEqual(self.repo.pushes(), [])

    def test_a_published_recipe_needs_nothing_in_the_store(self):
        self.repo.media()
        self.repo.firmware()
        store = self.repo.base / "store"
        code, _, stderr = self.repo.run("--store", str(store), "recipes/media")
        self.assertEqual(code, 0, stderr)
        self.repo.registry.held[f"{HOST}/{FIRMWARE_REF}"] = fake_digest(FIRMWARE_REF)
        code, stdout, stderr = self.repo.run("--skip-published", "--no-build", "--store", str(store), "--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.outcome(stdout, "r1", FIRMWARE_REPOSITORY), "published")
        self.assertEqual(self.outcome(stdout, T1), "pushed")


class Firmware(Case):
    """A firmware package goes the media sets' way."""

    def firmware_outcome(self, stdout, tag="r1"):
        return self.row(stdout, FIRMWARE_REPOSITORY, tag)[4]

    def test_calls_follow_the_recipe(self):
        self.repo.firmware()
        code, stdout, stderr = self.repo.run()
        self.assertEqual(code, 0, stderr)
        calls = [c["argv"] for c in self.repo.calls()]
        store = calls[2][calls[2].index("--store") + 1]
        self.assertEqual(calls[2], ["recipe", "build", str(self.repo.root / BIOS), "--store", store])
        self.assertEqual(calls[3], ["image", "firmware", "show", FIRMWARE_REF, "--store", store])
        # One dry run: a package has no alias.
        dry = [c for c in calls if "--dry-run" in c]
        self.assertEqual(len(dry), 1)
        self.assertEqual(dry[0][:7], ["image", "push", FIRMWARE_REF, f"{HOST}/{FIRMWARE_REF}",
                                      "--store", store, "--dry-run"])
        row = self.row(stdout, FIRMWARE_REPOSITORY, "r1")
        self.assertEqual(row[2:], ["-", fake_digest(FIRMWARE_REF), "dry", "run", "GPL-2.0-or-later"])
        self.assertIn(f"{BIOS}: {FIRMWARE_REF}", stderr)
        self.assertEqual(self.repo.registry.calls, [])
        self.assertEqual(self.repo.pushes(), [])

    def test_sets_and_packages_together_and_selection(self):
        self.repo.media()
        self.repo.firmware()
        code, stdout, stderr = self.repo.run()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.firmware_outcome(stdout), "dry")
        self.assertEqual(self.outcome(stdout, T2), "dry")
        # In recipe path order, one store.
        builds = [c["argv"] for c in self.repo.calls() if c["argv"][:2] == ["recipe", "build"]]
        self.assertEqual(self.repo.builds(), [BIOS, M1, M2])
        self.assertEqual(len({a[a.index("--store") + 1] for a in builds}), 1)
        for selector, expected in ((BIOS, [BIOS]), (FIRMWARE_REF, [BIOS]), ("recipes/firmware", [BIOS]),
                                   (f"{MEDIA}:{T1}", [M1]), ("recipes/media/demo", [M1, M2])):
            self.repo.log.unlink(missing_ok=True)
            code, _, stderr = self.repo.run(selector)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(self.repo.builds(), expected, selector)
        for selector in (f"{FIRMWARE_REPOSITORY}:r2", FIRMWARE_REPOSITORY, f"{FIRMWARE_REPOSITORY}:1",
                         "unemu/firmware/demo-bios:1", "unemu/firmware/demo-bios:r1",
                         "private/firmware/ibm-pc/demo-bios:r1"):
            self.repo.log.unlink(missing_ok=True)
            code, _, stderr = self.repo.run(selector)
            self.assertEqual(code, 2, selector)
            self.assertIn("neither a recipe, a folder of recipes nor a ref", stderr)
            self.assertEqual(self.repo.calls(), [])

    def test_the_show_must_name_it_and_allow_unemu(self):
        self.repo.firmware()
        digest = fake_digest(FIRMWARE_REF)
        for settings, fragment in (
            ({"show_first": f'{FIRMWARE_REPOSITORY}:r2\t{digest}\t"Demo BIOS"'}, "firmware show: the first line"),
            ({"show_first": f'{FIRMWARE_REF}\tsha256:{"c" * 64}\t"Demo BIOS"'}, "firmware show: the first line"),
            ({"push_line": "push\tprivate registries outside unemu/ only: copyleft ROM without its source "
                           "archive"}, "firmware show: the last line is"),
        ):
            self.repo.configure(**settings)
            code, stdout, _ = self.repo.run()
            self.assertEqual(code, 1, settings)
            self.assertEqual(self.firmware_outcome(stdout), "failed", settings)
            self.assertIn(fragment, stdout, settings)
        self.repo.configure()
        self.assertEqual(self.repo.run()[0], 0)

    def test_digest_pins(self):
        self.repo.firmware(pin=None, name="bios")
        self.repo.firmware(catalogue_id="other-bios", pin="sha256:" + "e" * 64)
        code, stdout, _ = self.repo.run()
        self.assertEqual(code, 1)
        self.assertEqual(self.firmware_outcome(stdout), "unpinned")
        self.assertEqual(self.row(stdout, "unemu/firmware/ibm-pc/other-bios", "r1")[4], "mismatch")
        self.assertIn(f"unemu gives {fake_digest('unemu/firmware/ibm-pc/other-bios:r1')}, the recipe pins "
                      f"sha256:{'e' * 64}", stdout)
        code, stdout, _ = self.repo.run("--push", "recipes/firmware/bios.firmware.toml")
        self.assertEqual(code, 1)
        self.assertEqual(self.firmware_outcome(stdout), "unpinned")
        self.assertEqual(self.repo.pushes(), [])

    def test_push(self):
        self.repo.firmware()
        code, stdout, stderr = self.repo.run("--push")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(self.firmware_outcome(stdout), "pushed")
        pushes = self.repo.pushes()
        self.assertEqual([p["argv"] for p in pushes], [[
            "image", "push", FIRMWARE_REF, f"{HOST}/{FIRMWARE_REF}",
            "--store", pushes[0]["argv"][5], "--config", pushes[0]["argv"][7],
        ]])
        self.assertEqual((pushes[0]["env"][USER_ENV], pushes[0]["env"][PASSWORD_ENV]), (USER, SECRET))
        for call in self.repo.calls():
            self.assertNotIn("--move", call["argv"])
            if call not in pushes:
                self.assertFalse({USER_ENV, PASSWORD_ENV, HOST_ENV} & set(call["env"]), call["argv"])
        self.assertEqual(self.repo.registry.calls, [(HOST, FIRMWARE_REPOSITORY, "r1")] * 2)
        # Never a latest tag.
        self.assertFalse(any("latest" in str(c["argv"]) for c in self.repo.calls()))

    def test_a_revision_never_moves(self):
        self.repo.firmware()
        target = f"{HOST}/{FIRMWARE_REF}"
        self.repo.registry.held[target] = fake_digest(FIRMWARE_REF)
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

    def test_two_recipes_of_one_package_are_refused(self):
        self.repo.firmware()
        self.repo.firmware(name="again")
        code, _, stderr = self.repo.run(BIOS)
        self.assertEqual(code, 1)
        self.assertIn(f"both name {FIRMWARE_REF}", stderr)
        self.assertEqual(self.repo.calls(), [])

    def test_a_failure_of_either_kind_holds_every_push(self):
        # Each scenario in a fresh repository: a passing, pinned set or
        # package next to one of the other kind that is a draft or fails.
        scenarios = (
            ("a pinned set, a draft package", {}, {"pin": None}, {}, "media", "unpinned"),
            ("a pinned set, a failing package", {}, {}, {"fail_build": [FIRMWARE_REF]}, "media", "failed"),
            ("a pinned package, two draft sets", {"pins": (None, None)}, {}, {}, "firmware", "unpinned"),
            ("a pinned package, a set with another digest", {"pins": ("sha256:" + "e" * 64, "match")}, {}, {},
             "firmware", "mismatch"),
            ("a pinned package, a failing set", {}, {}, {"fail_build": [f"{MEDIA}:{T1}"]}, "firmware", "failed"),
        )
        for name, sets, package, settings, passing, outcome in scenarios:
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                repo = Repository(directory)
                repo.media(**sets)
                repo.firmware(**package)
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
