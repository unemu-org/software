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
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import tomllib
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
    text = 'recipe = "1.0"\n'
    if work:
        text += f'work = "{work}"\n'
    text += (f'\n[media]\nname = "unemu/media/ibm-pc/{name}"\nplatform = "ibm-pc"\n'
             f'release = "{release}"\nrevision = 1\n')
    text += '\n[license]\nexpression = "GPL-2.0-or-later"\n'
    if bundle:
        text += f'\n[input.text]\nname = "{bundle}"\nsize = 1\nsha256 = "{"a" * 64}"\nbundle = true\n'
    return text


FIRMWARE = ('recipe = "1.0"\n\n[firmware]\nname = "unemu/firmware/ibm-pc/demo-bios"\nplatform = "ibm-pc"\n'
            'revision = 1\n\n[license]\nexpression = "GPL-2.0-or-later"\n')

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
            "recipes/media/alpha/alpha.work.toml": 'recipe = "1.0"\n\n[work]\nid = "alpha"\n',
            "recipes/media/alpha/licenses.txt": "licences\n",
            "recipes/media/alpha/notes.txt": "notes\n",
            SCRIPT_PATH: "#!/usr/bin/env bash\n",
            "recipes/media/alpha/source/helper.py": "pass\n",
            BETA: media("beta", "2"),
            "recipes/media/beta/licenses.txt": "not bundled by beta\n",
            BIOS: FIRMWARE,
        }
        for path in ("lib/source.sh", "lib/publish.py", "lib/recipes.py", "flake.nix", "flake.lock",
                     "unemu.toml", "docs/recipes.md", "tests/publish_test.py", "README.md", "AGENTS.md",
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


def workflow_run(workflow, step):
    """The literal shell body of a named workflow step, without a YAML dependency."""
    text = (SCRIPT.parent.parent / ".github/workflows" / workflow).read_text()
    after = text.split(f"      - name: {step}\n", 1)[1].split("        run: ", 1)[1]
    if not after.startswith("|\n"):
        return after.splitlines()[0] + "\n"
    after = after[2:]
    lines = []
    for line in after.splitlines():
        if line.strip() and not line.startswith("          "):
            break
        lines.append(line)
    return textwrap.dedent("\n".join(lines)) + "\n"


def workflow_steps(text):
    """Read this repo's block-style step mappings without a YAML dependency.

    Keep nested mappings (with/env) as text. A run's indented shell body
    cannot supply a step field or an action input.
    """
    for block in re.split(r"(?m)^      - ", text)[1:]:
        # End at the next job, ignoring its job-level keys and comments.
        block = re.split(r"(?m)^  \S", block)[0]
        fields = {}
        key = None
        for line in ("        " + block).splitlines():
            match = re.fullmatch(r"        ([\w-]+):\s*(.*)", line)
            if match:
                key, value = match.groups()
                if key in fields:
                    raise AssertionError(f"duplicate step key: {key}")
                fields[key] = value + "\n"
            elif key and line.startswith("          "):
                fields[key] += line[10:] + "\n"
        yield fields


def workflow_scalars(text):
    """Parse the scalar inputs of a step's block mapping (with or env)."""
    fields = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([\w-]+):\s+(.+?)\s*(?: #.*)?", line)
        if not match:
            raise AssertionError(f"expected a block scalar mapping: {line}")
        key, value = match.groups()
        if key in fields:
            raise AssertionError(f"duplicate mapping key: {key}")
        fields[key] = value.strip("\"'")
    return fields


class WorkflowPolicy(unittest.TestCase):
    def test_publish_handover_handles_recipes_and_already_published_outputs(self):
        script = workflow_run("publish.yml", "What to push")
        for mode, output_pending in (("outputs", False), ("outputs", True),
                                     ("recipes", False), ("both", False), ("both", True)):
            with self.subTest(mode=mode, output_pending=output_pending), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                runner = root / "runner"
                runner.mkdir()
                if mode != "recipes":
                    (runner / "report.json").write_text(json.dumps([
                        {"outcome": "dry run" if output_pending else "published"}]))
                    (runner / "dry-run.txt").write_text("output dry run\n")
                if mode != "outputs":
                    (runner / "recipes.json").write_text('[{"outcome": "guard only"}]')
                    (runner / "recipes.txt").write_text("recipe guards\n")
                if output_pending:
                    (runner / "store/images").mkdir(parents=True)
                    (runner / "store/images/index.json").write_text("checked OCI layout")
                    (runner / "store/store.toml").write_text("schema = 1\n")
                output = root / "github-output"
                done = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=root,
                                      env=dict(GIT_ENV, MODE=mode, RUNNER_TEMP=str(runner), GITHUB_OUTPUT=str(output)),
                                      capture_output=True, text=True, check=False)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(dict(line.split("=", 1) for line in output.read_text().splitlines()),
                                 {"any": str(output_pending or mode != "outputs").lower(),
                                  "outputs": str(output_pending).lower()})
                self.assertEqual((root / "handover/recipes.json").exists(), mode != "outputs")
                self.assertEqual((root / "handover/store/images/index.json").exists(), output_pending)
                self.assertEqual((root / "handover/store/store.toml").exists(), output_pending)
                self.assertEqual((root / "handover/store").exists(), output_pending)

    def test_recipe_publication_is_approved_and_follows_outputs(self):
        check = (HERE.parent / ".github/workflows/check.yml").read_text()
        guard = next(s for s in workflow_steps(check) if s.get("name", "").strip() == "Check every recipe artifact guard")
        self.assertNotIn("if", guard)
        self.assertIn("--recipes --guard-only", guard["run"])
        self.assertNotIn("--push", guard["run"])
        self.assertNotIn("--recipes", (HERE.parent / ".github/workflows/experimental.yml").read_text())
        text = (HERE.parent / ".github/workflows/publish.yml").read_text()
        self.assertIn("options: [outputs, recipes, both]", text)
        mode = text.split("      mode:\n", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("        default: both\n", mode)
        build, push = text.split("\n  push:\n")
        self.assertIn("any: ${{ steps.built.outputs.any }}", build)
        self.assertIn("outputs: ${{ steps.built.outputs.outputs }}", build)
        self.assertIn("needs.build.outputs.any == 'true'", push)
        self.assertIn("environment: media", push)
        self.assertNotIn("secrets.", build)
        self.assertIn("--recipes --guard-only", build)
        step = next(s for s in workflow_steps(build) if s.get("name", "").strip() == "Build and check")
        self.assertEqual(step["if"].strip(), "inputs.mode != 'recipes'")
        steps = list(workflow_steps(push))
        output_index = next(i for i, s in enumerate(steps) if s.get("name", "").strip() == "Push to unemu/media/ and unemu/firmware/")
        recipe_index = next(i for i, s in enumerate(steps) if s.get("name", "").strip() == "Push to unemu/recipes/")
        self.assertLess(output_index, recipe_index)
        download = next(s for s in steps if s.get("name", "").strip() == "Take the store of the build job")
        for step in (download, steps[output_index]):
            self.assertEqual(step["if"].strip(), "needs.build.outputs.outputs == 'true'")
        recipe = steps[recipe_index]
        self.assertEqual(recipe["if"].strip(), "inputs.mode != 'outputs'")
        self.assertIn("--recipes --push", recipe["run"])
        self.assertNotIn("--guard-only", recipe["run"])
        self.assertNotIn("handover/", recipe["run"])
        self.assertEqual(workflow_scalars(recipe["env"])["UNEMU_REGISTRY_USER"], "ci-promote")
        self.assertIn("--skip-published --no-build", steps[output_index]["run"])
        self.assertIn("words=(recipes/media recipes/firmware)", build)
        self.assertIn("words=(recipes/media recipes/firmware)", push)

    def test_actions_follow_the_unemu_release_pin(self):
        root = HERE.parent
        pin = tomllib.loads((root / "unemu.toml").read_text())
        action = "unemu-org/unemu/.github/actions/setup-unemu@" + pin["commit"]
        for name in ("check.yml", "experimental.yml", "publish.yml"):
            with self.subTest(workflow=name):
                text = (root / ".github/workflows" / name).read_text()
                self.assertNotRegex(text, r"lib/unemu\.py[\"']?[\s\\]+fetch\b")
                # Check every uses:, including reusable job workflows. Requiring
                # block syntax also prevents inline YAML from bypassing parsing.
                uses = re.findall(r"(?m)^\s*(?:-\s+)?uses:\s*(\S+)", text)
                self.assertTrue(uses, name)
                self.assertEqual(len(uses), len(re.findall(r"\buses:", text)))
                for use in uses:
                    self.assertRegex(use.strip("\"'"), r"^[^@\s]+@[0-9a-fA-F]{40}$")
                steps = list(workflow_steps(text))
                installers = [s for s in steps if "setup-unemu" in s.get("uses", "")]
                self.assertTrue(installers, name)
                self.assertEqual(len(installers), sum("setup-unemu" in use for use in uses))
                for step in installers:
                    use = step["uses"].split(" #", 1)[0].strip().strip("\"'")
                    self.assertEqual(use, action)
                    inputs = workflow_scalars(step.get("with", ""))
                    self.assertEqual(inputs.get("version"), pin["tag"])
                    self.assertEqual(inputs.get("commit"), pin["commit"])
                    self.assertTrue(step.get("id", "").strip())

    def test_cli_outputs_and_registry_secrets_use_step_env(self):
        for name in ("check.yml", "experimental.yml", "publish.yml"):
            text = (HERE.parent / ".github/workflows" / name).read_text()
            self.assertNotIn("$RUNNER_TEMP/unemu/unemu", text)
            jobs = re.split(r"(?m)^  ([\w-]+):\s*\n", text.split("\njobs:\n", 1)[1])
            for job, body in zip(jobs[1::2], jobs[2::2]):
                with self.subTest(workflow=name, job=job):
                    self.assertNotIn("secrets.", body.split("    steps:\n", 1)[0])
                    if job != "push":
                        self.assertNotIn("secrets.", body)
                    steps = list(workflow_steps(body))
                    installer_ids = {s["id"].strip() for s in steps if "setup-unemu" in s.get("uses", "")}
                    for step in steps:
                        run = step.get("run", "")
                        self.assertNotIn("${{", run)
                        if '"$UNEMU"' in run:
                            env = workflow_scalars(step.get("env", ""))
                            self.assertIn(env.get("UNEMU"),
                                          {"${{ steps." + id_ + ".outputs.path }}" for id_ in installer_ids})
                        if "secrets." in "".join(step.values()):
                            self.assertEqual(job, "push")
                            self.assertTrue(step["name"].startswith("Push to "))
                            self.assertIn("secrets.", step.get("env", ""))
                            self.assertNotIn("secrets.", "".join(v for k, v in step.items() if k != "env"))


class WorkflowSelection(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Repository(temporary.name)
        self.private = "recipes/private/media/demo/demo.media.toml"
        text = media("demo", "1.0").replace("unemu/media/", "private/media/").replace(
            '\n[license]\nexpression = "GPL-2.0-or-later"\n', "")
        self.repo.write(self.private, text)
        self.repo.change(BETA)
        # Run the actual selection code from the working tree, while it
        # reads recipe blobs only from the fixture's committed trees.
        for name in ("changed.py", "recipes.py"):
            shutil.copyfile(SCRIPT.parent / name, self.repo.root / "lib" / name)

    def selection(self, workflow, step, event):
        script = self.repo.root / "selection.sh"
        script.write_text(workflow_run(workflow, step))
        output = self.repo.root / "outputs"
        output.write_text("")
        env = dict(GIT_ENV, EVENT=event, PR_BASE=self.repo.first, BEFORE=self.repo.first,
                   GITHUB_OUTPUT=str(output))
        done = subprocess.run(["bash", "-euo", "pipefail", str(script)], cwd=self.repo.root,
                              env=env, capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        return {key: json.loads(value) for key, _, value in
                (line.partition("=") for line in output.read_text().splitlines())}

    def test_check_builds_only_open_but_statically_checks_all(self):
        for event in ("pull_request", "push", "schedule", "workflow_dispatch"):
            selected = self.selection("check.yml", "Select the recipes and scripts", event)
            self.assertEqual(selected["recipes"], [BETA] if event in ("pull_request", "push") else EVERY)
        self.assertEqual(workflow_run("check.yml", "Check every recipe").strip(),
                         'python3 -I lib/recipes.py check --cli "$UNEMU"')
        workflow = (SCRIPT.parent.parent / ".github/workflows/check.yml").read_text()
        self.assertIn("SELECTION: ${{ needs.tests.outputs.recipes }}", workflow)
        for name in ("Build the selected recipes against their locks", "Build them again from the inputs alone"):
            self.assertIn(f"- name: {name}\n        if: env.SELECTION != '[]'", workflow)

    def test_experimental_selects_only_open_on_push_and_manual_run(self):
        for event in ("push", "workflow_dispatch"):
            selected = self.selection("experimental.yml", "Select the recipes", event)
            self.assertEqual(selected["selection"], [BETA] if event == "push" else EVERY)

    def test_a_private_only_push_has_no_build_selection(self):
        # Change only the private recipe, leaving the copied real libraries
        # uncommitted: the selection reads git's trees, not these files.
        before = self.repo.git("rev-parse", "HEAD")
        self.repo.first = before
        self.repo.write(self.private, (self.repo.root / self.private).read_text() + "# edit\n")
        self.repo.git("add", self.private)
        self.repo.git("commit", "-q", "-m", "private metadata")
        for workflow, step, key in (("check.yml", "Select the recipes and scripts", "recipes"),
                                    ("experimental.yml", "Select the recipes", "selection")):
            self.assertEqual(self.selection(workflow, step, "push")[key], [])


class Changed(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.repo = Repository(self._directory.name)

    def test_private_recipes_are_static_only_in_both_selections(self):
        private = "recipes/private/media/demo/demo.media.toml"
        folder = private.rpartition("/")[0]
        text = media("demo", "1.0", work="demo", bundle="notes.txt").replace(
            "unemu/media/", "private/media/").replace('\n[license]\nexpression = "GPL-2.0-or-later"\n', "")
        self.repo.write(private, text)
        self.repo.write(folder + "/notes.txt", "Our note\n")
        self.repo.write(folder + "/demo.work.toml", 'recipe = "1.0"\n[work]\nid = "demo"\n')
        # A script in the private tree must never become a prepared input.
        self.repo.write(folder + "/source/build.sh", "exit 99\n")
        self.repo.commit("private description")
        self.assertEqual(self.repo.selected(self.repo.first), [private])
        self.assertEqual(self.repo.selected(self.repo.first, "HEAD", "--open-only"), [])
        for path in (private, folder + "/notes.txt", folder + "/demo.work.toml", private + ".lock/sha256/a"):
            with self.subTest(path=path):
                before = self.repo.git("rev-parse", "HEAD")
                self.repo.change(path)
                self.assertEqual(self.repo.selected(before), [private])
                self.assertEqual(self.repo.selected(before, "HEAD", "--open-only"), [])
                self.assertEqual(self.repo.selected(before, "HEAD", "--sources"), [])
        self.repo.change(BETA)
        self.assertEqual(self.repo.selected(self.repo.first), [BETA, private])
        self.assertEqual(self.repo.selected(self.repo.first, "HEAD", "--open-only"), [BETA])
        self.repo.change("lib/source.sh")
        for options in (("--all",), (ZERO, "HEAD"), ("unknown-base", "HEAD"), ("HEAD~1", "HEAD")):
            code, stdout, _ = self.repo.run("--json", *options)
            self.assertEqual((code, json.loads(stdout)), (0, [*EVERY, private]))
            code, stdout, _ = self.repo.run("--json", "--open-only", *options)
            self.assertEqual((code, json.loads(stdout)), (0, EVERY))
            code, stdout, _ = self.repo.run("--json", "--sources", *options)
            self.assertEqual((code, json.loads(stdout)), (0, [SCRIPT_PATH]))

    def test_invalid_recipes_never_enter_the_build_selection(self):
        for text in ("not toml [", media("demo", "1.0").replace("unemu/media/", "private/media/"),
                     media("demo", "1.0").replace("unemu/media/", "private/media/").replace(
                         '\n[license]\nexpression = "GPL-2.0-or-later"\n', "") +
                     '\n[input.game]\nname = "game.img"\nurls = ["https://example.invalid/game"]\n'):
            before = self.repo.git("rev-parse", "HEAD")
            path = "recipes/private/media/demo/demo.media.toml"
            self.repo.write(path, text)
            self.repo.commit("invalid description")
            self.assertEqual(self.repo.selected(before), [path])
            code, stdout, stderr = self.repo.run("--open-only", before, "HEAD")
            self.assertEqual((code, stdout), (1, ""))
            self.assertIn("cannot select recipes for builds", stderr)

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
        for path in ("lib/source.sh", "lib/new-helper.py", "lib/publish.py", "unemu.toml",
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
            ("unemu.toml", []),
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
        self.repo.write("recipes/media/alpha/alpha.work.toml", 'recipe = "1.0"\n# edited\n')
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
                     ("--sources", "--open-only", "--all"),
                     ("-HEAD", "HEAD"), ("HEAD", ""), ("--", "--output=x", "HEAD")):
            code, stdout, _ = self.repo.run(*args)
            self.assertEqual((code, stdout), (2, ""), args)


if __name__ == "__main__":
    unittest.main()
