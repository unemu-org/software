# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests of lib/manifest.py: the example manifest is accepted, the tags are
derived as the naming scheme states, the warnings fire without refusing, and
each refusal rule fires.

    python3 -I tests/manifest_test.py
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import tomllib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE.parent / "lib" / "manifest.py"
EXAMPLE = HERE / "fixtures" / "example.toml"

_spec = importlib.util.spec_from_file_location("manifest", SCRIPT)
manifest = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = manifest
_spec.loader.exec_module(manifest)

HASH = "a" * 64
# The FreeDOS 1.4 Floppy Edition statement unemupc98's template code makes.
FREEDOS_LICENSE = (
    "GPL-1.0-only AND GPL-1.0-or-later AND GPL-2.0-only AND GPL-2.0-or-later AND "
    "GPL-3.0-or-later AND LGPL-2.1-or-later AND MIT AND BSD-2-Clause AND MPL-2.0 AND "
    "Watcom-1.0 AND Info-ZIP AND Artistic-1.0 AND GFDL-1.1-or-later AND Zlib AND "
    "LicenseRef-FreeDOS-other"
)


def example():
    with open(EXAMPLE, "rb") as handle:
        return tomllib.load(handle)


def changed(change):
    data = example()
    change(data)
    return data


def run(*args):
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def shareware(data):
    release = data["release"]
    release["license"] = "LicenseRef-example-vendor-terms"
    release["terms"] = "VENDOR.DOC"
    del release["source"]
    release["extra"] = [
        {"path": "extras/VENDOR.DOC", "sha256": HASH, "dos_name": "VENDOR.DOC", "kind": "document"},
        {"path": "extras/ORDER.FRM", "sha256": HASH, "dos_name": "ORDER.FRM", "kind": "other"},
    ]


class Accepted(unittest.TestCase):
    def test_example_manifest(self):
        checked = manifest.load(EXAMPLE)
        self.assertEqual(checked.release.repository, "unemu/media/example")
        self.assertEqual([s.tag for s in checked.sets], ["1.0", "1.0-720k"])
        self.assertEqual(checked.warnings, ())
        self.assertEqual(checked.release.series, "Example Series")
        self.assertEqual(checked.release.sort_title, "Example")
        self.assertEqual(
            [path for path, _ in manifest.pins(checked)],
            ["example-1440.img", "example-720.img", "LICENSE.TXT", "example-src.tar"],
        )

    def test_cli_check_and_pins(self):
        result = run("check", str(EXAMPLE))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("ok, 2 sets, 2 disks, 2 extras", result.stdout)
        self.assertIn("  unemu/media/example:1.0\n", result.stdout)
        self.assertIn("  unemu/media/example:1.0-720k\n", result.stdout)
        result = run("pins", str(EXAMPLE))
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 4)
        for line in lines:
            path, digest = line.split(" ")
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_derived_tags(self):
        def tags(change):
            return [s.tag for s in manifest.validate(changed(change)).sets]

        def edition(d):
            d["release"]["edition_slug"] = "livecd"
        self.assertEqual(tags(edition), ["1.0-livecd", "1.0-livecd-720k"])

        def revision(d):
            d["set"][1]["revision"] = 2
            d["set"][0]["revision"] = 1
        self.assertEqual(tags(revision), ["1.0", "1.0-720k-r2"])

        def both(d):
            d["release"]["edition_slug"] = "cd"
            d["set"][0]["revision"] = 3
        self.assertEqual(tags(both), ["1.0-cd-r3", "1.0-cd-720k"])

        def other_default(d):
            d["release"]["default_format"] = "720K"
        self.assertEqual(tags(other_default), ["1.0-1.44m", "1.0"])

        def every_format(d):
            d["release"]["default_format"] = "360K"
            d["set"] = [dict(d["set"][0], format=f, disk=[{"path": f"d{i}.img", "sha256": HASH, "label": "Disk 1"}])
                        for i, f in enumerate(("1.44M", "1.2M", "720K", "360K", "iso-2048"))]
        self.assertEqual(tags(every_format), ["1.0-1.44m", "1.0-1.2m", "1.0-720k", "1.0", "1.0-iso"])

        def letters(d):
            d["release"]["version"] = "1.0a"
        self.assertEqual(tags(letters), ["1.0a", "1.0a-720k"])

    def test_shareware_shape(self):
        # One LicenseRef- for the vendor's own terms needs no source archive,
        # also in parentheses: the parsed tree decides.
        for license in ("LicenseRef-example-vendor-terms", "(LicenseRef-x)", " ( ( LicenseRef-x.1 ) ) "[1:-1]):
            def change(d, license=license):
                shareware(d)
                d["release"]["license"] = license
            checked = manifest.validate(changed(change))
            self.assertIsNone(checked.release.source)

    def test_licence_expressions_with_source(self):
        for license in (
            "(GPL-2.0-only WITH Classpath-exception-2.0 OR MIT) AND LicenseRef-x",
            "MIT AND (Zlib OR BSD-3-Clause)",
            "MIT\tAND\u00a0Zlib",
            FREEDOS_LICENSE,
        ):
            manifest.validate(changed(lambda d, license=license: d["release"].update(license=license)))
        _, canonical = manifest.parse_license(FREEDOS_LICENSE, "license")
        self.assertEqual(canonical, FREEDOS_LICENSE)
        self.assertLessEqual(len(canonical), 256)
        _, canonical = manifest.parse_license("((MIT AND Zlib)  AND (ISC OR X11))", "license")
        self.assertEqual(canonical, "MIT AND Zlib AND (ISC OR X11)")

    def test_warnings_do_not_refuse(self):
        def change(d):
            d["release"]["labels"] = ["self-test"]
            d["set"][0]["title"] = "Example 1.0"
            d["set"][1]["title"] = "Example (self-test EDITION)"
        checked = manifest.validate(changed(change))
        warnings = "\n".join(checked.warnings)
        self.assertIn("no kind label", warnings)
        self.assertIn("no licence class label", warnings)
        self.assertIn("set[1].title: 'Example 1.0' holds the version '1.0'", warnings)
        self.assertIn("set[2].title: 'Example (self-test EDITION)' holds the edition", warnings)

        def word_parts(d):
            d["set"][0]["title"] = "Example 1.01 for 1.0x"
        self.assertEqual(manifest.validate(changed(word_parts)).warnings, ())

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "warned.toml"
            path.write_text(
                EXAMPLE.read_text(encoding="utf-8").replace('"utility", "open-source", ', ""),
                encoding="utf-8",
            )
            result = run("check", str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("warning: release.labels: no kind label", result.stderr)
            result = run("pins", str(path))
            self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_import_request(self):
        checked = manifest.load(EXAMPLE)
        request = manifest.import_request(checked, "1.0-720k", "out/example")
        self.assertEqual(request.tag, "unemu/media/example:1.0-720k")
        self.assertEqual(request.title.title, "Example")
        self.assertEqual(request.title.publishers, ("Example Publisher",))
        self.assertEqual(request.title.developers, ("Example Developer",))
        self.assertEqual(request.title.release_year, 1993)
        self.assertEqual(request.title.genres, ("utility",))
        self.assertEqual(request.title.platform, "DOS")
        self.assertEqual(request.title.version, "1.0")
        self.assertEqual(request.title.edition, "Self-test Edition")
        self.assertEqual(request.labels, ("utility", "open-source", "self-test"))
        self.assertEqual(
            request.media,
            (manifest.Source("out/example/example-720.img",
                             "Disk 1 of 1 - example installation (example-720.img)", "720K"),),
        )
        self.assertEqual(
            request.extras,
            (
                manifest.ExtraSource("out/example/LICENSE.TXT", "LICENSE.TXT", "document"),
                manifest.ExtraSource("out/example/example-src.tar", "EXSRC.TAR", "archive"),
            ),
        )
        self.assertTrue(request.compress)
        self.assertEqual(manifest.import_request(checked, "1.0", "out").tag, "unemu/media/example:1.0")
        self.assertEqual(
            manifest.open_statement(checked),
            manifest.OpenStatement("GPL-2.0-or-later", "LICENSE.TXT", "example-src.tar"),
        )
        with self.assertRaises(manifest.Refusal):
            manifest.import_request(checked, "1.0-1.2m", "out/example")

    def test_digest_pin(self):
        pin = "sha256:" + "0123456789abcdef" * 4
        checked = manifest.validate(changed(lambda d: d["set"][0].update(digest=pin)))
        self.assertEqual([s.digest for s in checked.sets], [pin, None])
        self.assertEqual([s.digest for s in manifest.load(EXAMPLE).sets], [None, None])

    def test_import_arguments(self):
        checked = manifest.load(EXAMPLE)
        request = manifest.import_request(checked, "1.0-720k", "/out/example")
        self.assertEqual(
            manifest.import_arguments(request, manifest.open_statement(checked)),
            [
                "image", "media", "import", "/out/example/example-720.img",
                "--title", "Example", "--tag", "unemu/media/example:1.0-720k",
                "--label", "1=Disk 1 of 1 - example installation (example-720.img)",
                "--format", "1=720K",
                "--extra", "/out/example/LICENSE.TXT:LICENSE.TXT",
                "--extra", "/out/example/example-src.tar:EXSRC.TAR",
                "--publisher", "Example Publisher", "--developer", "Example Developer",
                "--year", "1993", "--genre", "utility", "--platform", "DOS",
                "--description", checked.sets[1].description,
                "--version", "1.0", "--edition", "Self-test Edition",
                "--set-label", "utility", "--set-label", "open-source", "--set-label", "self-test",
                "--open", "--license", "GPL-2.0-or-later", "--license-text", "LICENSE.TXT",
                "--source", "example-src.tar",
            ],
        )

        # Every disk in installer order with its label and format; repeated
        # options in the manifest's order; no edition or source when absent.
        def change(d):
            shareware(d)
            del d["release"]["edition"]
            d["release"]["publishers"] = ["B", "A"]
            d["set"][0]["disk"].append({"path": "two.img", "sha256": HASH, "label": "Second"})
        checked = manifest.validate(changed(change))
        args = manifest.import_arguments(
            manifest.import_request(checked, "1.0", "/o"), manifest.open_statement(checked)
        )
        self.assertEqual(args[3:5], ["/o/example-1440.img", "/o/two.img"])
        self.assertEqual(
            args[9:17],
            ["--label", "1=Disk 1 of 1 - example installation (example-1440.img)", "--format", "1=1.44M",
             "--label", "2=Second", "--format", "2=1.44M"],
        )
        self.assertLess(args.index("B"), args.index("A"))
        self.assertNotIn("--edition", args)
        self.assertNotIn("--source", args)
        self.assertEqual(args[-5:], ["--open", "--license", "LicenseRef-example-vendor-terms",
                                     "--license-text", "VENDOR.DOC"])

    def test_cli_sets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "pinned.toml"
            pin = "sha256:" + "f" * 64
            text = EXAMPLE.read_text(encoding="utf-8").replace(
                'format = "1.44M"\n\n', f'format = "1.44M"\ndigest = "{pin}"\n\n', 1
            )
            path.write_text(text, encoding="utf-8")
            result = run("sets", str(path), "out/example")
        self.assertEqual(result.returncode, 0, result.stderr)
        described = json.loads(result.stdout)
        self.assertEqual([d["ref"] for d in described],
                         ["unemu/media/example:1.0", "unemu/media/example:1.0-720k"])
        self.assertEqual([d["digest"] for d in described], [pin, None])
        self.assertEqual({d["repository"] for d in described}, {"unemu/media/example"})
        self.assertEqual([d["tag"] for d in described], ["1.0", "1.0-720k"])
        self.assertEqual({d["license"] for d in described}, {"GPL-2.0-or-later"})
        checked = manifest.load(EXAMPLE)
        out_dir = str(pathlib.Path("out/example").resolve())
        self.assertEqual(
            described[1]["import"],
            manifest.import_arguments(
                manifest.import_request(checked, "1.0-720k", out_dir), manifest.open_statement(checked)
            ),
        )
        self.assertEqual(run("sets", str(EXAMPLE)).returncode, 2)


class Refused(unittest.TestCase):
    def refused(self, change, fragment):
        with self.assertRaises(manifest.Refusal) as caught:
            manifest.validate(changed(change))
        self.assertIn(fragment, str(caught.exception))

    def first_extra(self, data):
        return data["release"]["extra"][0]

    def first_disk(self, data):
        return data["set"][0]["disk"][0]

    def test_unknown_keys(self):
        self.refused(lambda d: d.update(extra=1), "manifest: unknown key 'extra'")
        self.refused(lambda d: d["release"].update(publisher="x"), "release: unknown key 'publisher'")
        self.refused(lambda d: d["release"].update(title_version="1.0"), "release: unknown key 'title_version'")
        self.refused(lambda d: self.first_extra(d).update(name="x"), "release.extra[1]: unknown key 'name'")
        self.refused(lambda d: d["set"][0].update(repository="example"), "set[1]: unknown key 'repository'")
        self.refused(lambda d: d["set"][0].update(tag="1.0"), "set[1]: unknown key 'tag'")
        self.refused(lambda d: self.first_disk(d).update(format="x"), "set[1].disk[1]: unknown key 'format'")

    def test_missing_keys(self):
        self.refused(lambda d: d.pop("set"), "manifest: missing required key 'set'")
        for key in ("work", "version", "default_format", "terms"):
            self.refused(lambda d, key=key: d["release"].pop(key), f"release: missing required key {key!r}")
        self.refused(lambda d: self.first_extra(d).pop("kind"), "release.extra[1]: missing required key 'kind'")
        self.refused(lambda d: d["set"][1].pop("description"), "set[2]: missing required key 'description'")
        self.refused(lambda d: self.first_disk(d).pop("sha256"), "set[1].disk[1]: missing required key 'sha256'")

    def test_work(self):
        for work in ("Example", "unemu/media/example", "-example", "example-", "exa mple", "", 7):
            self.refused(lambda d, work=work: d["release"].update(work=work), "release.work")
        self.refused(lambda d: d["release"].update(work="a" * 244), "is longer than 255 bytes")
        manifest.validate(changed(lambda d: d["release"].update(work="a" * 243)))

    def test_version(self):
        for version in ("1.0-beta", "V1.0", "v1.0", "1.0 ", "", ".1", "1_0", 1.0):
            self.refused(lambda d, version=version: d["release"].update(version=version), "release.version")
        self.refused(lambda d: d["release"].update(version="1" * 129), "is not a valid tag")
        for version in ("1.", "1.4.", "1a."):
            self.refused(lambda d, version=version: d["release"].update(version=version),
                         "ending with a letter or digit")
        for version in ("1..4", "1.0..2"):
            self.refused(lambda d, version=version: d["release"].update(version=version),
                         "has an empty part between dots")
        for version in ("1", "1.4", "1.0a", "2025.04.02"):
            manifest.validate(changed(lambda d, version=version: d["release"].update(version=version)))

    def test_edition_slug(self):
        for edition_slug in ("Live", "live-cd", "2cd", "", "live_cd"):
            self.refused(lambda d, s=edition_slug: d["release"].update(edition_slug=s), "release.edition_slug")
        self.refused(lambda d: d["release"].update(edition_slug="r2"), "reads as a revision")
        # "iso" is the iso-2048 format's slug: 1.0-iso would name two sets.
        self.refused(lambda d: d["release"].update(edition_slug="iso"), "reads as a format")

        def without_edition(d):
            d["release"]["edition_slug"] = "livecd"
            del d["release"]["edition"]
        self.refused(without_edition, "a tagged edition needs release.edition")

    def test_refs_across_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            text = EXAMPLE.read_text(encoding="utf-8")
            other_default = pathlib.Path(directory) / "other-default.toml"
            other_default.write_text(text.replace('default_format = "1.44M"', 'default_format = "720K"'),
                                     encoding="utf-8")
            # Each manifest alone is valid; together both give example:1.0.
            self.assertEqual(run("check", str(other_default)).returncode, 0)
            result = run("refs", str(EXAMPLE), str(other_default))
            self.assertEqual(result.returncode, 1)
            self.assertIn("unemu/media/example:1.0 is also", result.stderr)
            self.assertEqual(result.stdout, "")
            result = run("refs", str(EXAMPLE), str(EXAMPLE))
            self.assertEqual(result.returncode, 1)

            other_work = pathlib.Path(directory) / "other-work.toml"
            other_work.write_text(text.replace('work = "example"', 'work = "example2"'), encoding="utf-8")
            result = run("refs", str(EXAMPLE), str(other_work))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), [
                "unemu/media/example:1.0",
                "unemu/media/example:1.0-720k",
                "unemu/media/example2:1.0",
                "unemu/media/example2:1.0-720k",
            ])

            broken = pathlib.Path(directory) / "broken.toml"
            broken.write_text("[release\n", encoding="utf-8")
            result = run("refs", str(EXAMPLE), str(broken))
            self.assertEqual(result.returncode, 1)
            self.assertIn("not valid TOML", result.stderr)
        self.assertEqual(run("refs").returncode, 2)

    def test_licence_nesting(self):
        limit = manifest.MAX_LICENSE_NESTING
        for depth, accepted in ((limit, True), (limit + 1, False), (400, False)):
            license = "(" * depth + "MIT" + ")" * depth
            self.assertLessEqual(len(license), 1024)
            if accepted:
                _, canonical = manifest.parse_license(license, "license")
                self.assertEqual(canonical, "MIT")
            else:
                with self.assertRaises(manifest.Refusal) as caught:
                    manifest.parse_license(license, "license")
                self.assertIn("nests parentheses", str(caught.exception))
        self.refused(lambda d: d["release"].update(license="(" * 400 + "MIT" + ")" * 400),
                     "nests parentheses 400 deep")

    def test_revision_and_duplicate_tags(self):
        for revision in (0, -1, True, "2", 1.5):
            self.refused(lambda d, r=revision: d["set"][0].update(revision=r), "set[1].revision")
        self.refused(lambda d: d["set"][1].update(format="1.44M"),
                     "set[2]: the derived tag '1.0' is also set[1]'s")

        def same_revision(d):
            d["set"][1].update(format="1.44M", revision=2)
            d["set"][0].update(revision=2)
        self.refused(same_revision, "the derived tag '1.0-r2' is also set[1]'s")
        self.refused(lambda d: d["release"].update(default_format="2.88M"), "release.default_format")

    def test_copyleft_without_source(self):
        for license in ("GPL-2.0-or-later", "MIT AND LGPL-2.1-or-later", "(MPL-2.0)",
                        "AGPL-3.0-only", "GFDL-1.3-or-later", "EPL-2.0", "MIT OR GPL-3.0-only"):
            def change(d, license=license):
                d["release"]["license"] = license
                del d["release"]["source"]
            self.refused(change, "names copyleft licences")

    def test_expression_without_source(self):
        for license in ("MIT", "LicenseRef-a AND LicenseRef-b", "LicenseRef-a OR MIT"):
            def change(d, license=license):
                d["release"]["license"] = license
                del d["release"]["source"]
            self.refused(change, "is not one LicenseRef-")

    def test_licences_unemupc98_refuses(self):
        cases = {
            "GPL-2.0+": "unknown SPDX licence identifier 'GPL-2.0+'",
            "GPL-2.0": "unknown SPDX licence identifier 'GPL-2.0'",
            "gpl-2.0-only": "unknown SPDX licence identifier",
            "Foo-1.0": "unknown SPDX licence identifier",
            "MIT+": "unknown SPDX licence identifier 'MIT+'",
            "LicenseRef-": "unknown SPDX licence identifier 'LicenseRef-'",
            "LicenseRef-a_b": "unknown SPDX licence identifier",
            "MIT WITH Foo-exception": "unknown SPDX licence exception",
            "MIT WITH": "unknown SPDX licence exception",
            "GPL-2.0-or-later and MIT": "invalid SPDX licence expression",
            "GPL-2.0-or-later,MIT": "unknown SPDX licence identifier",
            "(MIT": "unbalanced parentheses",
            "MIT)": "invalid SPDX licence expression",
            "MIT AND": "incomplete licence expression",
            "AND MIT": "unknown SPDX licence identifier 'AND'",
            "": "incomplete licence expression",
            "MIT" + " " * 1014 + "AND Zlib": "too long",
            "LicenseRef-" + "a" * 250: "too long",
            "--MIT": "starts with '--'",
            " MIT": "starts or ends with white space",
        }
        for license, fragment in cases.items():
            self.refused(lambda d, license=license: d["release"].update(license=license),
                         fragment)
        # 1024 bytes of input are accepted; the canonical form decides then.
        manifest.validate(changed(lambda d: d["release"].update(license="MIT" + " " * 1013 + "AND Zlib")))
        self.assertEqual(len("MIT" + " " * 1013 + "AND Zlib"), 1024)

    def test_terms_and_source_name_extras(self):
        self.refused(lambda d: d["release"].update(terms="NOTICE.TXT"),
                     "release.terms: 'NOTICE.TXT' is not the file name of an extra")
        self.refused(lambda d: d["release"].update(source="other-src.tar"),
                     "release.source: 'other-src.tar' is not the file name of an extra")
        # The DOS name is not the file name the import matches.
        self.refused(lambda d: d["release"].update(source="EXSRC.TAR"),
                     "release.source: 'EXSRC.TAR' is not the file name of an extra")
        self.refused(lambda d: d["release"].update(source="LICENSE.TXT"), "is the terms text")
        self.refused(lambda d: d["release"].update(terms="--LICENSE.TXT"), "starts with '--'")

        def two_named(d):
            d["release"]["extra"].append(
                {"path": "docs/LICENSE.TXT", "sha256": HASH, "dos_name": "LICENSE2.TXT", "kind": "document"})
        self.refused(two_named, "names 2 extras")

        def not_an_archive(d):
            d["release"]["extra"].append(
                {"path": "NOTES.TXT", "sha256": HASH, "dos_name": "NOTES.TXT", "kind": "document"})
            d["release"]["source"] = "NOTES.TXT"
        self.refused(not_an_archive, "is a document extra, not an archive")

    def test_dos_names(self):
        self.refused(lambda d: self.first_extra(d).update(dos_name="EXSRC.TAR"), "is also the DOS name")
        for name in ("LICENSES.TEXT", "license.txt", "LONGFILENAME.TXT", "A.B.C", "CON.TXT",
                     "COM1", "LPT9.DOC", "WITH SPACE.TXT", ".TXT", "A.", ""):
            self.refused(lambda d, name=name: self.first_extra(d).update(dos_name=name),
                         "is not a canonical upper-case DOS 8.3 name")

    def test_hashes(self):
        for digest in ("0" * 63, "0" * 65, "A" * 64, "g" * 64, "", 0):
            self.refused(lambda d, digest=digest: self.first_disk(d).update(sha256=digest),
                         "set[1].disk[1].sha256")
            self.refused(lambda d, digest=digest: self.first_extra(d).update(sha256=digest),
                         "release.extra[1].sha256")

    def test_set_digest(self):
        for digest in ("0" * 64, "sha256:" + "0" * 63, "sha256:" + "0" * 65, "sha256:" + "A" * 64,
                       "SHA256:" + "0" * 64, "sha512:" + "0" * 64, " sha256:" + "0" * 64,
                       "sha256:" + "0" * 64 + "\n", "sha256:", "", 0, ["sha256:" + "0" * 64]):
            self.refused(lambda d, digest=digest: d["set"][1].update(digest=digest), "set[2].digest")

    def test_paths_inside_out(self):
        for path in ("../example-1440.img", "/example-1440.img", "a/../../b.img", "a//b.img",
                     "a/", "", ".hidden.img", "a/./b.img", "with space.img", "a\\b.img", "$HOME.img",
                     "-x.img", "--x.img", "a/+b.img"):
            self.refused(lambda d, path=path: self.first_disk(d).update(path=path),
                         "is not a path inside out/<recipe>/")
            self.refused(lambda d, path=path: self.first_extra(d).update(path=path),
                         "is not a path inside out/<recipe>/")

    def test_path_limits(self):
        def extra(name):
            return lambda d: d["release"]["extra"].append(
                {"path": f"docs/{name}", "sha256": HASH, "dos_name": "README.TXT", "kind": "document"})
        self.refused(extra("a" * 125 + ".txt"), "is longer than 128 characters")
        manifest.validate(changed(extra("a" * 124 + ".txt")))

        def case_only(d):
            d["set"][1]["disk"][0]["path"] = "EXAMPLE-1440.IMG"
        self.refused(case_only, "differ only in case")

        def case_extra(d):
            d["release"]["extra"].append(
                {"path": "license.txt", "sha256": HASH, "dos_name": "LICENSE2.TXT", "kind": "document"})
        self.refused(case_extra, "differ only in case")

    def test_kind_follows_extension(self):
        self.refused(lambda d: self.first_extra(d).update(kind="archive"),
                     "'archive' differs from 'document'")
        self.refused(lambda d: self.first_extra(d).update(kind="manual"), "is not one of")

    def test_same_path_two_hashes(self):
        def change(d):
            d["set"][1]["disk"][0].update(path="example-1440.img", sha256=HASH)
        self.refused(change, "is pinned with another SHA-256")

        # The same file pinned twice with one hash is consistent; it is
        # printed once.
        data = example()
        data["set"][1]["disk"][0].update(data["set"][0]["disk"][0])
        checked = manifest.validate(data)
        self.assertEqual([p for p, _ in manifest.pins(checked)].count("example-1440.img"), 1)

    def test_values_the_cli_trims_or_takes_for_options(self):
        # unemupc98's CLI trims these values and refuses one starting with
        # '--'; the manifest holds them as the import stores them.
        setters = {
            "release.publishers[1]": lambda d, v: d["release"].update(publishers=[v]),
            "release.developers[1]": lambda d, v: d["release"].update(developers=[v]),
            "release.genres[1]": lambda d, v: d["release"].update(genres=[v]),
            "release.edition": lambda d, v: d["release"].update(edition=v),
            "release.labels[1]": lambda d, v: d["release"].update(labels=[v]),
            "release.series": lambda d, v: d["release"].update(series=v),
            "release.sort_title": lambda d, v: d["release"].update(sort_title=v),
            "set[1].title": lambda d, v: d["set"][0].update(title=v),
            "set[1].description": lambda d, v: d["set"][0].update(description=v),
            "set[1].disk[1].label": lambda d, v: self.first_disk(d).update(label=v),
        }
        for where, setter in setters.items():
            for value, fragment in ((" example", "white space"), ("example ", "white space"),
                                    ("example\u00a0", "white space"), ("--example", "starts with '--'")):
                self.refused(lambda d, s=setter, v=value: s(d, v), f"{where}: ")
                self.refused(lambda d, s=setter, v=value: s(d, v), fragment)
        self.refused(lambda d: d["release"].update(platform=" DOS"), "release.platform")

    def test_set_and_disk_labels(self):
        # unemupc98's import gives set labels and disk labels one rule
        # (valid_text): not blank, at most 128 characters, no control
        # characters (U+0000 to U+001F and U+007F). Set labels are lower case.
        refused_text = ("x" * 129, "line\nbreak", "tab\there", "bell\x07", "delete\x7f", "", "   ")
        for label in refused_text:
            self.refused(lambda d, label=label: d["release"].update(labels=["utility", label]),
                         "release.labels[2]: expected 1 to 128 characters without control characters")
            self.refused(lambda d, label=label: self.first_disk(d).update(label=label),
                         "set[1].disk[1].label: expected 1 to 128 characters without control characters")
        self.refused(lambda d: d["release"].update(labels=["utility", 7]), "release.labels[2]: expected a string")
        self.refused(lambda d: d["release"].update(labels="utility"), "release.labels: expected a list")
        self.refused(lambda d: d["release"].update(labels=["utility", "utility"]),
                     "release.labels: duplicate entry")
        self.refused(lambda d: d["release"].update(labels=["utility", "Shareware"]),
                     "release.labels[2]: 'Shareware' is not lower case")
        # At the limit, and beyond ASCII, both are accepted.
        data = example()
        data["release"]["labels"] = ["utility", "open-source", "x" * 128, "na\u00efve caf\u00e9"]
        data["set"][0]["disk"][0]["label"] = "\u0395\u03bb\u03bb\u03b7\u03bd\u03b9\u03ba\u03ac " + "\u00e9" * 119
        checked = manifest.validate(data)
        self.assertEqual(checked.release.labels[2:], ("x" * 128, "na\u00efve caf\u00e9"))

    def test_release_fields(self):
        self.refused(lambda d: d["release"].update(year=0), "release.year")
        self.refused(lambda d: d["release"].update(year=True), "release.year")
        self.refused(lambda d: d["release"].update(year="1993"), "release.year")
        self.refused(lambda d: d["release"].update(platform="MS-DOS"), "release.platform")
        self.refused(lambda d: d["release"].update(publishers=[]), "release.publishers")
        self.refused(lambda d: d["release"].update(developers="Example"), "release.developers")
        self.refused(lambda d: d["release"].update(genres=["a", "a"]), "duplicate entry")
        self.refused(lambda d: d["release"].update(edition=""), "release.edition")
        self.refused(lambda d: d["release"].update(series="x" * 129), "release.series")
        self.refused(lambda d: d["release"].update(sort_title="a\nb"), "release.sort_title")
        self.refused(lambda d: d["release"].update(extra=[]), "release.extra")

    def test_set_fields(self):
        self.refused(lambda d: d["set"][0].update(format="2.88M"), "set[1].format")
        self.refused(lambda d: d["set"][0].update(title="x" * 129), "set[1].title")
        self.refused(lambda d: d["set"][0].update(description="two\nlines"), "set[1].description")
        self.refused(lambda d: d["set"][0].update(disk=[]), "set[1].disk")
        self.refused(lambda d: self.first_disk(d).update(label="  "), "set[1].disk[1].label")
        self.refused(lambda d: d.update(set=[]), "set: expected a non-empty array of tables")

    def test_cli_refusals(self):
        with tempfile.TemporaryDirectory() as directory:
            broken = pathlib.Path(directory) / "broken.toml"
            broken.write_text("[release\n", encoding="utf-8")
            result = run("check", str(broken))
            self.assertEqual(result.returncode, 1)
            self.assertIn("not valid TOML", result.stderr)

            text = EXAMPLE.read_text(encoding="utf-8").replace('kind = "archive"', 'kind = "other"')
            changed_file = pathlib.Path(directory) / "changed.toml"
            changed_file.write_text(text, encoding="utf-8")
            for command in ("check", "pins"):
                result = run(command, str(changed_file))
                self.assertEqual(result.returncode, 1)
                self.assertIn("release.extra[2].kind", result.stderr)
                self.assertEqual(result.stdout, "")

            result = run("check", str(pathlib.Path(directory) / "missing.toml"))
            self.assertEqual(result.returncode, 1)
            self.assertIn("cannot read", result.stderr)
        self.assertEqual(run().returncode, 2)
        self.assertEqual(run("lint", str(EXAMPLE)).returncode, 2)


if __name__ == "__main__":
    unittest.main()
