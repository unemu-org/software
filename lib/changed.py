# SPDX-License-Identifier: AGPL-3.0-or-later
"""The recipes a change touches, for CI: which ones to build and check.

    python3 -I lib/changed.py [--json] [--sources] BASE HEAD
    python3 -I lib/changed.py [--json] [--sources] --all [HEAD]

BASE and HEAD are commits of this repository (HEAD by default for --all).
Prints every recipe file at HEAD that the change from BASE to HEAD touches,
one path per line in path order (with --json, as one JSON list):

  - a recipe is a file recipes/**/NAME.media.toml or NAME.firmware.toml at
    HEAD, outside hidden and lock folders (lib/recipes.py);
  - a recipe is touched by a change to the recipe itself, to anything in its
    lock folder (NAME.<kind>.toml.lock/), to one of its bundled inputs (an
    [input] with bundle = true, beside it by its name) or to the work file
    it names (WORK.work.toml beside it); a deleted recipe is not selected,
    since it is no longer at HEAD;
  - a change to a shared input selects every recipe: lib/ (the pipeline and
    its helpers), upcli.toml (the CLI that builds and checks every recipe)
    and .github/workflows/ (the CI steps that build, compare and dry-run
    them);
  - anything else selects nothing: docs/ and the other documentation,
    tests/ (the README's check command runs them on every change), the
    toolchain of the devShell (flake.nix, flake.lock: no recipe runs in it)
    and the tooling configuration.

With --sources it prints instead the scripts that make prepared inputs,
recipes/**/source/build.sh at HEAD, that the change touches: a change to
anything in the script's own folder, or to a file of the work folder above
it that is no recipe file, lock or work file (the licence index the
archive carries, say), or to a shared input of the scripts: lib/source.sh,
flake.nix, flake.lock (the toolchain they run in) and .github/workflows/.

Everything is selected, as with --all, when BASE is all zeros (what GitHub
sends for a new branch) or is not a commit here (a force push whose old head
is gone). A rename is a deletion plus an addition. Nothing is printed when
nothing is selected.

The answer comes from git alone: `git diff --name-only -z --no-renames`
between the two commits' trees, `git ls-tree` of HEAD and the recipe files'
blobs at HEAD, never the working tree. git runs without the user's or the
system's configuration. Exit status: 0 with the selection printed, 1 when
HEAD is not a commit or git fails, 2 for a usage error.

Standard library only. Run it with -I.
"""

import argparse
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


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


recipes = _module("recipes")

# A change under any of these selects every recipe: a directory ends in /.
SHARED = ("lib/", "upcli.toml", ".github/workflows/")
# ... and every prepared-input script.
SHARED_SOURCES = ("lib/source.sh", "flake.nix", "flake.lock", ".github/workflows/")
SOURCE_SCRIPT = "source/build.sh"
# The null commit GitHub names for a branch that did not exist before
# (SHA-1 or SHA-256 object names).
ZERO = re.compile(r"0{40}|0{64}")


class Failure(Exception):
    """git failed, or HEAD is not a commit."""


def git_environment(environ):
    """ENVIRON without anything that points git at other configuration or
    another repository; no system or global configuration, no prompt."""
    env = {name: value for name, value in environ.items() if not name.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    })
    return env


def git(root, args, env, check=True):
    """Run git in ROOT; its stdout as bytes, or None when CHECK is false and
    git fails."""
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=env, check=False)
    except OSError as error:
        raise Failure(f"cannot run git: {error.strerror or error}") from None
    if done.returncode != 0:
        if not check:
            return None
        reason = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise Failure(f"git {' '.join(args)}: {reason[-1] if reason else f'exit status {done.returncode}'}")
    return done.stdout


def commit(root, name, env):
    """NAME as a full commit id, or None when it names no commit here."""
    out = git(root, ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{name}^{{commit}}"], env,
              check=False)
    if out is None:
        return None
    return out.decode("ascii").strip()


def split_z(data):
    """NUL-separated paths as text; git keeps them unquoted with -z."""
    return [part.decode("utf-8", "surrogateescape") for part in data.split(b"\0") if part]


def files_at(root, head, env):
    """Every file under recipes/ at HEAD."""
    return split_z(git(root, ["ls-tree", "-r", "-z", "--name-only", "--full-tree", head, "--", "recipes/"], env))


def recipes_at(root, head, env):
    """Every recipe at HEAD, as (path, work file or None, bundled names); a
    recipe its file cannot describe still counts, by its path alone."""
    found = []
    for path in files_at(root, head, env):
        if not recipes.kind_of(path):
            continue
        work, bundled = None, ()
        try:
            recipe = recipes.parse(path, git(root, ["cat-file", "blob", f"{head}:{path}"], env))
            work, bundled = recipe.work, recipe.bundled
        except recipes.Refusal:
            pass
        found.append((path, work, bundled))
    return sorted(found)


def sources_at(root, head, env):
    """Every prepared-input script at HEAD: recipes/**/source/build.sh."""
    return sorted(path for path in files_at(root, head, env)
                  if path.endswith("/" + SOURCE_SCRIPT) and path.count("/") >= 3)


def changed_paths(root, base, head, env):
    """Every path that differs between the trees of BASE and HEAD."""
    return split_z(git(root, ["diff", "--name-only", "-z", "--no-renames", "--no-relative", "--no-ext-diff",
                              base, head, "--"], env))


def shared(path, items):
    return any(path == item or (item.endswith("/") and path.startswith(item)) for item in items)


def touches(path, recipe):
    """Whether a change to PATH touches the recipe (path, work, bundled)."""
    own, work, bundled = recipe
    folder = own.rpartition("/")[0]
    if path == own or path.startswith(own + recipes.LOCK_SUFFIX + "/"):
        return True
    directory, _, name = path.rpartition("/")
    if directory != folder:
        return False
    return name in bundled or (work is not None and name == work + recipes.WORK_SUFFIX)


def touches_source(path, script):
    """Whether a change to PATH touches the prepared-input script SCRIPT:
    anything in its folder, or a plain file of the work folder above it."""
    folder = script[: -len("/build.sh")]
    if path.startswith(folder + "/"):
        return True
    work_folder = folder.rpartition("/")[0]
    directory, _, name = path.rpartition("/")
    if directory != work_folder:
        return False
    return not (recipes.kind_of(path) or name.endswith(recipes.WORK_SUFFIX))


def select(root, base, head, environ=None, sources=False):
    """The recipes (or with SOURCES the prepared-input scripts) at HEAD that
    the change from BASE (None: every one) to HEAD touches, and a note on
    stderr's behalf (or None)."""
    env = git_environment(os.environ if environ is None else environ)
    head_id = commit(root, head, env)
    if head_id is None:
        raise Failure(f"HEAD {head!r} is not a commit in {root}")
    if sources:
        every = sources_at(root, head_id, env)
        names = every
    else:
        every = recipes_at(root, head_id, env)
        names = [path for path, _, _ in every]
    if base is None:
        return names, None
    if ZERO.fullmatch(base):
        return names, f"BASE {base} is the null commit (a new branch): every one"
    base_id = commit(root, base, env)
    if base_id is None:
        return names, f"BASE {base!r} is not a commit here (a force push?): every one"
    paths = changed_paths(root, base_id, head_id, env)
    if any(shared(path, SHARED_SOURCES if sources else SHARED) for path in paths):
        return names, "a shared input changed: every one"
    if sources:
        return [script for script in every if any(touches_source(p, script) for p in paths)], None
    return [recipe[0] for recipe in every if any(touches(p, recipe) for p in paths)], None


def main(argv=None, root=ROOT, environ=None):
    parser = argparse.ArgumentParser(prog="lib/changed.py",
                                     description="Print the recipes a change from BASE to HEAD touches.")
    parser.add_argument("--json", action="store_true", help="print one JSON list instead of one path per line")
    parser.add_argument("--all", action="store_true", help="print every recipe (or script) at HEAD")
    parser.add_argument("--sources", action="store_true",
                        help="print the prepared-input scripts the change touches instead of the recipes")
    parser.add_argument("commits", nargs="*", metavar="BASE HEAD")
    options = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if options.all:
        if len(options.commits) > 1:
            parser.error("--all takes at most HEAD")
        base, head = None, (options.commits or ["HEAD"])[0]
    elif len(options.commits) == 2:
        base, head = options.commits
    else:
        parser.error("give BASE and HEAD, or --all [HEAD]")
    for name in (base, head):
        if name is not None and (not name or name.startswith("-")):
            parser.error(f"{name!r} is not a commit name")
    try:
        selected, note = select(root, base, head, environ, sources=options.sources)
    except Failure as failure:
        print(f"changed.py: {failure}", file=sys.stderr)
        return 1
    if note:
        print(f"changed.py: {note}", file=sys.stderr)
    if options.json:
        print(json.dumps(selected))
    else:
        for path in selected:
            print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
