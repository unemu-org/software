# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests of lib/changed.py, offline, in throwaway git repositories.

    python3 -I tests/changed_test.py
"""

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / "lib" / "changed.py"

_spec = importlib.util.spec_from_file_location("changed", SCRIPT)
changed = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = changed
_spec.loader.exec_module(changed)

ZERO = "0" * 40
# What git needs to commit here, and nothing from the caller's own setup.
GIT_ENV = {
    "PATH": os.environ.get("PATH", os.defpath),
    "HOME": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    "LC_ALL": "C",
}


class Repository:
    """A git repository with three recipes, a library and docs."""

    def __init__(self, directory):
        self.root = pathlib.Path(directory) / "repo"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        for path in ("recipes/alpha/build.sh", "recipes/alpha/media.toml",
                     "recipes/beta/build.sh", "recipes/beta/firmware.toml", "recipes/beta/helper.py",
                     "recipes/gamma/build.sh", "recipes/gamma/media.toml",
                     "lib/recipe.sh", "lib/publish.py", "flake.nix", "flake.lock", "upcli.toml",
                     "docs/recipes.md", "tests/publish_test.py", "README.md", "AGENTS.md",
                     ".github/workflows/check.yml"):
            self.write(path, f"{path}\n")
        self.first = self.commit("first")

    def write(self, path, text):
        full = self.root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")

    def git(self, *args):
        done = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, encoding="utf-8",
                              env=GIT_ENV, check=True)
        return done.stdout.strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def change(self, *paths, remove=(), message="change"):
        """A commit that edits PATHS and removes REMOVE; its id."""
        for path in paths:
            full = self.root / path
            previous = full.read_text(encoding="utf-8") if full.exists() else ""
            self.write(path, previous + "changed\n")
        for path in remove:
            self.git("rm", "-q", "-r", path)
        return self.commit(message)

    def run(self, *args, environ=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = changed.main(list(args), root=self.root, environ=environ or GIT_ENV)
            except SystemExit as stop:
                code = stop.code
        return code, stdout.getvalue(), stderr.getvalue()

    def selected(self, base, head="HEAD"):
        code, stdout, stderr = self.run(base, head)
        if code != 0:
            raise AssertionError(f"exit {code}: {stderr}")
        return stdout.split()


class Changed(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.repo = Repository(self._directory.name)

    def test_a_recipe_change_selects_that_recipe(self):
        self.repo.change("recipes/beta/helper.py")
        self.assertEqual(self.repo.selected(self.repo.first), ["beta"])
        self.repo.change("recipes/gamma/media.toml", "recipes/alpha/build.sh")
        self.assertEqual(self.repo.selected(self.repo.first), ["alpha", "beta", "gamma"])
        # Only the change between the two commits counts.
        self.assertEqual(self.repo.selected("HEAD~1"), ["alpha", "gamma"])
        self.assertEqual(self.repo.selected("HEAD"), [])

    def test_a_shared_input_selects_every_recipe(self):
        for path in ("lib/recipe.sh", "lib/new-helper.py", "flake.nix", "flake.lock", "upcli.toml",
                     ".github/workflows/check.yml", ".github/workflows/new.yml"):
            with self.subTest(path):
                before = self.repo.git("rev-parse", "HEAD")
                self.repo.change(path)
                code, stdout, stderr = self.repo.run(before, "HEAD")
                self.assertEqual(code, 0, stderr)
                self.assertEqual(stdout.split(), ["alpha", "beta", "gamma"])
                self.assertIn("a shared input changed", stderr)

    def test_documentation_and_tests_select_nothing(self):
        for path in ("docs/recipes.md", "docs/new.md", "tests/publish_test.py", "tests/fixtures/x/media.toml",
                     "README.md", "AGENTS.md", ".github/dependabot.yml", "recipes/README.md",
                     "librarian/x", "lib.md", "flake.nix.orig"):
            with self.subTest(path):
                before = self.repo.git("rev-parse", "HEAD")
                self.repo.change(path)
                code, stdout, stderr = self.repo.run(before, "HEAD")
                self.assertEqual((code, stdout, stderr), (0, "", ""))

    def test_a_deleted_recipe_is_not_selected(self):
        self.repo.change(remove=["recipes/alpha"])
        self.assertEqual(self.repo.selected(self.repo.first), [])
        self.repo.change("recipes/beta/build.sh", remove=["recipes/gamma/media.toml"])
        # gamma lost its manifest: it is no recipe any more.
        self.assertEqual(self.repo.selected(self.repo.first), ["beta"])

    def test_a_renamed_recipe_is_selected_under_its_new_name(self):
        self.repo.git("mv", "recipes/alpha", "recipes/delta")
        self.repo.commit("rename")
        self.assertEqual(self.repo.selected(self.repo.first), ["delta"])

    def test_a_file_moved_out_of_a_recipe_selects_it(self):
        # git would see a rename (the same bytes) and name only the new path,
        # docs/notes.md; the recipe lost a file all the same.
        self.repo.write("recipes/alpha/notes.md", "notes of the alpha recipe\n" * 20)
        before = self.repo.commit("notes")
        self.repo.git("mv", "recipes/alpha/notes.md", "docs/notes.md")
        self.repo.commit("move the notes")
        self.assertEqual(self.repo.selected(before), ["alpha"])

    def test_a_directory_without_a_manifest_is_no_recipe(self):
        self.repo.change("recipes/wip/build.sh")
        self.assertEqual(self.repo.selected(self.repo.first), [])
        code, stdout, _ = self.repo.run("--all")
        self.assertEqual((code, stdout.split()), (0, ["alpha", "beta", "gamma"]))
        self.repo.change("recipes/wip/firmware.toml")
        self.assertEqual(self.repo.selected(self.repo.first), ["wip"])

    def test_the_null_base_and_an_unknown_base_select_every_recipe(self):
        self.repo.change("docs/recipes.md")
        for base, note in ((ZERO, "null commit"), ("0" * 64, "null commit"),
                           ("1234567890abcdef1234567890abcdef12345678", "not a commit here"),
                           ("no-such-branch", "not a commit here")):
            with self.subTest(base):
                code, stdout, stderr = self.repo.run(base, "HEAD")
                self.assertEqual(code, 0, stderr)
                self.assertEqual(stdout.split(), ["alpha", "beta", "gamma"])
                self.assertIn(note, stderr)
        # Not quite the null commit: an ordinary (unknown) name.
        code, stdout, stderr = self.repo.run("0" * 39, "HEAD")
        self.assertIn("not a commit here", stderr)

    def test_head_must_be_a_commit(self):
        for head in ("no-such-branch", "1234567890abcdef1234567890abcdef12345678",
                     self.repo.git("rev-parse", "HEAD:README.md")):
            code, stdout, stderr = self.repo.run(self.repo.first, head)
            self.assertEqual(code, 1, head)
            self.assertEqual(stdout, "")
            self.assertIn("is not a commit", stderr)

    def test_the_answer_comes_from_the_commits_not_the_working_tree(self):
        head = self.repo.change("recipes/alpha/build.sh")
        self.repo.write("recipes/beta/build.sh", "uncommitted\n")
        self.repo.write("lib/recipe.sh", "uncommitted\n")
        self.repo.write("recipes/new/media.toml", "uncommitted\n")
        self.assertEqual(self.repo.selected(self.repo.first, head), ["alpha"])
        code, stdout, _ = self.repo.run("--all", self.repo.first)
        self.assertEqual(stdout.split(), ["alpha", "beta", "gamma"])
        # An older HEAD: the recipes of that commit. (This commit takes the
        # files written above too.)
        self.repo.change("recipes/epsilon/media.toml")
        self.assertEqual(self.repo.run("--all", head)[1].split(), ["alpha", "beta", "gamma"])
        self.assertEqual(self.repo.run("--all")[1].split(), ["alpha", "beta", "epsilon", "gamma", "new"])

    def test_json(self):
        self.repo.change("recipes/beta/build.sh")
        code, stdout, _ = self.repo.run("--json", self.repo.first, "HEAD")
        self.assertEqual((code, json.loads(stdout)), (0, ["beta"]))
        code, stdout, _ = self.repo.run("--json", "HEAD", "HEAD")
        self.assertEqual((code, stdout), (0, "[]\n"))
        code, stdout, _ = self.repo.run("--json", "--all")
        self.assertEqual(json.loads(stdout), ["alpha", "beta", "gamma"])

    def test_git_settings_of_the_caller_do_not_count(self):
        self.repo.change("recipes/beta/build.sh")
        other = pathlib.Path(self._directory.name) / "other"
        other.mkdir()
        environ = dict(GIT_ENV, GIT_DIR=str(other), GIT_WORK_TREE=str(other), GIT_CONFIG_PARAMETERS="'diff.relative=true'")
        code, stdout, stderr = self.repo.run(self.repo.first, "HEAD", environ=environ)
        self.assertEqual((code, stdout.split()), (0, ["beta"]), stderr)

    def test_usage_errors(self):
        for args in ((), ("HEAD",), ("a", "b", "c"), ("--all", "a", "b"), ("--bogus",),
                     ("-HEAD", "HEAD"), ("HEAD", ""), ("--", "--output=x", "HEAD")):
            code, stdout, _ = self.repo.run(*args)
            self.assertEqual((code, stdout), (2, ""), args)


if __name__ == "__main__":
    unittest.main()
