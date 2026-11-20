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


def media(name, release, work=None, bundle=None):
    """A minimal media recipe of unemu/media/ibm-pc/NAME."""
    text = 'recipe = "0.1"\n'
    if work:
        text += f'work = "{work}"\n'
    text += (f'\n[media]\nname = "unemu/media/ibm-pc/{name}"\nplatform = "ibm-pc"\n'
             f'release = "{release}"\nrevision = 1\n')
    if bundle:
        text += f'\n[input.text]\nname = "{bundle}"\nsize = 1\nsha256 = "{"a" * 64}"\nbundle = true\n'
    return text


FIRMWARE = 'recipe = "0.1"\n\n[firmware]\nname = "unemu/firmware/ibm-pc/demo-bios"\nplatform = "ibm-pc"\nrevision = 1\n'

ALPHA = "recipes/media/alpha/alpha-1.0.media.toml"
ALPHA_720K = "recipes/media/alpha/alpha-1.0-720k.media.toml"
BETA = "recipes/media/beta/beta-2.media.toml"
BIOS = "recipes/firmware/demo-bios.firmware.toml"
SCRIPT_PATH = "recipes/media/alpha/source/build.sh"
EVERY = [BIOS, ALPHA_720K, ALPHA, BETA]


class Repository:
    """A git repository with four recipes (two of one work, with a work file,
    a bundled text and a prepared-input script), a library and docs."""

    def __init__(self, directory):
        self.root = pathlib.Path(directory) / "repo"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        files = {
            ALPHA: media("alpha", "1.0", work="alpha", bundle="licenses.txt"),
            ALPHA + ".lock/sha256/" + "1" * 64: "lock\n",
            ALPHA_720K: media("alpha", "1.0-720k", work="alpha", bundle="licenses.txt"),
            "recipes/media/alpha/alpha.work.toml": 'recipe = "0.1"\n\n[work]\nid = "alpha"\n',
            "recipes/media/alpha/licenses.txt": "licences\n",
            "recipes/media/alpha/notes.txt": "notes\n",
            SCRIPT_PATH: "#!/usr/bin/env bash\n",
            "recipes/media/alpha/source/helper.py": "pass\n",
            BETA: media("beta", "2"),
            "recipes/media/beta/licenses.txt": "not bundled by beta\n",
            BIOS: FIRMWARE,
        }
        for path in ("lib/source.sh", "lib/publish.py", "lib/recipes.py", "flake.nix", "flake.lock",
                     "upcli.toml", "docs/recipes.md", "tests/publish_test.py", "README.md", "AGENTS.md",
                     ".github/workflows/check.yml"):
            files[path] = f"{path}\n"
        for path, text in files.items():
            self.write(path, text)
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
        """A commit that edits PATHS (a TOML comment, so recipes still
        parse) and removes REMOVE; its id."""
        for path in paths:
            full = self.root / path
            previous = full.read_text(encoding="utf-8") if full.exists() else ""
            self.write(path, previous + "# changed\n")
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

    def selected(self, base, head="HEAD", *options):
        code, stdout, stderr = self.run(*options, base, head)
        if code != 0:
            raise AssertionError(f"exit {code}: {stderr}")
        return stdout.split()


class Changed(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.repo = Repository(self._directory.name)

    def test_a_recipe_change_selects_that_recipe(self):
        self.repo.change(BETA)
        self.assertEqual(self.repo.selected(self.repo.first), [BETA])
        self.repo.change(ALPHA, BIOS)
        self.assertEqual(self.repo.selected(self.repo.first), [BIOS, ALPHA, BETA])
        # Only the change between the two commits counts.
        self.assertEqual(self.repo.selected("HEAD~1"), [BIOS, ALPHA])
        self.assertEqual(self.repo.selected("HEAD"), [])

    def test_its_lock_bundled_inputs_and_work_file_select_a_recipe(self):
        for path, expected in (
            (ALPHA + ".lock/sha256/" + "1" * 64, [ALPHA]),
            (ALPHA + ".lock/sha256/" + "2" * 64, [ALPHA]),
            ("recipes/media/alpha/licenses.txt", [ALPHA_720K, ALPHA]),
            ("recipes/media/alpha/alpha.work.toml", [ALPHA_720K, ALPHA]),
            # beta bundles nothing and names no work.
            ("recipes/media/beta/licenses.txt", []),
            ("recipes/media/beta/beta.work.toml", []),
            # Neither a recipe's input nor its work file.
            ("recipes/media/alpha/notes.txt", []),
            (SCRIPT_PATH, []),
            ("recipes/media/alpha/source/helper.py", []),
            ("recipes/README.md", []),
        ):
            with self.subTest(path):
                before = self.repo.git("rev-parse", "HEAD")
                self.repo.change(path)
                self.assertEqual(self.repo.selected(before), expected)

    def test_a_shared_input_selects_every_recipe(self):
        for path in ("lib/source.sh", "lib/new-helper.py", "lib/publish.py", "upcli.toml",
                     ".github/workflows/check.yml", ".github/workflows/new.yml"):
            with self.subTest(path):
                before = self.repo.git("rev-parse", "HEAD")
                self.repo.change(path)
                code, stdout, stderr = self.repo.run(before, "HEAD")
                self.assertEqual(code, 0, stderr)
                self.assertEqual(stdout.split(), EVERY)
                self.assertIn("a shared input changed", stderr)

    def test_documentation_tests_and_the_toolchain_select_nothing(self):
        for path in ("docs/recipes.md", "docs/new.md", "tests/publish_test.py", "README.md", "AGENTS.md",
                     ".github/dependabot.yml", "librarian/x", "lib.md", "flake.nix", "flake.lock",
                     "flake.nix.orig"):
            with self.subTest(path):
                before = self.repo.git("rev-parse", "HEAD")
                self.repo.change(path)
                code, stdout, stderr = self.repo.run(before, "HEAD")
                self.assertEqual((code, stdout, stderr), (0, "", ""))

    def test_a_deleted_recipe_is_not_selected(self):
        self.repo.change(remove=[BETA])
        self.assertEqual(self.repo.selected(self.repo.first), [])
        self.repo.change(ALPHA, remove=[ALPHA_720K])
        self.assertEqual(self.repo.selected(self.repo.first), [ALPHA])

    def test_a_renamed_recipe_is_selected_under_its_new_name(self):
        new = "recipes/media/beta/beta-2.1.media.toml"
        self.repo.git("mv", BETA, new)
        self.repo.commit("rename")
        self.assertEqual(self.repo.selected(self.repo.first), [new])

    def test_files_that_are_no_recipes(self):
        for path, expected in (("recipes/media/alpha/alpha.toml", []), ("recipes/media/.hidden/x.media.toml", []),
                               ("recipes/x.firmware.toml.bak", []), ("elsewhere/x.media.toml", []),
                               # No recipe of its own, but a file of alpha's lock.
                               (ALPHA + ".lock/x.media.toml", [ALPHA])):
            with self.subTest(path):
                before = self.repo.git("rev-parse", "HEAD")
                self.repo.write(path, FIRMWARE)
                self.repo.commit("no recipe")
                self.assertEqual(self.repo.selected(before), expected)
        self.assertEqual(self.repo.run("--all")[1].split(), EVERY)
        # A recipe file that does not parse still counts, by its own path.
        before = self.repo.git("rev-parse", "HEAD")
        self.repo.write("recipes/firmware/broken.firmware.toml", "not toml [\n")
        self.repo.commit("broken")
        self.assertEqual(self.repo.selected(before), ["recipes/firmware/broken.firmware.toml"])

    def test_sources(self):
        for path, expected in (
            ("recipes/media/alpha/source/helper.py", [SCRIPT_PATH]),
            (SCRIPT_PATH, [SCRIPT_PATH]),
            ("recipes/media/alpha/licenses.txt", [SCRIPT_PATH]),
            ("recipes/media/alpha/notes.txt", [SCRIPT_PATH]),
            # A recipe, its lock and its work file are no input of the script.
            (ALPHA, []),
            (ALPHA + ".lock/sha256/" + "1" * 64, []),
            ("recipes/media/alpha/alpha.work.toml", []),
            ("recipes/media/beta/licenses.txt", []),
            ("lib/publish.py", []),
            ("upcli.toml", []),
            ("docs/recipes.md", []),
        ):
            with self.subTest(path):
                before = self.repo.git("rev-parse", "HEAD")
                self.repo.change(path)
                self.assertEqual(self.repo.selected(before, "HEAD", "--sources"), expected)
        for path in ("lib/source.sh", "flake.nix", "flake.lock", ".github/workflows/check.yml"):
            with self.subTest(path):
                before = self.repo.git("rev-parse", "HEAD")
                self.repo.change(path)
                code, stdout, stderr = self.repo.run("--sources", before, "HEAD")
                self.assertEqual((code, stdout.split()), (0, [SCRIPT_PATH]), stderr)
                self.assertIn("a shared input changed", stderr)
        code, stdout, _ = self.repo.run("--sources", "--all")
        self.assertEqual((code, stdout.split()), (0, [SCRIPT_PATH]))

    def test_the_null_base_and_an_unknown_base_select_everything(self):
        self.repo.change("docs/recipes.md")
        for base, note in ((ZERO, "null commit"), ("0" * 64, "null commit"),
                           ("1234567890abcdef1234567890abcdef12345678", "not a commit here"),
                           ("no-such-branch", "not a commit here")):
            with self.subTest(base):
                code, stdout, stderr = self.repo.run(base, "HEAD")
                self.assertEqual(code, 0, stderr)
                self.assertEqual(stdout.split(), EVERY)
                self.assertIn(note, stderr)
                code, stdout, stderr = self.repo.run("--sources", base, "HEAD")
                self.assertEqual(stdout.split(), [SCRIPT_PATH])
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
        head = self.repo.change(BETA)
        self.repo.write(ALPHA, "uncommitted\n")
        self.repo.write("lib/publish.py", "uncommitted\n")
        self.repo.write("recipes/media/new/new-1.media.toml", media("new", "1"))
        self.assertEqual(self.repo.selected(self.repo.first, head), [BETA])
        code, stdout, _ = self.repo.run("--all", self.repo.first)
        self.assertEqual(stdout.split(), EVERY)
        # The work file a recipe names is read at HEAD, not from the working
        # tree: alpha names "alpha" at HEAD.
        self.repo.write(ALPHA, media("alpha", "1.0", work="other"))
        before = self.repo.git("rev-parse", "HEAD")
        self.repo.git("add", "recipes/media/alpha/alpha.work.toml")
        self.repo.write("recipes/media/alpha/alpha.work.toml", 'recipe = "0.1"\n# edited\n')
        self.repo.git("add", "recipes/media/alpha/alpha.work.toml")
        self.repo.git("commit", "-q", "-m", "work file only")
        self.assertEqual(self.repo.selected(before), [ALPHA_720K, ALPHA])

    def test_json(self):
        self.repo.change(BETA)
        code, stdout, _ = self.repo.run("--json", self.repo.first, "HEAD")
        self.assertEqual((code, json.loads(stdout)), (0, [BETA]))
        code, stdout, _ = self.repo.run("--json", "HEAD", "HEAD")
        self.assertEqual((code, stdout), (0, "[]\n"))
        code, stdout, _ = self.repo.run("--json", "--all")
        self.assertEqual(json.loads(stdout), EVERY)

    def test_git_settings_of_the_caller_do_not_count(self):
        self.repo.change(BETA)
        other = pathlib.Path(self._directory.name) / "other"
        other.mkdir()
        environ = dict(GIT_ENV, GIT_DIR=str(other), GIT_WORK_TREE=str(other),
                       GIT_CONFIG_PARAMETERS="'diff.relative=true'")
        code, stdout, stderr = self.repo.run(self.repo.first, "HEAD", environ=environ)
        self.assertEqual((code, stdout.split()), (0, [BETA]), stderr)

    def test_usage_errors(self):
        for args in ((), ("HEAD",), ("a", "b", "c"), ("--all", "a", "b"), ("--bogus",),
                     ("-HEAD", "HEAD"), ("HEAD", ""), ("--", "--output=x", "HEAD")):
            code, stdout, _ = self.repo.run(*args)
            self.assertEqual((code, stdout), (2, ""), args)


if __name__ == "__main__":
    unittest.main()
