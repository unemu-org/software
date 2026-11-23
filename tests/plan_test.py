# SPDX-License-Identifier: AGPL-3.0-or-later
"""Offline survey tests; the fake unemu accepts only recipe plan --json."""

import contextlib
import copy
import importlib.util
import io
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "lib/plan.py"
_spec = importlib.util.spec_from_file_location("plan", SCRIPT)
plan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plan)

SHA = "a" * 64
MEDIA = '''recipe = "1.0"
[media]
name = "private/media/ibm-pc/demo"
release = "1.0-720k"
revision = 2
'''
FIRMWARE = '''recipe = "1.0"
[firmware]
name = "unemu/firmware/ibm-pc/bios"
revision = 1
'''
# The fields and statuses measured from unemu 0.0.4; notably `name` has no
# tag and findings replace the usual inputs/outputs arrays on check failure.
INPUT = {"form": "file", "found": None, "id": "archive", "ids": [],
         "name": "GAME.ZIP", "near_misses": [], "optional": False, "reason": None,
         "sha256": SHA, "size": 940242, "status": "missing",
         "urls": ["https://example.org/GAME.ZIP"], "used_by": ["Disk 1 of 1"]}


def report(*inputs, complete=False):
    return {"complete": complete, "inputs": list(inputs), "kind": "media", "libraries": [],
            "members": [], "name": "private/media/ibm-pc/demo", "outputs": [], "payloads": [],
            "recipe": "demo.media.toml", "steps": [], "store": None, "store_warnings": [],
            "summary": "can build: all inputs found or not needed" if complete else "missing 1 of 2 inputs"}


FAKE_CLI = r'''#!@PYTHON@ -I
import json, os, pathlib, sys
with open(@CONFIG@) as handle:
    config = json.load(handle)
argv = sys.argv[1:]
with open(@LOG@, "a") as handle:
    handle.write(json.dumps({"argv": argv, "env": dict(os.environ)}) + "\n")
if argv[:2] != ["recipe", "plan"] or argv[3:4] != ["--json"]:
    sys.exit(93)
answer = config[pathlib.Path(argv[2]).name]
print(answer.get("raw", json.dumps(answer["plan"])))
print(answer.get("stderr", ""), file=sys.stderr, end="")
sys.exit(answer.get("code", 0 if answer["plan"].get("complete") else 1))
'''


class Survey(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = pathlib.Path(temporary.name)
        self.recipes = self.root / "recipes"
        self.recipes.mkdir()
        self.config = self.root / "fake.json"
        self.log = self.root / "calls.jsonl"
        self.cli = self.root / "unemu"
        self.cli.write_text(FAKE_CLI.replace("@PYTHON@", sys.executable)
                            .replace("@CONFIG@", repr(str(self.config))).replace("@LOG@", repr(str(self.log))))
        self.cli.chmod(0o700)
        self.env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(self.root / "own home"),
                    "XDG_CONFIG_HOME": str(self.root / "own config"), "LC_ALL": "pl_PL.UTF-8", "TZ": "Europe/Warsaw"}
        self.responses = {}
        self.media = self.add("games/demo.media.toml", MEDIA, report(INPUT))
        self.firmware = self.add("bios.firmware.toml", FIRMWARE, report(complete=True))

    def add(self, name, text, response):
        path = self.recipes / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.responses[path.name] = {"plan": response}
        return path

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def tearDown(self):
        # Every invocation, even tests of failures, is a read-only local plan.
        for call in self.calls():
            argv = call["argv"]
            self.assertEqual(argv[:2], ["recipe", "plan"])
            self.assertEqual(argv[3], "--json")
            self.assertTrue(pathlib.Path(argv[2]).is_file())
            for forbidden in ("build", "fetch", "push", "--fetch", "--registry", "--keep-as"):
                self.assertNotIn(forbidden, argv)

    def run_plan(self, *args, env=None, default_cli=True):
        self.config.write_text(json.dumps(self.responses))
        self.log.unlink(missing_ok=True)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = plan.main((["--cli", str(self.cli)] if default_cli else []) + list(map(str, args)),
                             root=self.root, environ=self.env if env is None else env)
        return code, stdout.getvalue(), stderr.getvalue()

    def json_rows(self, *args):
        code, stdout, stderr = self.run_plan("--json", *args)
        self.assertEqual((code, stderr), (0, ""))
        return json.loads(stdout)

    def test_default_multiple_recipes_and_json(self):
        rows = self.json_rows()
        self.assertEqual([r["status"] for r in rows], ["ready", "needs --fetch"])
        self.assertEqual([r["refs"] for r in rows], [["unemu/firmware/ibm-pc/bios:r1"],
                                                    ["private/media/ibm-pc/demo:1.0-720k-r2"]])
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(rows[1]["missing"][0]["size"], INPUT["size"])

    def test_private_layout_surveys_missing_user_files_beside_open_recipes(self):
        private = self.recipes / "private/media/demo/demo.media.toml"
        private.parent.mkdir(parents=True)
        self.media.rename(private)
        item = copy.deepcopy(INPUT)
        item["urls"] = []
        self.responses[private.name]["plan"] = report(item)
        rows = self.json_rows()
        self.assertEqual([r["status"] for r in rows], ["ready", "missing"])
        self.assertEqual(rows[1]["refs"], ["private/media/ibm-pc/demo:1.0-720k-r2"])
        self.assertEqual(rows[1]["missing"][0]["urls"], [])
        self.assertEqual(len(self.calls()), 2)

    def test_folders_ignore_work_hidden_and_locks_and_deduplicate(self):
        for name in ("games/demo.work.toml", "games/.hidden/x.media.toml",
                     "games/demo.media.toml.lock/x.media.toml", "games/.hidden.media.toml"):
            path = self.recipes / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("invalid TOML!")
        rows = self.json_rows(self.recipes / "games", self.media, self.firmware)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(self.calls()), 2)

    def test_explicit_files_outside_repository_and_arbitrary_names(self):
        path = self.root / "custom.toml"
        path.write_text(MEDIA)
        self.responses[path.name] = {"plan": report(complete=True)}
        rows = self.json_rows(path)
        self.assertEqual(rows[0]["refs"], ["private/media/ibm-pc/demo:1.0-720k-r2"])
        self.assertEqual(len(self.calls()), 1)

    def test_missing_without_urls_lists_identity_and_dump_ids(self):
        item = copy.deepcopy(INPUT)
        item.update(urls=[], ids=[{"db": "mame", "set": "demo", "name": "rom.bin"}])
        self.responses[self.media.name]["plan"] = report(INPUT, item)
        row = self.json_rows(self.media)[0]
        self.assertEqual(row["status"], "missing")
        self.assertEqual(row["missing"][1]["ids"], item["ids"])
        code, stdout, _ = self.run_plan(self.media)
        self.assertEqual(code, 0)
        for fragment in ("missing", "GAME.ZIP", "940242 bytes", "SHA-256 " + SHA, "mame:demo/rom.bin"):
            self.assertIn(fragment, stdout)

    def test_file_set_lists_only_missing_files(self):
        item = copy.deepcopy(INPUT)
        item.update(form="files", urls=[], name="roms", files=[
            {"path": "found.bin", "size": 1, "sha256": SHA, "found": {"source": "store", "tags": []}},
            {"path": "missing.bin", "size": 2, "sha256": SHA, "found": None}])
        self.responses[self.media.name]["plan"] = report(item)
        row = self.json_rows(self.media)[0]
        self.assertEqual(row["status"], "missing")
        self.assertEqual([(f["name"], f["size"]) for f in row["missing"]], [("missing.bin", 2)])

    def test_found_outputs_and_members_make_inputs_not_needed(self):
        unused = copy.deepcopy(INPUT)
        unused.update(status="not-needed", urls=[], reason="the outputs made from it were found")
        optional = copy.deepcopy(INPUT)
        optional.update(optional=True, urls=[])
        response = report(unused, optional, complete=True)
        response["payloads"] = [{"kind": "medium", "id": "disk-1", "found": {"source": "store", "tags": ["demo:r1"]}}]
        self.responses[self.media.name]["plan"] = response
        row = self.json_rows(self.media)[0]
        self.assertEqual((row["status"], row["missing"]), ("ready", []))

    def test_found_input_needs_no_fetch(self):
        item = copy.deepcopy(INPUT)
        item.update(status="found", found={"source": "library", "library": "lib", "path": "other.zip", "member": "GAME.ZIP"})
        self.responses[self.media.name]["plan"] = report(item, complete=True)
        self.assertEqual(self.json_rows(self.media)[0]["status"], "ready")

    def test_invalid_check_has_first_findings_and_survey_continues(self):
        self.media.write_text("not TOML")
        self.responses[self.media.name] = {"plan": {"complete": False, "recipe": str(self.media),
                                                   "summary": "refused: 5 findings", "findings": [f"finding {i}" for i in range(5)]},
                                           "code": 1}
        rows = self.json_rows()
        self.assertEqual([r["status"] for r in rows], ["ready", "invalid"])
        self.assertEqual(rows[1]["refs"], [])
        self.assertEqual(rows[1]["findings"], ["finding 0", "finding 1", "finding 2"])
        self.assertIn("finding 0", self.run_plan(self.media)[1])

    def test_unemu_errors_and_unrecognized_output_are_invalid_rows(self):
        for answer in ({"code": 2, "stderr": "bad config\n", "raw": "", "plan": {}},
                       {"code": 0, "raw": "{}", "plan": {}},
                       {"code": 0, "raw": "[]", "plan": {}},
                       {"code": 0, "raw": "not JSON", "plan": {}},
                       {"plan": report(), "code": 1}):
            self.responses[self.media.name] = answer
            self.assertEqual(self.json_rows(self.media)[0]["status"], "invalid")

    def test_warnings_are_visible_in_json_and_table(self):
        response = report(INPUT)
        response.update(store_warnings=["unreadable store tag"], libraries=[
            {"path": "lib", "failed": [{"path": "file", "error": "denied"}], "unreadable": []}])
        self.responses[self.media.name]["plan"] = response
        row = self.json_rows(self.media)[0]
        self.assertEqual(row["warnings"], ["unreadable store tag", "lib: file: denied"])
        self.assertIn("lib: file: denied", self.run_plan(self.media)[1])

    def test_options_and_user_preferences_environment_are_preserved(self):
        store, library = self.root / "store", self.root / "a library"
        store.mkdir()
        library.mkdir()
        preferences = self.root / "preferences.toml"
        preferences.write_text('libraries = ["/registered/library"]\n')
        self.json_rows(self.media, "--library", library, "--library", self.root, "--store", store, "--config", preferences)
        call = self.calls()[0]
        self.assertEqual(call["argv"][4:], ["--library", str(library), "--library", str(self.root),
                                             "--store", str(store), "--config", str(preferences)])
        self.assertEqual(call["env"]["HOME"], self.env["HOME"])
        self.assertEqual(call["env"]["XDG_CONFIG_HOME"], self.env["XDG_CONFIG_HOME"])
        self.assertEqual((call["env"]["LC_ALL"], call["env"]["TZ"]), ("C", "UTC"))
        self.json_rows(self.media)
        self.assertNotIn("--config", self.calls()[0]["argv"])
        self.assertEqual(self.calls()[0]["env"]["HOME"], self.env["HOME"])

    def test_footer_commands_include_required_store_and_private_target(self):
        code, stdout, _ = self.run_plan(self.firmware, "--library", self.root)
        self.assertEqual(code, 0)
        build = shlex.split(next(line.partition(": ")[2] for line in stdout.splitlines() if line.startswith("Build a ready")))
        push = shlex.split(next(line.partition(": ")[2] for line in stdout.splitlines() if line.startswith("Push to your")))
        self.assertEqual(build[1:4], ["recipe", "build", str(self.firmware)])
        self.assertIn("--library", build)
        self.assertEqual(build[-2:], ["--store", "DIR"])
        self.assertEqual(push[1:5], ["image", "push", "unemu/firmware/ibm-pc/bios:r1", "private/firmware/ibm-pc/bios:r1"])
        self.assertEqual(push[-4:], ["--registry", "HOST", "--store", "DIR"])
        self.assertNotIn("--library", push)

    def test_footer_preserves_store_config_and_quotes_paths(self):
        store = self.root / "store space"
        store.mkdir()
        preferences = self.root / "config space.toml"
        preferences.write_text("libraries = []\n")
        _, stdout, _ = self.run_plan(self.firmware, "--store", store, "--config", preferences)
        for line in stdout.splitlines():
            if line.startswith(("Build a ready", "Push to your")):
                command = shlex.split(line.partition(": ")[2])
                self.assertEqual(command.count("--store"), 1)
                self.assertIn(str(store), command)
                self.assertIn(str(preferences), command)

    def test_cli_environment_variable_and_path_lookup(self):
        env = dict(self.env, UNEMU="unemu", PATH=str(self.root))
        code, _, stderr = self.run_plan("--json", self.media, env=env, default_cli=False)
        self.assertEqual((code, stderr), (0, ""))

    def test_usage_errors_do_not_invoke_unemu(self):
        for args in (("--fetch",), ("--push",), (self.root / "absent",),
                     ("--library", self.root / "absent"), ("--store", self.media),
                     ("--config", self.root), (self.root / "empty",)):
            (self.root / "empty").mkdir(exist_ok=True)
            code, _, _ = self.run_plan(*args)
            self.assertEqual(code, 2, args)
            self.assertEqual(self.calls(), [])
        self.assertEqual(self.run_plan(default_cli=False)[0], 2)

    def test_cannot_run_unemu_is_nonzero(self):
        self.cli.unlink()
        code, _, stderr = self.run_plan()
        self.assertEqual(code, 1)
        self.assertIn("cannot run", stderr)

    def test_isolated_script_entry_point_and_help(self):
        self.config.write_text(json.dumps(self.responses))
        done = subprocess.run([sys.executable, "-I", str(SCRIPT), "--cli", str(self.cli), "--json", str(self.media)],
                              capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)[0]["status"], "needs --fetch")
        help_text = self.run_plan("--help")[1]
        self.assertIn("index caches", help_text)
        self.assertIn("HOME", help_text)


if __name__ == "__main__":
    unittest.main()
