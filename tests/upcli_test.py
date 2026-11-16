# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests of lib/upcli.py, offline: the pin format, the commit line, and
fetch against fake release archives served from file://, whose upcli is a
small Python script that logs how it was run. curl is a wrapper that logs
its arguments and hands a file:// download to the real curl (the
devShell's); any other URL fails at once. Nothing here reaches the network.

    python3 -I tests/upcli_test.py
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import stat
import sys
import tarfile
import tempfile
import unittest
import unittest.mock

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SCRIPT = ROOT / "lib" / "upcli.py"

_spec = importlib.util.spec_from_file_location("upcli", SCRIPT)
upcli = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = upcli
_spec.loader.exec_module(upcli)

COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba9876543210fedcba98"
TAG, VERSION = "v9.9.9", "9.9.9"
TRIPLE = "x86_64-unknown-linux-gnu"
LINUX = ("Linux", "x86_64")
DIRECTORY = f"unemupc-{VERSION}-{TRIPLE}"
MEMBER = f"{DIRECTORY}/upcli"
FILE = f"unemupc-{VERSION}-{TRIPLE}.tar.gz"

# The fake upcli: it records its arguments, environment and working
# directory, then answers --version with LINES on stdout and ERRORS on
# stderr, and exits with STATUS.
FAKE_UPCLI = """#!@PYTHON@ -I
import json, os, sys
with open(@LOG@, "w") as handle:
    json.dump({"argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd()}, handle)
if sys.argv[1:] != ["--version"]:
    sys.exit(2)
print(@LINES@)
print(@ERRORS@, file=sys.stderr)
sys.exit(@STATUS@)
"""

# The curl on PATH in these tests: it logs its arguments; a file:// URL
# goes to the real curl, anything else fails as an unreachable host does.
FAKE_CURL = """#!@PYTHON@ -I
import json, os, sys
with open(@LOG@, "a") as handle:
    handle.write(json.dumps(sys.argv[1:]) + "\\n")
if not sys.argv[-1].startswith("file://"):
    print("curl: (7) the tests reach no network", file=sys.stderr)
    sys.exit(7)
os.execv(@CURL@, [@CURL@, *sys.argv[1:]])
"""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def pin_text(sha="a" * 64, size=1, upcli_sha="b" * 64, upcli_size=1, commit=COMMIT, triple=TRIPLE):
    return (f'commit = "{commit}"\ntag = "{TAG}"\nversion = "{VERSION}"\n\n'
            f'[archive.{triple}]\nfile = "unemupc-{VERSION}-{triple}.tar.gz"\n'
            f'sha256 = "{sha}"\nsize = {size}\nupcli_sha256 = "{upcli_sha}"\nupcli_size = {upcli_size}\n')


def write_archive(path, members):
    """A gzip tar archive at PATH of MEMBERS, each (name, kind, value): a
    file with the bytes VALUE, a dir, or a symlink or hardlink to VALUE."""
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as tar:
        for name, kind, value in members:
            info = tarfile.TarInfo(name)
            info.mode = 0o755
            if kind == "file":
                info.size = len(value)
                tar.addfile(info, io.BytesIO(value))
                continue
            info.type = {"dir": tarfile.DIRTYPE, "symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE}[kind]
            info.linkname = value or ""
            tar.addfile(info)


class Release:
    """A throwaway repository root, a release served from file://, a fake
    upcli that logs its run and a curl that logs its arguments."""

    def __init__(self, directory):
        self.base = pathlib.Path(directory).resolve()
        self.root = self.base / "repo"
        self.root.mkdir()
        self.served = self.base / "releases"
        (self.served / TAG).mkdir(parents=True)
        self.cache = self.root / "cache" / "upcli"
        self.log = self.base / "upcli-run.json"
        self.curl_log = self.base / "curl-calls.jsonl"
        self.bin = self.base / "fake-bin"
        self.bin.mkdir()
        real_curl = shutil.which("curl")
        if real_curl is None:
            raise unittest.SkipTest("no curl on PATH: run the tests inside nix develop")
        curl = (FAKE_CURL.replace("@PYTHON@", sys.executable).replace("@LOG@", repr(str(self.curl_log)))
                .replace("@CURL@", repr(real_curl)))
        (self.bin / "curl").write_text(curl, encoding="utf-8")
        (self.bin / "curl").chmod(0o755)

    def upcli(self, lines=f"upcli {VERSION}\ncommit: {COMMIT}", status=0, errors=""):
        return (FAKE_UPCLI.replace("@PYTHON@", sys.executable).replace("@LOG@", repr(str(self.log)))
                .replace("@LINES@", repr(lines)).replace("@ERRORS@", repr(errors))
                .replace("@STATUS@", str(status))).encode()

    def members(self, upcli=None):
        """The members of a release archive, in its layout."""
        return [
            (DIRECTORY, "dir", None),
            (MEMBER, "file", upcli if upcli is not None else self.upcli()),
            (f"{DIRECTORY}/unemupc", "file", b"the emulator"),
            (f"{DIRECTORY}/LICENSE", "file", b"licence"),
            (f"{DIRECTORY}/README.md", "file", b"readme"),
            (f"{DIRECTORY}/THIRD-PARTY-LICENSES.md", "file", b"third-party licences"),
            (f"{DIRECTORY}/notices", "dir", None),
            (f"{DIRECTORY}/notices/LICENSES", "dir", None),
            (f"{DIRECTORY}/notices/LICENSES/MIT.txt", "file", b"MIT"),
        ]

    def publish(self, members=None, data=None, **pins):
        """Serve an archive of MEMBERS (or the bytes DATA) and pin it: the
        archive as served, the binary as its first file member named MEMBER
        (or the default fake upcli). PINS (sha, size, upcli_sha, upcli_size)
        replace those pins. The archive's bytes."""
        members = self.members() if members is None else members
        path = self.served / TAG / FILE
        if data is None:
            write_archive(path, members)
        else:
            path.write_bytes(data)
        data = path.read_bytes()
        body = next((v for n, k, v in members if n == MEMBER and k == "file"), self.upcli())
        values = {"sha": sha256(data), "size": len(data), "upcli_sha": sha256(body), "upcli_size": len(body)}
        values.update(pins)
        (self.root / upcli.PIN_FILE).write_text(pin_text(**values), encoding="ascii")
        return data

    def fetch(self, out="bin", *args, host=LINUX, base=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        environ = {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
                   "HOME": str(self.base / "caller-home")}
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = upcli.main(["fetch", *args, str(self.base / out)], root=self.root, environ=environ,
                              base=base or self.served.as_uri(), host=host)
        return code, stdout.getvalue(), stderr.getvalue()

    def ran(self):
        """How the fake upcli was run, or None when it was not."""
        if not self.log.exists():
            return None
        return json.loads(self.log.read_text())

    def curl_calls(self):
        if not self.curl_log.exists():
            return []
        return [json.loads(line) for line in self.curl_log.read_text().splitlines()]

    def cached(self):
        return sorted(p.name for p in self.cache.iterdir()) if self.cache.exists() else []


class Case(unittest.TestCase):
    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.release = Release(self._directory.name)

    def tearDown(self):
        self._directory.cleanup()

    def refused(self, fragment, *args, out="bin", ran=False, **kwargs):
        """A fetch that is refused with FRAGMENT in its reason, leaves no
        OUT and nothing in the cache but the archive, and runs the fake
        upcli only when RAN."""
        code, stdout, stderr = self.release.fetch(out, *args, **kwargs)
        self.assertEqual(code, 1, stderr)
        self.assertEqual(stdout, "")
        self.assertIn("refused: ", stderr)
        self.assertIn(fragment, stderr)
        self.assertFalse(os.path.lexists(self.release.base / out), stderr)
        self.assertEqual([n for n in self.release.cached() if n != FILE], [])
        self.assertEqual(self.release.ran() is not None, ran, stderr)
        return stderr

    def uncache(self):
        """Remove the cached archive, so the next fetch downloads."""
        os.unlink(self.release.cache / FILE)


class Pin(Case):
    def read(self, text):
        path = self.release.root / upcli.PIN_FILE
        path.write_text(text, encoding="utf-8")
        return upcli.read_pin(path)

    def test_the_repository_pin(self):
        pin = upcli.read_pin(ROOT / upcli.PIN_FILE)
        self.assertEqual(sorted(pin.archives), sorted(upcli.TRIPLES))
        self.assertTrue(upcli.RELEASES.startswith("https://"))
        for triple in pin.archives:
            self.assertEqual(pin.url(triple), f"{upcli.RELEASES}/{pin.tag}/unemupc-{pin.version}-{triple}.tar.gz")

    def test_a_valid_pin(self):
        pin = self.read("# a comment\n" + pin_text(sha="c" * 64, size=123, upcli_sha="d" * 64, upcli_size=45))
        self.assertEqual((pin.commit, pin.tag, pin.version), (COMMIT, TAG, VERSION))
        self.assertEqual(pin.archives, {TRIPLE: upcli.Archive(TRIPLE, FILE, "c" * 64, 123, "d" * 64, 45)})
        self.assertEqual(pin.member(TRIPLE), MEMBER)
        both = pin_text() + pin_text(triple="aarch64-apple-darwin").split("\n\n", 1)[1]
        self.assertEqual(sorted(self.read(both).archives), sorted(upcli.TRIPLES))

    def test_refused(self):
        good = pin_text()
        head = good.split("\n\n")[0] + "\n"
        for text, fragment in (
            ("", "missing 'commit'"),
            (good.replace(f'commit = "{COMMIT}"\n', ""), "missing 'commit'"),
            (good.replace(f'tag = "{TAG}"\n', ""), "missing 'tag'"),
            (good.replace(f'version = "{VERSION}"\n', ""), "missing 'version'"),
            (head, "missing 'archive'"),
            (good.replace("commit =", "release = \"x\"\ncommit ="), "unknown key 'release'"),
            (good.replace("size = 1\n", "size = 1\nurl = \"https://example.invalid/\"\n", 1), "unknown key 'url'"),
            (good.replace("sha256 = ", "sha512 = ", 1), "unknown key 'sha512'"),
            (good.replace('sha256 = "' + "a" * 64 + '"\n', ""), "missing 'sha256'"),
            (good.replace("\nsize = 1\n", "\n"), "missing 'size'"),
            (good.replace('upcli_sha256 = "' + "b" * 64 + '"\n', ""), "missing 'upcli_sha256'"),
            (good.replace("upcli_size = 1\n", ""), "missing 'upcli_size'"),
            (good.replace(f"[archive.{TRIPLE}]", "[archive.aarch64-unknown-linux-gnu]")
             .replace(f"-{TRIPLE}.tar.gz", "-aarch64-unknown-linux-gnu.tar.gz"), "not a supported target triple"),
            (head + "archive = {}\n", "one table per target triple"),
            (head + 'archive = "x"\n', "one table per target triple"),
            (head + f'archive = {{ {TRIPLE} = "x" }}\n', "not a table"),
            (good.replace(f'file = "{FILE}"', 'file = "upcli.tar.gz"'), "file must be"),
            (good.replace(f'file = "{FILE}"', f'file = "../{FILE}"'), "file must be"),
            (good.replace(COMMIT, COMMIT[:12]), "commit must be"),
            (good.replace(COMMIT, COMMIT.upper()), "commit must be"),
            (good.replace(f'"{COMMIT}"', "[]"), "commit must be"),
            (good.replace(f'"{TAG}"', '"releases/v9"'), "tag must be"),
            (good.replace(f'"{TAG}"', '""'), "tag must be"),
            (good.replace(f'version = "{VERSION}"', 'version = "9 9"'), "version must be"),
            (good.replace("a" * 64, "A" * 64), "sha256 must be"),
            (good.replace("a" * 64, "a" * 63), "sha256 must be"),
            (good.replace("b" * 64, "B" * 64), "upcli_sha256 must be"),
            (good.replace("b" * 64, "b" * 65), "upcli_sha256 must be"),
            (good.replace("\nsize = 1", "\nsize = 0"), "size must be"),
            (good.replace("\nsize = 1", "\nsize = -1"), "size must be"),
            (good.replace("\nsize = 1", "\nsize = true"), "size must be"),
            (good.replace("\nsize = 1", '\nsize = "1"'), "size must be"),
            (good.replace("\nsize = 1", "\nsize = 1.0"), "size must be"),
            (good.replace("\nsize = 1", f"\nsize = {upcli.MAX_ARCHIVE + 1}"), "size must be"),
            (good.replace("upcli_size = 1", "upcli_size = 0"), "upcli_size must be"),
            (good.replace("upcli_size = 1", "upcli_size = false"), "upcli_size must be"),
            (good.replace("upcli_size = 1", f"upcli_size = {upcli.MAX_BINARY + 1}"), "upcli_size must be"),
            # TOML itself refuses a key or table given twice.
            (f'commit = "{COMMIT}"\n' + good, "not valid TOML"),
            (good + "size = 2\n", "not valid TOML"),
            (good + good.split("\n\n")[1], "not valid TOML"),
            (COMMIT + "\n", "not valid TOML"),
        ):
            with self.assertRaises(upcli.Refusal, msg=text) as caught:
                self.read(text)
            self.assertIn(fragment, str(caught.exception), text)
            self.assertTrue(str(caught.exception).startswith(f"{upcli.PIN_FILE}: "), caught.exception)
        with self.assertRaises(upcli.Refusal) as caught:
            upcli.read_pin(self.release.root / "missing.toml")
        self.assertIn("cannot read", str(caught.exception))


class CommitLine(unittest.TestCase):
    def test_the_one_commit_line_is_read(self):
        text = (f"upcli 0.0.2\nsource: https://github.com/unemu-org/unemupc/tree/v0.0.2\ncommit: {COMMIT}\n"
                f"\nthird-party components:\n\nExample (upstream commit {OTHER})\nsource: x/{OTHER}\n")
        self.assertEqual(upcli.reported_commit(text), COMMIT)
        self.assertEqual(upcli.reported_commit(f"commit: {COMMIT}"), COMMIT)
        # Lines end at '\n' only: no other line boundary str.splitlines
        # knows starts a commit line.
        for text in ("", "upcli 0.0.1\nsource: https://github.com/unemu-org/unemupc/tree/v0.0.1\n",
                     f"upcli 0.1.0 ({COMMIT})", f" commit: {COMMIT}", f"Commit: {COMMIT}",
                     f"upstream commit: {COMMIT}", f"\ufeffcommit: {COMMIT}",
                     *(f"x{separator}commit: {COMMIT}" for separator in
                       ("\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"))):
            self.assertIsNone(upcli.reported_commit(text), repr(text))
        for text, fragment in (
            (f"commit: {COMMIT}\ncommit: {COMMIT}", "2 commit lines"),
            (f"commit: {COMMIT}\ncommit: {OTHER}", "2 commit lines"),
            (f"commit: {COMMIT.upper()}", "the commit line"),
            (f"commit: {COMMIT[:12]}", "the commit line"),
            (f"commit: {COMMIT}0", "the commit line"),
            (f"commit:{COMMIT}", "the commit line"),
            (f"commit: {COMMIT} ", "the commit line"),
            (f"commit: {COMMIT}\r", "the commit line"),
            (f"commit: {COMMIT}\x0c", "the commit line"),
            ("commit: ", "the commit line"),
        ):
            with self.assertRaises(upcli.Refusal) as caught:
                upcli.reported_commit(text)
            self.assertIn(fragment, str(caught.exception), repr(text))


class CheckCli(Case):
    """check_cli on its own, with the fake upcli at a path of its own."""

    def check(self, body, env=None):
        cli = self.release.base / "upcli"
        cli.write_bytes(body)
        cli.chmod(0o755)
        tmp = self.release.base / "tmp"
        tmp.mkdir(exist_ok=True)
        env = env or {"PATH": os.environ.get("PATH", os.defpath), "TMPDIR": str(tmp), "LC_ALL": "C", "TZ": "UTC"}
        return upcli.check_cli(str(cli), COMMIT, env)

    def test_lines_end_at_newline_only(self):
        self.assertEqual(self.check(self.release.upcli(f"upcli {VERSION}\ncommit: {COMMIT}")), COMMIT)
        self.assertEqual(self.check(self.release.upcli(f"upcli {VERSION}\r\ncommit: {COMMIT}\r")), COMMIT)
        for lines in (f"upcli {VERSION}\rcommit: {COMMIT}", f"upcli {VERSION}\x0ccommit: {COMMIT}"):
            with self.assertRaises(upcli.Refusal) as caught:
                self.check(self.release.upcli(lines))
            self.assertIn("prints no commit line", str(caught.exception), repr(lines))
        with self.assertRaises(upcli.Refusal) as caught:
            self.check(self.release.upcli(f"commit: {COMMIT}\r\rx"))
        self.assertIn("prints the commit line", str(caught.exception))

    def test_only_utf8(self):
        for stream in (b"stdout", b"stderr"):
            body = self.release.upcli().replace(b"print(", b"sys.%s.buffer.write(b'\\xff\\n'); print(" % stream, 1)
            with self.assertRaises(upcli.Refusal, msg=stream) as caught:
                self.check(body)
            self.assertIn("prints something other than UTF-8", str(caught.exception))

    def test_a_scratch_directory_it_cannot_make(self):
        missing = str(self.release.base / "no-such-tmp")
        with self.assertRaises(upcli.Refusal) as caught:
            self.check(self.release.upcli(), {"PATH": os.defpath, "TMPDIR": missing})
        self.assertIn(f"cannot make a scratch directory for {self.release.base / 'upcli'} --version in {missing}",
                      str(caught.exception))
        self.assertNotIn("cannot run", str(caught.exception))
        self.assertIsNone(self.release.ran())
        with self.assertRaises(upcli.Refusal) as caught:
            upcli.check_cli(str(self.release.base / "no-such-upcli"), COMMIT, {"PATH": os.defpath})
        self.assertIn("cannot run", str(caught.exception))


class Host(unittest.TestCase):
    def test_hosts(self):
        self.assertEqual(upcli.host_triple("Linux", "x86_64"), "x86_64-unknown-linux-gnu")
        self.assertEqual(upcli.host_triple("Darwin", "arm64"), "aarch64-apple-darwin")
        for system, machine in (("Linux", "aarch64"), ("Darwin", "x86_64"), ("Windows", "AMD64"),
                                ("FreeBSD", "amd64"), ("Linux", "i686")):
            with self.assertRaises(upcli.Refusal) as caught:
                upcli.host_triple(system, machine)
            self.assertIn("no archive for this host", str(caught.exception))


class Fetch(Case):
    def test_good_path(self):
        data = self.release.publish()
        code, stdout, stderr = self.release.fetch()
        self.assertEqual(code, 0, stderr)
        binary = self.release.base / "bin" / "upcli"
        self.assertEqual(stdout, f"{binary}\n")
        self.assertEqual(sorted(os.listdir(binary.parent)), ["upcli"])
        self.assertEqual(stat.S_IMODE(os.lstat(binary).st_mode), 0o755)
        self.assertTrue(stat.S_ISREG(os.lstat(binary).st_mode))
        self.assertEqual(binary.read_bytes(), self.release.upcli())
        # The archive is in the cache, as served, and nothing else is.
        self.assertEqual(self.release.cached(), [FILE])
        self.assertEqual((self.release.cache / FILE).read_bytes(), data)
        self.assertIn(f"download {self.release.served.as_uri()}/{TAG}/{FILE}", stderr)
        self.assertIn(f"{MEMBER}: {len(self.release.upcli())} bytes, SHA-256 {sha256(self.release.upcli())}, "
                      "as pinned", stderr)
        self.assertIn(f"reports commit {COMMIT}, as pinned", stderr)
        # It ran once, for --version, in an environment and a directory of
        # its own, both removed afterwards.
        run = self.release.ran()
        self.assertEqual(run["argv"], ["--version"])
        env = {k: v for k, v in run["env"].items() if not k.startswith("__CF_")}
        self.assertEqual(sorted(env), ["HOME", "LC_ALL", "PATH", "TMPDIR", "TZ"])
        self.assertEqual((env["LC_ALL"], env["TZ"]), ("C", "UTC"))
        self.assertNotEqual(env["HOME"], str(self.release.base / "caller-home"))
        self.assertFalse(os.path.exists(env["HOME"]) or os.path.exists(env["TMPDIR"]))
        self.assertTrue(os.path.realpath(run["cwd"]).startswith(os.path.realpath(env["TMPDIR"]) + os.sep),
                        run["cwd"])
        self.assertNotEqual(os.path.realpath(run["cwd"]), os.path.realpath(os.getcwd()))
        # The host's archive by default; another directory is a fresh one.
        code, _, stderr = self.release.fetch("again", "--triple", TRIPLE, host=("Darwin", "arm64"))
        self.assertEqual(code, 0, stderr)

    def test_the_curl_call(self):
        data = self.release.publish()
        self.assertEqual(self.release.fetch()[0], 0)
        [argv] = self.release.curl_calls()
        url = f"{self.release.served.as_uri()}/{TAG}/{FILE}"
        self.assertEqual(argv[0], "-q")
        self.assertEqual(argv[-1], url)

        def option(argv, name):
            self.assertEqual(argv.count(name), 1, argv)
            return argv[argv.index(name) + 1]

        self.assertEqual(option(argv, "--max-filesize"), str(len(data)))
        self.assertEqual(option(argv, "--proto-redir"), "=https")
        # file:// only for the tests' own base.
        self.assertEqual(option(argv, "--proto"), "=https,file")
        for flag in ("--fail", "--location"):
            self.assertIn(flag, argv)
        # The download went to a temporary directory, removed afterwards.
        part = option(argv, "-o")
        self.assertFalse(os.path.lexists(os.path.dirname(part)), part)
        self.assertFalse(part.startswith(str(self.release.cache)), part)
        # The real releases: HTTPS only (this curl fails at once).
        self.uncache()
        self.release.log.unlink()
        stderr = self.refused("download failed: ", out="real", base=upcli.RELEASES)
        self.assertIn("the tests reach no network", stderr)
        argv = self.release.curl_calls()[-1]
        self.assertEqual(argv[-1], f"{upcli.RELEASES}/{TAG}/{FILE}")
        self.assertEqual(option(argv, "--proto"), "=https")
        self.assertEqual(option(argv, "--proto-redir"), "=https")
        self.assertEqual(self.release.cached(), [])

    def test_a_cached_archive_is_checked_again(self):
        self.release.publish()
        self.assertEqual(self.release.fetch()[0], 0)
        # The cached copy is used, never the network...
        (self.release.served / TAG / FILE).unlink()
        code, stdout, stderr = self.release.fetch("again")
        self.assertEqual(code, 0, stderr)
        self.assertIn("cached ", stderr)
        self.assertNotIn("download ", stderr)
        self.assertEqual(len(self.release.curl_calls()), 1)
        # ...and checked every time: a changed copy is refused, not replaced.
        cached = self.release.cache / FILE
        valid = cached.read_bytes()
        with open(cached, "ab") as handle:
            handle.write(b"x")
        changed = cached.read_bytes()
        self.release.log.unlink()
        self.refused("the cached archive", out="third")
        self.assertEqual(cached.read_bytes(), changed)
        self.refused("Remove it and run again", out="third")
        # A link in its place is refused, even to a valid archive...
        elsewhere = self.release.base / "elsewhere.tar.gz"
        elsewhere.write_bytes(valid)
        cached.unlink()
        cached.symlink_to(elsewhere)
        self.refused("the cached archive", out="third")
        # ...and so is anything but a regular file.
        cached.unlink()
        cached.mkdir()
        self.refused("not a regular file", out="third")

    def test_the_checked_bytes_are_the_extracted_ones(self):
        """A cached archive rewritten in place right after its check is
        not read again: the checked bytes are extracted, and the other
        archive's upcli never runs."""
        self.release.publish()
        self.assertEqual(self.release.fetch()[0], 0)
        evil_ran = self.release.base / "evil-ran"
        evil = self.release.upcli(f"upcli {VERSION}\ncommit: {COMMIT}").replace(
            b"import json, os, sys\n", f"import json, os, sys\nopen({str(evil_ran)!r}, 'w').close()\n".encode())
        write_archive(self.release.base / "evil.tar.gz", self.release.members(evil))
        cached = self.release.cache / FILE
        original = upcli.check_bytes
        swapped = []

        def check_then_swap(data, size, sha):
            original(data, size, sha)
            if not swapped:
                with open(cached, "r+b") as handle:  # the same file, rewritten
                    handle.truncate(0)
                    handle.write((self.release.base / "evil.tar.gz").read_bytes())
                swapped.append(True)

        with unittest.mock.patch.object(upcli, "check_bytes", check_then_swap):
            code, _, stderr = self.release.fetch("again")
        self.assertEqual(swapped, [True])
        self.assertEqual(code, 0, stderr)
        self.assertEqual((self.release.base / "again" / "upcli").read_bytes(), self.release.upcli())
        self.assertFalse(evil_ran.exists())

    def test_the_member_must_have_its_pinned_bytes(self):
        body = self.release.upcli()
        for pins, fragment in (
            ({"upcli_sha": "0" * 64}, f"{len(body)} bytes, SHA-256 {sha256(body)}; upcli.toml pins"),
            ({"upcli_size": len(body) + 1}, f"is {len(body)} bytes; upcli.toml pins {len(body) + 1}"),
            ({"upcli_size": len(body) - 1}, f"is {len(body)} bytes; upcli.toml pins {len(body) - 1}"),
        ):
            self.release.publish(**pins)
            self.refused(fragment)
            self.uncache()
        # Another upcli in the archive than the pinned one.
        self.release.publish(self.release.members(body + b"# changed\n"), upcli_sha=sha256(body),
                             upcli_size=len(body) + 10)
        self.refused("SHA-256")

    def test_wrong_hash(self):
        self.release.publish(sha="0" * 64)
        self.refused("wrong file from file://")
        self.assertEqual(self.release.cached(), [])

    def test_wrong_size(self):
        size = len(self.release.publish())
        for wrong in (size - 1, size + 1):
            self.release.publish(size=wrong)
            self.refused("refused: ")
            self.assertEqual(self.release.cached(), [])

    def test_extra_members_are_ignored(self):
        members = self.release.members() + [
            ("upcli", "file", b"another upcli"),
            ("other/upcli", "file", b"another upcli"),
            (f"./{MEMBER}", "file", b"another spelling"),
            (f"{DIRECTORY}/upcli.d/upcli", "file", b"nested"),
            (f"{DIRECTORY}/link", "symlink", "/etc/passwd"),
            (f"{DIRECTORY}/hard", "hardlink", f"{DIRECTORY}/unemupc"),
        ]
        self.release.publish(members)
        code, _, stderr = self.release.fetch()
        self.assertEqual(code, 0, stderr)
        self.assertEqual(sorted(os.listdir(self.release.base / "bin")), ["upcli"])
        self.assertEqual((self.release.base / "bin" / "upcli").read_bytes(), self.release.upcli())
        self.assertEqual(sorted(os.listdir(self.release.base)),
                         ["bin", "curl-calls.jsonl", "fake-bin", "releases", "repo", "upcli-run.json"])

    def test_a_missing_member(self):
        for members in (
            [m for m in self.release.members() if m[0] != MEMBER],
            [(f"unemupc-9.9.8-{TRIPLE}/upcli", "file", self.release.upcli())],
            [(f"unemupc-{VERSION}-aarch64-apple-darwin/upcli", "file", self.release.upcli())],
            [("upcli", "file", self.release.upcli())],
            [(f"./{MEMBER}", "file", self.release.upcli())],
        ):
            self.release.publish(members)
            self.refused(f"the archive has no member {MEMBER}")
            self.uncache()

    def test_the_member_must_be_one_regular_file(self):
        body = self.release.upcli()
        unemupc = (f"{DIRECTORY}/unemupc", "file", body)
        for members, fragment in (
            ([unemupc, (MEMBER, "symlink", "unemupc")], "is a symbolic link"),
            ([unemupc, (MEMBER, "symlink", "/etc/passwd")], "is a symbolic link"),
            ([unemupc, (MEMBER, "hardlink", f"{DIRECTORY}/unemupc")], "is a hard link"),
            ([(MEMBER, "dir", None)], "not a regular file"),
            ([(MEMBER, "file", body), (MEMBER, "file", body)], "2 members named"),
        ):
            self.release.publish(members)
            self.refused(fragment)
            self.uncache()
        self.release.publish([(MEMBER, "file", b"")], upcli_sha=sha256(body), upcli_size=len(body))
        self.refused("is 0 bytes")

    def test_path_traversal(self):
        for name in ("../evil", "/tmp/evil", f"{DIRECTORY}/../../evil", f"{DIRECTORY}/notices/../../../evil", ".."):
            self.release.publish(self.release.members() + [(name, "file", b"evil")])
            self.refused("leaves its directory")
            self.assertFalse(os.path.lexists(self.release.base / "evil"))
            self.uncache()

    def test_not_an_archive(self):
        self.release.publish(data=b"not a gzip tar archive, though pinned")
        self.refused("not a readable gzip tar archive")

    def test_the_commit_line_must_be_the_pinned_one(self):
        for lines, errors, status, fragment in (
            (f"upcli {VERSION}\ncommit: {OTHER}", "", 0, f"reports commit {OTHER}; upcli.toml pins {COMMIT}"),
            (f"upcli {VERSION}\nsource: https://github.com/unemu-org/unemupc, version {VERSION}", "", 0,
             "prints no commit line"),
            (f"upcli {VERSION} ({COMMIT})", "", 0, "prints no commit line"),
            # Only stdout is read.
            (f"upcli {VERSION}", f"commit: {COMMIT}", 0, "prints no commit line"),
            # A lone carriage return ends no line, read as bytes.
            (f"upcli {VERSION}\rcommit: {COMMIT}", "", 0, "prints no commit line"),
            (f"commit: {COMMIT}\ncommit: {COMMIT}", "", 0, "2 commit lines"),
            (f"commit: {COMMIT}", "", 1, "--version failed"),
        ):
            self.release.publish(self.release.members(self.release.upcli(lines, status, errors)))
            self.refused(fragment, ran=True)
            self.release.log.unlink()
            self.uncache()
        # A commit line on stdout counts, whatever stderr says, and CRLF
        # ends a line as '\n' does.
        for lines, errors in ((f"upcli {VERSION}\ncommit: {COMMIT}", f"commit: {OTHER}"),
                              (f"upcli {VERSION}\r\ncommit: {COMMIT}\r", "")):
            self.release.publish(self.release.members(self.release.upcli(lines, errors=errors)))
            code, _, stderr = self.release.fetch()
            self.assertEqual(code, 0, (lines, stderr))
            shutil.rmtree(self.release.base / "bin")
            self.uncache()

    def test_a_failed_cache_store(self):
        """The cache store fails at its rename: the fetch fails, leaves no
        temporary file in the cache and takes nothing out of the archive."""
        self.release.publish()
        with unittest.mock.patch.object(upcli.os, "replace", side_effect=OSError(28, "No space left on device")):
            code, stdout, stderr = self.release.fetch()
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("failed: [Errno 28] No space left on device", stderr)
        self.assertEqual(os.listdir(self.release.cache), [])
        self.assertFalse(os.path.lexists(self.release.base / "bin"))
        self.assertIsNone(self.release.ran())

    def test_the_stored_copy_is_not_read_back(self):
        """A download is extracted from the checked bytes in memory, never
        from the copy written into the cache."""
        self.release.publish()
        evil_ran = self.release.base / "evil-ran"
        evil = self.release.upcli().replace(
            b"import json, os, sys\n", f"import json, os, sys\nopen({str(evil_ran)!r}, 'w').close()\n".encode())
        write_archive(self.release.base / "evil.tar.gz", self.release.members(evil))
        original = upcli.store

        def store_other_bytes(data, dest):
            original((self.release.base / "evil.tar.gz").read_bytes(), dest)

        with unittest.mock.patch.object(upcli, "store", store_other_bytes):
            code, _, stderr = self.release.fetch()
        self.assertEqual(code, 0, stderr)
        self.assertEqual((self.release.base / "bin" / "upcli").read_bytes(), self.release.upcli())
        self.assertFalse(evil_ran.exists())

    def test_the_archive_read_is_bounded(self):
        """A cached file larger than the pin is refused after reading one
        byte more than the pinned size, never read whole."""
        size = len(self.release.publish())
        self.assertEqual(self.release.fetch()[0], 0)
        with open(self.release.cache / FILE, "ab") as handle:
            handle.write(b"\0" * (1 << 20))
        self.release.log.unlink()
        self.refused(f"the cached archive {self.release.cache / FILE}: {size + 1} bytes, SHA-256 ",
                     out="again")

    def test_an_unsupported_triple(self):
        self.release.publish()
        self.refused("unemupc releases have no archive for 'aarch64-unknown-linux-gnu'",
                     "--triple", "aarch64-unknown-linux-gnu")
        self.refused("upcli.toml pins no archive for aarch64-apple-darwin", "--triple", "aarch64-apple-darwin")
        self.refused("no archive for this host (Linux aarch64)", host=("Linux", "aarch64"))
        self.refused("no archive for this host (Darwin x86_64)", host=("Darwin", "x86_64"))
        # Nothing was downloaded.
        self.assertEqual(self.release.cached(), [])
        self.assertEqual(self.release.curl_calls(), [])

    def test_the_directory_must_be_new(self):
        self.release.publish()
        (self.release.base / "bin").mkdir()
        code, _, stderr = self.release.fetch()
        self.assertEqual(code, 1)
        self.assertIn("exists; give a new directory", stderr)
        self.assertEqual(os.listdir(self.release.base / "bin"), [])
        self.assertEqual(self.release.cached(), [])
        self.assertIsNone(self.release.ran())

    def test_usage(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(upcli.main([], root=self.release.root), 2)
            self.assertEqual(upcli.main(["fetch"], root=self.release.root), 2)
            self.assertEqual(upcli.main(["install", "x"], root=self.release.root), 2)


if __name__ == "__main__":
    unittest.main()
