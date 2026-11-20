# SPDX-License-Identifier: AGPL-3.0-or-later
"""The pinned unemupc CLI, upcli: its pin (upcli.toml), the commit a upcli
binary reports, and the pinned release binary.

    python3 -I lib/upcli.py fetch [--triple TRIPLE] [--cache DIR] OUT_DIR

    OUT_DIR          a new directory for the binary, OUT_DIR/upcli; it must
                     not exist yet
    --triple TRIPLE  the release archive to take it from (default: the
                     host's: x86_64-unknown-linux-gnu on Linux x86_64,
                     aarch64-apple-darwin on macOS arm64; any other host or
                     triple is refused). The binary must run on this host:
                     its --version is checked.
    --cache DIR      where the downloaded archive is kept (default:
                     cache/upcli/)

upcli.toml pins one unemupc release: the commit its tag points to, the tag,
the version, and for each platform the SHA-256 and size of the release
archive and of the upcli inside it. lib/publish.py reads it with read_pin
and checks the binary it is given with check_cli, as fetch does here.

fetch is the one place this repository runs a downloaded program, under the
one exception AGENTS.md states to "never execute anything from a download":

 1. The archive upcli.toml pins for TRIPLE, unemupc-<version>-<triple>.tar.gz,
    is read into memory once, and those bytes are checked against the pinned
    size and SHA-256 (never against the release's SHA256SUMS) and are the
    bytes everything below uses. A cached copy is read and checked that way
    on every run, and a wrong one is refused, never replaced. Otherwise the
    archive is downloaded from the release over HTTPS (curl, with the flags
    and limits of lib/source.sh's fetch) into a temporary directory, and
    only once checked are its bytes written into the cache.
 2. Those bytes give up one member only, unemupc-<version>-<triple>/upcli:
    it must be the archive's one member of that name, a regular file (no
    symbolic or hard link), and its bytes must have the size and SHA-256
    upcli.toml pins for the binary. An archive that names any member by an
    absolute path or with a '..' component is refused whole. tarfile never
    writes a file itself here: the checked bytes are written into
    OUT_DIR/upcli, mode 0755.
 3. OUT_DIR/upcli --version runs with an environment of its own (PATH, a
    fresh HOME and TMPDIR, LC_ALL=C, TZ=UTC) in a scratch directory; the
    commit line of its stdout must name the pinned commit (check_cli). Until
    then, and if anything fails, nothing else runs the binary, and a failure
    removes OUT_DIR again.

The binary's path goes to stdout, progress to stderr. Exit status: 0 when
OUT_DIR/upcli is the pinned upcli, 1 on a refusal, 2 for a usage error.

Standard library only (tomllib, Python 3.11 or later), and curl for the
download. Run it with -I.
"""

import argparse
import dataclasses
import hashlib
import io
import os
import pathlib
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

PIN_FILE = "upcli.toml"
# Where a release's assets are: <RELEASES>/<tag>/<file>.
RELEASES = "https://github.com/unemu-org/unemupc/releases/download"
# The hosts a unemupc release has an archive for, by (platform.system(),
# platform.machine()), and the archive's target triple.
HOSTS = {
    ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("Darwin", "arm64"): "aarch64-apple-darwin",
}
TRIPLES = tuple(HOSTS.values())
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
# A tag or a version: a plain name, safe in a URL path and a file name.
NAME = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}")
# The line a release build's `upcli --version` names its commit on
# (unemupc crates/platforms/src/lib.rs, licence_notice and commit_line, from
# UNEMUPC_SOURCE_COMMIT at compile time): every line of stdout that
# starts with COMMIT_PREFIX counts, and it must be exactly COMMIT_LINE.
COMMIT_PREFIX = "commit:"
COMMIT_LINE = re.compile(r"commit: ([0-9a-f]{40})")
PIN_KEYS = ("commit", "tag", "version", "archive")
ARCHIVE_KEYS = ("file", "sha256", "size", "upcli_sha256", "upcli_size")
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_BINARY = 256 * 1024 * 1024
# The download's limits, as lib/source.sh's fetch sets them.
CONNECT_TIMEOUT = 20
FETCH_MAX_TIME = 1800
# How long `upcli --version` may take.
VERSION_TIMEOUT = 60


class Refusal(Exception):
    """Why the pin, an archive or a upcli binary is refused."""


@dataclasses.dataclass(frozen=True)
class Archive:
    """One platform's release archive, as upcli.toml pins it, and the upcli
    inside it."""

    triple: str
    file: str
    sha256: str
    size: int
    upcli_sha256: str
    upcli_size: int


@dataclasses.dataclass(frozen=True)
class Pin:
    """The unemupc release upcli.toml pins."""

    commit: str
    tag: str
    version: str
    archives: dict

    def member(self, triple):
        """The archive member that is upcli."""
        return f"unemupc-{self.version}-{triple}/upcli"

    def url(self, triple, base=RELEASES):
        return f"{base}/{self.tag}/{self.archives[triple].file}"


def log(text):
    print(text, file=sys.stderr, flush=True)


# --- the pin ------------------------------------------------------------------


def _keys(table, wanted, where):
    unknown = sorted(set(table) - set(wanted))
    if unknown:
        raise Refusal(f"{where}: unknown key {unknown[0]!r}")
    for key in wanted:
        if key not in table:
            raise Refusal(f"{where}: missing {key!r}")


def _string(table, key, pattern, what, where):
    value = table[key]
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise Refusal(f"{where}: {key} must be {what}, not {value!r}")
    return value


def _size(table, key, limit, where):
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= limit:
        raise Refusal(f"{where}: {key} must be a whole number of bytes, 1 to {limit}, not {value!r}")
    return value


def parse_pin(data, where=PIN_FILE):
    """A Pin from the parsed TOML DATA: exactly the keys commit (40
    lower-case hex digits), tag and version (plain names) and archive, a
    table of one or more tables named by a supported target triple, each
    with exactly file (unemupc-<version>-<triple>.tar.gz), sha256 and size
    (the archive's: 64 lower-case hex digits, 1 to MAX_ARCHIVE bytes), and
    upcli_sha256 and upcli_size (its upcli member's, 1 to MAX_BINARY bytes).
    Anything else is a Refusal; TOML itself refuses a key given twice."""
    if not isinstance(data, dict):
        raise Refusal(f"{where}: not a table")
    _keys(data, PIN_KEYS, where)
    commit = _string(data, "commit", COMMIT, "a full commit, 40 lower-case hex digits", where)
    tag = _string(data, "tag", NAME, "a plain tag name", where)
    version = _string(data, "version", NAME, "a plain version", where)
    tables = data["archive"]
    if not isinstance(tables, dict) or not tables:
        raise Refusal(f"{where}: archive must hold one table per target triple")
    archives = {}
    for triple, table in tables.items():
        here = f"{where}: archive.{triple}"
        if triple not in TRIPLES:
            raise Refusal(f"{here}: not a supported target triple ({', '.join(TRIPLES)})")
        if not isinstance(table, dict):
            raise Refusal(f"{here}: not a table")
        _keys(table, ARCHIVE_KEYS, here)
        name = f"unemupc-{version}-{triple}.tar.gz"
        if table["file"] != name:
            raise Refusal(f"{here}: file must be {name!r}, not {table['file']!r}")
        archives[triple] = Archive(
            triple, name,
            _string(table, "sha256", SHA256, "64 lower-case hex digits", here),
            _size(table, "size", MAX_ARCHIVE, here),
            _string(table, "upcli_sha256", SHA256, "64 lower-case hex digits", here),
            _size(table, "upcli_size", MAX_BINARY, here),
        )
    return Pin(commit, tag, version, archives)


def read_pin(path):
    """The Pin in the file PATH (parse_pin)."""
    where = os.path.basename(path)
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as error:
        raise Refusal(f"{where}: cannot read: {error.strerror or error}") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise Refusal(f"{where}: not valid TOML: {error}") from None
    return parse_pin(data, where)


# --- the commit a binary reports ----------------------------------------------


def reported_commit(text):
    """The commit on the commit line of TEXT, what `upcli --version`
    printed on stdout, or None when no line starts with 'commit:'. Lines
    end at '\\n' only (check_cli has made each CRLF '\\n' already; a lone
    '\\r' ends no line). That line must be the only one, and exactly
    'commit: ' and 40 lower-case hex digits: a Refusal otherwise. Nothing
    else in TEXT is read: the third-party notices name commits of their
    own."""
    lines = [line for line in text.split("\n") if line.startswith(COMMIT_PREFIX)]
    if not lines:
        return None
    if len(lines) > 1:
        raise Refusal(f"upcli --version prints {len(lines)} commit lines; a upcli prints one")
    found = COMMIT_LINE.fullmatch(lines[0])
    if not found:
        raise Refusal(f"upcli --version prints the commit line {lines[0]!r}; expected 'commit: ' and "
                      "40 lower-case hex digits")
    return found.group(1)


def output_text(data, cli):
    """DATA, what CLI printed, as strict UTF-8 with each CRLF made '\\n';
    a lone '\\r' stays as it is, so it never ends a line."""
    try:
        return data.decode("utf-8", errors="strict").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise Refusal(f"{cli} --version prints something other than UTF-8") from None


def check_cli(cli, commit, env, timeout=VERSION_TIMEOUT):
    """Run `CLI --version` in ENV (and nothing else), in a new scratch
    directory inside ENV's TMPDIR; the commit line of its stdout must name
    COMMIT (its stderr is not read). The output is read as bytes and
    decoded by output_text, so the lines reported_commit sees end at '\\n'
    only. A Refusal when the scratch directory cannot be made, or CLI
    cannot run, fails, takes longer than TIMEOUT seconds, prints anything
    but UTF-8, or names no commit or another one. A upcli without a commit
    line is refused too: a non-release build prints one only when built
    with UNEMUPC_SOURCE_COMMIT set."""
    tmpdir = env.get("TMPDIR")
    try:
        scratch = tempfile.TemporaryDirectory(prefix="upcli-cwd.", dir=tmpdir, ignore_cleanup_errors=True)
    except OSError as error:
        raise Refusal(f"cannot make a scratch directory for {cli} --version in "
                      f"{tmpdir or 'the temporary directory'}: {error.strerror or error}") from None
    with scratch as cwd:
        try:
            done = subprocess.run([cli, "--version"], capture_output=True, stdin=subprocess.DEVNULL, env=env,
                                  cwd=cwd, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise Refusal(f"{cli} --version did not finish within {timeout} seconds") from None
        except OSError as error:
            raise Refusal(f"cannot run {cli}: {error.strerror or error}") from None
    stdout, stderr = output_text(done.stdout, cli), output_text(done.stderr, cli)
    if done.returncode != 0:
        lines = [line for line in (stderr + stdout).splitlines() if line.strip()]
        reason = lines[-1] if lines else f"exit status {done.returncode}"
        raise Refusal(f"{cli} --version failed: {reason}")
    reported = reported_commit(stdout)
    if reported is None:
        raise Refusal(
            f"{cli} --version prints no commit line, so it cannot be checked against {PIN_FILE}: use "
            "the pinned release's upcli (lib/upcli.py fetch), or build it at the pinned commit with "
            "UNEMUPC_SOURCE_COMMIT set to that commit"
        )
    if reported != commit:
        raise Refusal(f"{cli} --version reports commit {reported}; {PIN_FILE} pins {commit}")
    return reported


# --- fetch --------------------------------------------------------------------


def host_triple(system=None, machine=None):
    """The target triple of the release archive for this host (or for
    SYSTEM and MACHINE, as platform.system() and platform.machine() name
    them); a Refusal for any host HOSTS does not list."""
    system = platform.system() if system is None else system
    machine = platform.machine() if machine is None else machine
    triple = HOSTS.get((system, machine))
    if triple is None:
        raise Refusal(f"unemupc releases have no archive for this host ({system} {machine}); "
                      f"they have {', '.join(TRIPLES)}. Build upcli from source at the pinned commit instead")
    return triple


def check_bytes(data, size, sha256):
    """DATA has SIZE bytes and the SHA-256 SHA256; a Refusal saying what it
    has instead otherwise."""
    digest = hashlib.sha256(data).hexdigest()
    if (len(data), digest) != (size, sha256):
        raise Refusal(f"{len(data)} bytes, SHA-256 {digest}; {PIN_FILE} pins {size} bytes, SHA-256 {sha256}")


def read_checked(path, archive):
    """The bytes of PATH, a regular file (not a link) that holds the pinned
    archive, read once (at most one byte more than its pinned size) and
    checked in memory, so the checked bytes are the ones used; a Refusal
    saying what is wrong otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise Refusal(f"cannot open it: {error.strerror or error}") from None
    try:
        regular = stat.S_ISREG(os.fstat(fd).st_mode)
    except OSError:
        regular = False
    if not regular:
        os.close(fd)
        raise Refusal("not a regular file")
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(archive.size + 1)
    check_bytes(data, archive.size, archive.sha256)
    return data


def curl_command(url, archive, part, base):
    """The curl call that downloads URL into PART. -q comes first so no
    ~/.curlrc applies; the limits let a stalled or oversized answer give
    way; only HTTPS is followed, and file:// is allowed only for a BASE
    other than RELEASES (the tests')."""
    return [
        "curl", "-q", "--fail", "--silent", "--show-error", "--location", "--retry", "2",
        "--connect-timeout", str(CONNECT_TIMEOUT), "--max-time", str(FETCH_MAX_TIME),
        "--max-filesize", str(archive.size),
        "--proto", "=https" if base == RELEASES else "=https,file", "--proto-redir", "=https",
        "-o", part, url,
    ]


def download(url, archive, environ, base):
    """The checked bytes of the archive at URL, downloaded into a temporary
    directory that is removed afterwards."""
    with tempfile.TemporaryDirectory(prefix="upcli-download.") as scratch:
        part = os.path.join(scratch, archive.file)
        try:
            done = subprocess.run(curl_command(url, archive, part, base), stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, encoding="utf-8",
                                  errors="replace", env=environ, check=False)
        except OSError as error:
            raise Refusal(f"cannot run curl: {error.strerror or error} (nix develop provides it)") from None
        if done.returncode != 0:
            lines = [line for line in done.stderr.splitlines() if line.strip()]
            reason = lines[-1] if lines else f"curl exit status {done.returncode}"
            raise Refusal(f"download failed: {url}: {reason}")
        try:
            return read_checked(part, archive)
        except Refusal as refusal:
            raise Refusal(f"wrong file from {url}: {refusal}") from None


def store(data, dest):
    """Write DATA, checked bytes, into the cache at DEST: under a temporary
    name in its directory, synced to disk, then renamed into place, mode
    0644. A failure leaves no temporary file behind."""
    directory = os.path.dirname(dest)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, dest)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def unsafe(name):
    """Whether an archive member's NAME is absolute or has a '..'
    component."""
    return name.startswith("/") or ".." in name.split("/")


def member_bytes(data, member, archive):
    """The bytes of the member MEMBER of DATA, a gzip tar archive: the
    archive's one member of that name, a regular file with the size and
    SHA-256 the pin gives the binary; no member of the archive may be
    absolute or have a '..' component. Nothing is written."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            members = tar.getmembers()
            for info in members:
                if unsafe(info.name):
                    raise Refusal(f"the archive has a member that leaves its directory: {info.name!r}")
            found = [info for info in members if info.name == member]
            if not found:
                raise Refusal(f"the archive has no member {member}")
            if len(found) > 1:
                raise Refusal(f"the archive has {len(found)} members named {member}")
            info = found[0]
            if info.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
                kind = ("a symbolic link" if info.issym() else "a hard link" if info.islnk()
                        else f"of tar type {info.type!r}")
                raise Refusal(f"the member {member} is {kind}, not a regular file")
            if info.size != archive.upcli_size:
                raise Refusal(f"the member {member} is {info.size} bytes; {PIN_FILE} pins {archive.upcli_size}")
            body = tar.extractfile(info).read(info.size + 1)
    except (tarfile.TarError, EOFError, OSError, zlib.error) as error:
        raise Refusal(f"not a readable gzip tar archive: {error}") from None
    try:
        check_bytes(body, archive.upcli_size, archive.upcli_sha256)
    except Refusal as refusal:
        raise Refusal(f"the member {member}: {refusal}") from None
    return body


def write_binary(body, dest):
    """Write BODY into the new file DEST, then make it mode 0755."""
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(body)
        os.fchmod(handle.fileno(), 0o755)


def fetch(pin, triple, out_dir, cache_dir, environ, base=RELEASES):
    """The pinned upcli for TRIPLE in the new directory OUT_DIR (see the
    module's description); its path. BASE is where the releases are (the
    tests serve them from file://)."""
    if triple not in TRIPLES:
        raise Refusal(f"unemupc releases have no archive for {triple!r}; they have {', '.join(TRIPLES)}")
    if triple not in pin.archives:
        raise Refusal(f"{PIN_FILE} pins no archive for {triple}")
    archive = pin.archives[triple]
    out_dir = os.path.abspath(out_dir)
    if os.path.lexists(out_dir):
        raise Refusal(f"{out_dir} exists; give a new directory")
    cached = os.path.join(os.path.abspath(cache_dir), archive.file)
    log(f"{PIN_FILE}: unemupc {pin.tag}, commit {pin.commit}")
    if os.path.lexists(cached):
        log(f"cached {cached}")
        try:
            data = read_checked(cached, archive)
        except Refusal as refusal:
            raise Refusal(f"the cached archive {cached}: {refusal}. Remove it and run again; "
                          "do not change the pin") from None
    else:
        url = pin.url(triple, base)
        log(f"download {url}")
        data = download(url, archive, environ, base)
        store(data, cached)
    log(f"{archive.file}: {archive.size} bytes, SHA-256 {archive.sha256}, as pinned")
    member = pin.member(triple)
    body = member_bytes(data, member, archive)
    log(f"{member}: {archive.upcli_size} bytes, SHA-256 {archive.upcli_sha256}, as pinned")
    os.mkdir(out_dir, 0o755)
    try:
        binary = os.path.join(out_dir, "upcli")
        write_binary(body, binary)
        with tempfile.TemporaryDirectory(prefix="upcli-version.") as scratch:
            os.mkdir(os.path.join(scratch, "home"))
            os.mkdir(os.path.join(scratch, "tmp"))
            env = {
                "PATH": environ.get("PATH", os.defpath),
                "HOME": os.path.join(scratch, "home"),
                "TMPDIR": os.path.join(scratch, "tmp"),
                "LC_ALL": "C",
                "TZ": "UTC",
            }
            check_cli(binary, pin.commit, env)
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
    log(f"upcli --version reports commit {pin.commit}, as pinned")
    return binary


def main(argv=None, root=ROOT, environ=None, base=RELEASES, host=None):
    """The command line; HOST, a (system, machine) pair, stands in for
    this host's in the tests."""
    environ = dict(os.environ if environ is None else environ)
    parser = argparse.ArgumentParser(prog="lib/upcli.py", description="The pinned unemupc CLI, upcli.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    fetch_parser = commands.add_parser(
        "fetch", help="download the pinned release archive, check it and take upcli out of it")
    fetch_parser.add_argument("--triple", help="the archive's target triple (default: the host's)")
    fetch_parser.add_argument("--cache", help="where the archive is kept (default: cache/upcli/)")
    fetch_parser.add_argument("out_dir", metavar="OUT_DIR", help="a new directory for OUT_DIR/upcli")
    try:
        options = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    root = pathlib.Path(root)
    try:
        pin = read_pin(root / PIN_FILE)
        triple = options.triple or host_triple(*(host or (None, None)))
        cache = options.cache or root / "cache" / "upcli"
        binary = fetch(pin, triple, options.out_dir, cache, environ, base)
    except Refusal as refusal:
        log(f"refused: {refusal}")
        return 1
    except OSError as error:
        log(f"failed: {error}")
        return 1
    print(binary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
