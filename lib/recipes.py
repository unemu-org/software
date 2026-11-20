# SPDX-License-Identifier: AGPL-3.0-or-later
"""The recipe files of this repository, as lib/publish.py and lib/changed.py
read them, and the check CI runs over them.

    python3 -I lib/recipes.py check --cli UPCLI [FILE|FOLDER]...

A recipe is a recipe file of upcli, the pinned unemupc release's CLI
(recipe version 0.1; docs/recipes.md): recipes/**/NAME.media.toml builds one
media set, recipes/**/NAME.firmware.toml one firmware package. Hidden
folders and lock folders (FILE.lock/) hold none. Beside a recipe may lie
its work file (WORK.work.toml, named by `work = "WORK"`), its bundled inputs
(an [input.ID] with `bundle = true`, found beside it by its `name`) and its
lock (FILE.lock/sha256/<hex>), which `upcli recipe pin` writes together
with `digest`.

This module reads only what the pipeline needs: the identity table, [media]
or [firmware], with `name` (the repository), `release` (optional),
`revision` and `digest` (absent: a draft); and `work`, the names of the
bundled inputs and the licence expression. upcli's `recipe check` is the
full check. The ref is NAME:TAG; the tag is <release>-r<revision>, or
r<revision> without a release (unemupc's decision-38), and the release
alone is the alias that names the newest revision. This pipeline publishes
repositories under unemu/media/ and unemu/firmware/ only.

`check` runs `upcli recipe check` over the files and folders given
(default: recipes/) and passes only when upcli prints an ok line for every
recipe and every work file a recipe names, and reports no finding, no drift
(a work file that now differs from a pinned recipe's lock: a build writes
the lock's value, so the edit would never reach a set until a new pin) and
no draft (a recipe without `digest`, which is never published). upcli runs
after its commit check (lib/upcli.py), with an environment of its own.
Exit status: 0 passed, 1 refused, 2 usage error.

Standard library only. Run it with -I.
"""

import argparse
import dataclasses
import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import tomllib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

RECIPES = "recipes"
SUFFIXES = {".media.toml": "media", ".firmware.toml": "firmware"}
WORK_SUFFIX = ".work.toml"
LOCK_SUFFIX = ".lock"
PREFIXES = {"media": "unemu/media/", "firmware": "unemu/firmware/"}
# OCI distribution: a repository name of lower-case components, a tag.
COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
REPOSITORY = re.compile(rf"{COMPONENT}(?:/{COMPONENT})*")
MAX_REPOSITORY = 255
TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,127}")
# decision-38: a release is parts joined by "-", none of them r<digits>.
PART = re.compile(r"[a-z0-9]+(?:[._][a-z0-9]+)*")
REVISION_PART = re.compile(r"r([1-9][0-9]*)")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
# What upcli recipe check prints for a work file that differs from a lock,
# and for a file that passed: `FILE: ok, KIND...`.
DRIFT = "drift:"
OK_LINE = re.compile(r"(.+): ok, ")


class Refusal(Exception):
    """A recipe file this pipeline cannot take, and why."""


@dataclasses.dataclass(frozen=True)
class Recipe:
    """One recipe file: PATH relative to the repository root, with "/"."""

    path: str
    kind: str
    repository: str
    release: str | None
    revision: int
    digest: str | None
    work: str | None
    bundled: tuple
    license: str | None

    @property
    def tag(self):
        return f"{self.release}-r{self.revision}" if self.release else f"r{self.revision}"

    @property
    def alias(self):
        """The release alone, which names its newest revision; None without
        a release."""
        return self.release

    @property
    def ref(self):
        return f"{self.repository}:{self.tag}"

    @property
    def folder(self):
        return self.path.rpartition("/")[0]

    @property
    def lock(self):
        """The lock folder, FILE.lock/, relative to the root."""
        return self.path + LOCK_SUFFIX + "/"


def kind_of(path):
    """The recipe kind a repository-relative PATH names, or None when it is
    no recipe: not under recipes/, not *.media.toml or *.firmware.toml, or
    inside a hidden or a lock folder."""
    parts = path.split("/")
    if len(parts) < 2 or parts[0] != RECIPES:
        return None
    if any(not part or part.startswith(".") for part in parts):
        return None
    if any(part.endswith(LOCK_SUFFIX) for part in parts[:-1]):
        return None
    for suffix, kind in SUFFIXES.items():
        if parts[-1].endswith(suffix) and len(parts[-1]) > len(suffix):
            return kind
    return None


def parse_release(release):
    """Whether RELEASE is a release of decision-38."""
    return all(PART.fullmatch(part) and not REVISION_PART.fullmatch(part) for part in release.split("-"))


def parse_revision(tag):
    """(release or None, number) of a revision tag <release>-rN or rN, or
    None when TAG is none."""
    head, dash, last = tag.rpartition("-")
    found = REVISION_PART.fullmatch(last)
    if not found:
        return None
    if not dash:
        return None, int(found.group(1))
    if not parse_release(head):
        return None
    return head, int(found.group(1))


def parse(path, data):
    """The Recipe of PATH, whose bytes are DATA; a Refusal when it is not a
    recipe this pipeline publishes."""
    kind = kind_of(path)
    if kind is None:
        raise Refusal(f"{path}: not a recipe file (recipes/**/NAME.media.toml or NAME.firmware.toml)")
    try:
        document = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise Refusal(f"{path}: not a TOML recipe: {error}") from None
    tables = [name for name in ("media", "firmware", "machine") if name in document]
    if tables != [kind]:
        raise Refusal(f"{path}: a .{kind}.toml file must have one identity table, [{kind}], not {tables}")
    identity = document[kind]
    if not isinstance(identity, dict):
        raise Refusal(f"{path}: [{kind}] is not a table")
    repository = identity.get("name")
    if not isinstance(repository, str) or not REPOSITORY.fullmatch(repository):
        raise Refusal(f"{path}: {kind}.name {repository!r} is not a repository name")
    if not repository.startswith(PREFIXES[kind]) or len(repository) > MAX_REPOSITORY:
        raise Refusal(f"{path}: {kind}.name {repository!r} is not under {PREFIXES[kind]}, "
                      "where this repository publishes")
    release = identity.get("release")
    if release is not None and (not isinstance(release, str) or not parse_release(release)):
        raise Refusal(f"{path}: {kind}.release {release!r} is not a release (decision-38)")
    revision = identity.get("revision")
    if type(revision) is not int or revision < 1:
        raise Refusal(f"{path}: {kind}.revision {revision!r} is not a whole number from 1")
    digest = identity.get("digest")
    if digest is not None and (not isinstance(digest, str) or not DIGEST.fullmatch(digest)):
        raise Refusal(f"{path}: {kind}.digest {digest!r} is not sha256:<64 hex digits>")
    work = document.get("work")
    if work is not None and not isinstance(work, str):
        raise Refusal(f"{path}: work is not a string")
    inputs = document.get("input", {})
    if not isinstance(inputs, dict):
        raise Refusal(f"{path}: input is not a table of inputs")
    bundled = tuple(sorted(
        value["name"] for value in inputs.values()
        if isinstance(value, dict) and value.get("bundle") is True and isinstance(value.get("name"), str)
    ))
    statement = document.get("license")
    license = statement.get("expression") if isinstance(statement, dict) else None
    recipe = Recipe(path, kind, repository, release, revision, digest, work, bundled,
                    license if isinstance(license, str) else None)
    if not TAG.fullmatch(recipe.tag):
        raise Refusal(f"{path}: the tag {recipe.tag!r} is longer than 128 characters")
    return recipe


def find(root):
    """Every recipe file under ROOT/recipes, relative to ROOT, sorted."""
    base = pathlib.Path(root) / RECIPES
    found = []
    for directory, folders, files in os.walk(base):
        folders[:] = sorted(f for f in folders if not f.startswith(".") and not f.endswith(LOCK_SUFFIX))
        for name in files:
            path = (pathlib.Path(directory) / name).relative_to(root).as_posix()
            if kind_of(path):
                found.append(path)
    return sorted(found)


def load(root):
    """Every Recipe of the repository at ROOT; a Refusal for the first file
    that is no recipe this pipeline takes, or for two that name one tag."""
    recipes = []
    for path in find(root):
        recipes.append(parse(path, (pathlib.Path(root) / path).read_bytes()))
    unique(recipes)
    return recipes


def unique(recipes):
    """No two recipes name one ref, and no alias is a ref or another
    recipe's alias of the same repository: a revision tag names one artifact
    and an alias follows one release."""
    seen = {}
    for recipe in recipes:
        names = [recipe.ref] + ([f"{recipe.repository}:{recipe.alias}"] if recipe.alias else [])
        for name in names:
            if name in seen:
                raise Refusal(f"{recipe.path} and {seen[name]} both name {name}")
            seen[name] = recipe.path


# --- the check ----------------------------------------------------------------


def _module(name):
    path = HERE / f"{name}.py"
    loaded = sys.modules.get(name)
    if loaded is not None and getattr(loaded, "__file__", None) == str(path):
        return loaded
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def selected(recipes, root, operands):
    """The recipes the OPERANDS (files or folders, relative to the current
    directory or absolute) name; every recipe without operands. A work file
    names the recipes beside it that name it."""
    if not operands:
        return list(recipes)
    chosen = []
    for operand in operands:
        try:
            rel = pathlib.Path(operand).resolve().relative_to(pathlib.Path(root).resolve()).as_posix()
        except ValueError:
            raise Refusal(f"{operand}: not inside the repository {root}") from None
        rel = "" if rel == "." else rel
        folder, _, name = rel.rpartition("/")
        matching = [r for r in recipes if r.path == rel or not rel or r.path.startswith(rel + "/")
                    or (name.endswith(WORK_SUFFIX) and r.folder == folder
                        and r.work == name[: -len(WORK_SUFFIX)])]
        if not matching:
            raise Refusal(f"{operand}: no recipe there")
        chosen.extend(r for r in matching if r not in chosen)
    return chosen


def files_of(root, recipes):
    """The files upcli's check must pass for RECIPES: each recipe file and
    the work file it names, as absolute paths."""
    files = []
    for recipe in recipes:
        files.append(pathlib.Path(root) / recipe.path)
        if recipe.work:
            files.append(pathlib.Path(root) / recipe.folder / f"{recipe.work}{WORK_SUFFIX}")
    return list(dict.fromkeys(files))


def not_passed(stdout, files):
    """The FILES that no `FILE: ok, ...` line of upcli recipe check's STDOUT
    names (paths compared once resolved)."""
    passed = set()
    for line in stdout.splitlines():
        found = OK_LINE.match(line)
        if found:
            passed.add(os.path.realpath(found.group(1)))
    return [f for f in files if os.path.realpath(f) not in passed]


def check(cli, root, operands, environ):
    """upcli recipe check over OPERANDS (default: recipes/): 0 when it
    passes, 1 with every reason printed."""
    upcli = _module("upcli")
    try:
        recipes = selected(load(root), root, operands)
        pin = upcli.read_pin(pathlib.Path(root) / upcli.PIN_FILE)
    except (Refusal, upcli.Refusal) as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="recipes-check.") as work:
        env = {"PATH": environ.get("PATH", os.defpath), "HOME": work, "TMPDIR": work, "LC_ALL": "C", "TZ": "UTC"}
        try:
            upcli.check_cli(cli, pin.commit, env)
        except upcli.Refusal as refusal:
            print(f"refused: {refusal}", file=sys.stderr)
            return 1
        paths = [str(pathlib.Path(root) / RECIPES)] if not operands else [os.path.abspath(o) for o in operands]
        # upcli reports only the files it is given or finds below a folder
        # it is given: a work file of a recipe given as a file is added.
        for work_file in files_of(root, recipes):
            if not any(os.path.realpath(work_file) == os.path.realpath(given)
                       or os.path.realpath(work_file).startswith(os.path.realpath(given) + os.sep)
                       for given in paths):
                paths.append(str(work_file))
        try:
            done = subprocess.run([cli, "recipe", "check", *paths], capture_output=True, encoding="utf-8",
                                  errors="replace", env=env, cwd=root, check=False)
        except OSError as error:
            print(f"refused: cannot run {cli}: {error.strerror or error}", file=sys.stderr)
            return 1
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    reasons = []
    if done.returncode != 0:
        reasons.append(f"upcli recipe check exited with status {done.returncode}")
    drift = [line.strip() for line in done.stdout.splitlines() if line.strip().startswith(DRIFT)]
    if drift:
        reasons.append(f"{len(drift)} drift lines: a work file differs from a lock; pin a new revision, "
                       "or put the work file back")
    missing = not_passed(done.stdout, files_of(root, recipes))
    if missing:
        reasons.append("upcli printed no ok line for " + ", ".join(
            pathlib.Path(f).relative_to(root).as_posix() for f in missing))
    drafts = [r.path for r in recipes if r.digest is None]
    if drafts:
        reasons.append("drafts, which are never published: " + ", ".join(drafts) + "; pin them "
                       "(upcli recipe pin)")
    for reason in reasons:
        print(f"refused: {reason}", file=sys.stderr)
    if not reasons:
        print(f"recipes: {len(recipes)} pinned recipes, no finding, no drift", file=sys.stderr)
    return 1 if reasons else 0


def main(argv=None, root=ROOT, environ=None):
    environ = dict(os.environ if environ is None else environ)
    parser = argparse.ArgumentParser(prog="lib/recipes.py", description="Check the recipe files.")
    commands = parser.add_subparsers(dest="command", required=True)
    checking = commands.add_parser("check", help="upcli recipe check, refusing findings, drift and drafts")
    checking.add_argument("--cli", default=environ.get("UPCLI"), help="the upcli binary (default: $UPCLI)")
    checking.add_argument("paths", nargs="*", metavar="FILE|FOLDER")
    try:
        options = parser.parse_args(sys.argv[1:] if argv is None else argv)
        if not options.cli:
            parser.error("--cli UPCLI (or $UPCLI) is required")
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    return check(os.path.abspath(options.cli), root, options.paths, environ)


if __name__ == "__main__":
    sys.exit(main())
