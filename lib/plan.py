# SPDX-License-Identifier: AGPL-3.0-or-later
"""Survey local recipe plans with unemu (Python 3.11+, standard library only).

Only `recipe plan FILE --json` is invoked. The user's HOME and preferences
are preserved; unemu may update its index caches in an existing store.
It builds, downloads and changes no image, and contacts no registry.
"""

import argparse
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUFFIXES = (".media.toml", ".firmware.toml")


def select(paths, root):
    """Find recipes without parsing them: invalid ones need survey rows too.

    Explicit files may have any name; folders omit work, hidden and lock files.
    Private recipes outside this repository are welcome.
    """
    chosen = set()
    for operand in paths or [str(root / "recipes")]:
        path = pathlib.Path(operand).absolute()
        if path.is_file():
            chosen.add(path)
        elif path.is_dir():
            found = []
            for directory, folders, files in os.walk(path):
                folders[:] = sorted(f for f in folders if not f.startswith(".") and not f.endswith(".lock"))
                found.extend(pathlib.Path(directory) / f for f in files
                             if not f.startswith(".") and f.endswith(SUFFIXES))
            if not found:
                raise ValueError(f"{operand}: no recipes there")
            chosen.update(found)
        else:
            raise ValueError(f"{operand}: not a file or folder")
    return sorted(chosen)


def display_path(path):
    try:
        return path.relative_to(pathlib.Path.cwd()).as_posix()
    except ValueError:
        return str(path)


def output_refs(path):
    """Plan in unemu 0.0.4 supplies the repository name, but no release or tag.

    Read only the identity here; unemu performs the recipe check, including
    private namespaces. Do not use lib/recipes.py's publication policy.
    """
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
        identity = document.get("media", document.get("firmware", {}))
        name, revision = identity["name"], identity["revision"]
        release = identity.get("release")
        tag = f"{release}-r{revision}" if release else f"r{revision}"
        return [f"{name}:{tag}"]
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError, AttributeError):
        return []


def plan_recipe(cli, path, options, env):
    """The single per-recipe call; replace here when unemu gains batch plans
    (unemu recipe survey, upc-5pw.113.26 in unemupc)."""
    return subprocess.run([cli, "recipe", "plan", str(path), "--json", *options],
                          stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                          errors="replace", env=env, check=False)


def missing_inputs(inputs):
    """Only needed inputs count; found payloads/members can replace archives.

    A file set reports each missing file's identity, with the parent's dump ids.
    Optional attachments do not prevent building the artifact.
    """
    missing = []
    for item in inputs:
        if item["status"] != "missing" or item["optional"]:
            continue
        files = [item] if item["form"] == "file" else [f for f in item["files"] if f["found"] is None]
        for file in files:
            missing.append({
                "input": item["id"],
                "name": item["name"] if item["form"] == "file" else file["path"],
                "size": file["size"], "sha256": file["sha256"],
                "ids": item["ids"], "urls": item["urls"],
            })
    return missing


def row_for(path, done):
    row = {"recipe": display_path(path), "refs": output_refs(path), "status": "invalid",
           "missing": [], "findings": [], "summary": "", "warnings": []}
    try:
        plan = json.loads(done.stdout)
        row["summary"] = plan["summary"]
        if plan.get("findings"):
            row["findings"] = plan["findings"][:3]
        elif done.returncode in (0, 1) and type(plan["complete"]) is bool:
            row["missing"] = missing_inputs(plan["inputs"])
            if plan["complete"]:
                row["status"] = "ready"
            elif row["missing"]:
                row["status"] = "needs --fetch" if all(i["urls"] for i in row["missing"]) else "missing"
            else:
                row["findings"] = ["incomplete plan without missing required inputs"]
            row["warnings"].extend(plan.get("store_warnings", []))
            for library in plan.get("libraries", []):
                for key in ("failed", "unreadable"):
                    for failure in library.get(key, []):
                        row["warnings"].append(f"{library['path']}: {failure['path']}: {failure['error']}")
    except (ValueError, KeyError, TypeError, AttributeError):
        row["status"] = "invalid"
        row["findings"] = ["unrecognized unemu plan JSON"]
    if row["status"] == "invalid" and not row["findings"]:
        row["findings"] = [f"unemu recipe plan exited with status {done.returncode}"]
    if done.stderr.strip():
        row["warnings"].extend(done.stderr.strip().splitlines()[:3])
    return row


def table(rows, cli, options):
    cells = [("RECIPE", "OUTPUT REF(S)", "STATUS", "DETAIL")]
    for row in rows:
        detail = []
        if row["status"] == "missing":
            for item in row["missing"]:
                if not item["urls"]:
                    ids = ", ".join(f"{i['db']}:{i['set']}/{i['name']}" for i in item["ids"])
                    detail.append(f"{item['name']}: {item['size']} bytes, SHA-256 {item['sha256']}"
                                  + (f", {ids}" if ids else ""))
        elif row["status"] == "invalid":
            detail.extend(row["findings"])
        detail.extend(str(w) for w in row["warnings"])
        # Keep one row per recipe even when diagnostics contain newlines.
        cells.append(tuple(" ".join(str(c).split()) for c in
                           (row["recipe"], ", ".join(row["refs"]) or "-", row["status"], "; ".join(detail))))
    widths = [max(len(row[i]) for row in cells) for i in range(3)]
    for row in cells:
        print("  ".join(row[i].ljust(widths[i]) for i in range(3)) + ("  " + row[3] if row[3] else ""))
    ready = next((row for row in rows if row["status"] == "ready" and row["refs"]), None)
    recipe, ref = (ready["recipe"], ready["refs"][0]) if ready else ("FILE", "REF")
    build = [cli, "recipe", "build", recipe, *options]
    push_options = []
    # image push requires --store; recipe build also accepts it.
    if "--store" not in options:
        build += ["--store", "DIR"]
        push_options += ["--store", "DIR"]
    for flag in ("--store", "--config"):
        if flag in options:
            push_options += [flag, options[options.index(flag) + 1]]
    target = "private/" + ref.removeprefix("unemu/").removeprefix("private/") if ready else "private/NAME:TAG"
    print("\nBuild a ready recipe: " + shlex.join(build))
    print("For needs --fetch, add --fetch to the build command.")
    print("Push to your own registry: " + shlex.join(
        [cli, "image", "push", ref, target, "--registry", "HOST", *push_options]))


def main(argv=None, root=ROOT, environ=None):
    env = dict(os.environ if environ is None else environ)
    parser = argparse.ArgumentParser(
        prog="lib/plan.py", description="Survey recipes using local files and your unemu preferences.",
        epilog="Runs only unemu recipe plan: no builds, downloads, image changes or registry access. "
               "unemu may update index caches in an existing store. HOME and preferences are preserved.")
    parser.add_argument("paths", nargs="*", metavar="FILE|FOLDER", help="default: all recipes under recipes/")
    parser.add_argument("--cli", default=env.get("UNEMU"), metavar="PATH", help="unemu binary (default: $UNEMU)")
    parser.add_argument("--library", action="append", default=[], metavar="DIR", help="extra library (repeatable)")
    parser.add_argument("--store", metavar="DIR", help="existing store (default: unemu's default store)")
    parser.add_argument("--config", metavar="FILE", help="unemu preferences (default: the user's own)")
    parser.add_argument("--json", action="store_true", help="print rows as JSON, without the footer")
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
        if not args.cli:
            parser.error("--cli PATH (or $UNEMU) is required")
        paths = select(args.paths, pathlib.Path(root))
        options = []
        for flag, values in (("--library", args.library), ("--store", [args.store] if args.store else []),
                             ("--config", [args.config] if args.config else [])):
            for value in values:
                path = pathlib.Path(value)
                if not (path.is_file() if flag == "--config" else path.is_dir()):
                    parser.error(f"{flag} {value}: not a {'file' if flag == '--config' else 'folder'}")
                options += [flag, value]
    except ValueError as error:
        parser.print_usage(sys.stderr)
        print(f"{parser.prog}: error: {error}", file=sys.stderr)
        return 2
    except SystemExit as stop:
        return stop.code
    cli = args.cli
    if os.sep in cli:
        cli = os.path.abspath(cli)
    else:
        cli = shutil.which(cli, path=env.get("PATH", os.defpath)) or cli
    env.update(LC_ALL="C", TZ="UTC")
    rows = []
    try:
        for path in paths:
            rows.append(row_for(path, plan_recipe(cli, path, options, env)))
    except OSError as error:
        print(f"cannot run {cli}: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        table(rows, cli, options)
    return 0


if __name__ == "__main__":
    sys.exit(main())
