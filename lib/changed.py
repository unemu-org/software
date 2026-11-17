# SPDX-License-Identifier: AGPL-3.0-or-later
"""The recipes a change touches, for CI: which ones to build and check.

    python3 -I lib/changed.py [--json] BASE HEAD
    python3 -I lib/changed.py [--json] --all [HEAD]

BASE and HEAD are commits of this repository (HEAD by default for --all).
Prints the name of every recipe at HEAD that the change from BASE to HEAD
touches, one per line in name order (with --json, as one JSON list):

  - a recipe is a directory recipes/<name>/ at HEAD with a manifest,
    media.toml or firmware.toml;
  - a file changed under recipes/<name>/ selects that recipe; a deleted
    recipe is not selected, since it is no longer at HEAD;
  - a change to a shared input selects every recipe: lib/ (the recipe
    library, the manifest checker, the pipeline), flake.nix and flake.lock
    (the toolchain), upcli.toml (the CLI that imports and digests every set
    and package) and .github/workflows/ (the CI steps that build, compare
    and dry-run the recipes);
  - anything else selects nothing: docs/ and the other documentation,
    tests/ (the README's check command runs them on every change; they are
    not inputs of any recipe) and the tooling configuration.

Every recipe is selected, as with --all, when BASE is all zeros (what
GitHub sends for a new branch) or is not a commit here (a force push whose
old head is gone). A rename is a deletion plus an addition: the new name
is selected, the old one is gone. Nothing is printed when nothing is
selected.

The answer comes from git alone: `git diff --name-only -z --no-renames`
between the two commits' trees, and `git ls-tree` of HEAD, never the
working tree. git runs without the user's or the system's configuration.
Exit status: 0 with the selection printed, 1 when HEAD is not a commit or
git fails, 2 for a usage error.

Standard library only. Run it with -I.
"""

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

MANIFEST_NAMES = ("media.toml", "firmware.toml")
# A change under any of these selects every recipe: a directory ends in /.
SHARED = ("lib/", "flake.nix", "flake.lock", "upcli.toml", ".github/workflows/")
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


def recipes_at(root, head, env):
    """Every recipe at HEAD: recipes/<name>/ holding a manifest."""
    paths = split_z(git(root, ["ls-tree", "-r", "-z", "--name-only", "--full-tree", head, "--", "recipes/"],
                        env))
    found = set()
    for path in paths:
        parts = path.split("/")
        if len(parts) == 3 and parts[0] == "recipes" and parts[2] in MANIFEST_NAMES:
            found.add(parts[1])
    return sorted(found)


def changed_paths(root, base, head, env):
    """Every path that differs between the trees of BASE and HEAD."""
    return split_z(git(root, ["diff", "--name-only", "-z", "--no-renames", "--no-relative", "--no-ext-diff",
                              base, head, "--"], env))


def shared(path):
    return any(path == item or (item.endswith("/") and path.startswith(item)) for item in SHARED)


def select(root, base, head, environ=None):
    """The recipes at HEAD that the change from BASE (None: every recipe)
    to HEAD touches, and a note on stderr's behalf (or None)."""
    env = git_environment(os.environ if environ is None else environ)
    head_id = commit(root, head, env)
    if head_id is None:
        raise Failure(f"HEAD {head!r} is not a commit in {root}")
    every = recipes_at(root, head_id, env)
    if base is None:
        return every, None
    if ZERO.fullmatch(base):
        return every, f"BASE {base} is the null commit (a new branch): every recipe"
    base_id = commit(root, base, env)
    if base_id is None:
        return every, f"BASE {base!r} is not a commit here (a force push?): every recipe"
    paths = changed_paths(root, base_id, head_id, env)
    if any(shared(path) for path in paths):
        return every, "a shared input changed: every recipe"
    touched = {path.split("/")[1] for path in paths if path.startswith("recipes/") and path.count("/") >= 2}
    return [recipe for recipe in every if recipe in touched], None


def main(argv=None, root=ROOT, environ=None):
    parser = argparse.ArgumentParser(prog="lib/changed.py",
                                     description="Print the recipes a change from BASE to HEAD touches.")
    parser.add_argument("--json", action="store_true", help="print one JSON list instead of one name per line")
    parser.add_argument("--all", action="store_true", help="print every recipe at HEAD")
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
        selected, note = select(root, base, head, environ)
    except Failure as failure:
        print(f"changed.py: {failure}", file=sys.stderr)
        return 1
    if note:
        print(f"changed.py: {note}", file=sys.stderr)
    if options.json:
        print(json.dumps(selected))
    else:
        for recipe in selected:
            print(recipe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
