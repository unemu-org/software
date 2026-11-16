# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the media sets, import them with the pinned unemupc CLI (upcli) into
a scratch store, check them, and push them only when asked.

    python3 -I lib/publish.py --cli UPCLI [--store DIR] [--experimental RUN]
                              [--push | --allow-unpinned] [RECIPE|REF]...

    --cli UPCLI         the upcli binary, built from the unemupc commit in
                        upcli.pin (default: the UPCLI environment variable;
                        a name without a slash is looked up on PATH)
    --store DIR         import into DIR, a new directory kept afterwards
                        (default: a fresh store under build/, removed
                        afterwards)
    --experimental RUN  target <host>/unemu/experimental/media/<work>:
                        <media-tag>.ci<RUN> (RUN: the CI run number, from 1)
                        instead of <host>/unemu/media/<work>:<media-tag>
    --push              push every checked set; without it nothing is
                        written to any registry, and nothing is read from one
    --allow-unpinned    a dry run that passes with sets whose manifest pins
                        no digest yet (to measure a new recipe's digests);
                        never with --push
    RECIPE|REF          a recipe (recipes/<recipe>/) or one set's ref
                        (unemu/media/<work>:<tag>); default: every recipe

The steps, in order; a set that fails one is not taken further:

 1. Every recipe's manifest checked as `manifest.py check` does, and every
    set's ref as `manifest.py refs` does across all of them (the same
    functions, called here). A refusal stops the run before anything else.
 2. Each selected recipe's build.sh (whose fast path stops at once when its
    outputs already match their pins), then every output of the recipe
    checked here against its pin.
 3. Each set imported with `upcli image media import` (the arguments are
    manifest.import_arguments), then `upcli image media show`, whose last
    line must report the set pushable to unemu/, and `upcli image push
    --dry-run` against the target, whose guard must say allowed. Both run
    offline. The import, the show and the dry run must name the same ref,
    target and set digest.
 4. The set digest compared with the manifest's digest pin: another digest
    fails the run. A set without a pin is reported with its digest, is
    never pushed, and fails the run unless --allow-unpinned is given.
 5. With --push, and only when every selected set passed and is pinned: for
    each set, an anonymous HTTPS read of the target tag on the registry
    first (anonymous_digest). A missing tag is pushed with `upcli image
    push` (never --move) and read back: a read-back that fails or names
    another digest leaves the set `unconfirmed` (pushed, not confirmed). A
    tag that already names the same digest is skipped; a tag that names
    another digest is refused. Any failure fails the run, and nothing after
    it is pushed; every set still gets its row in the summary.

A summary table (repository, tag, digest, outcome, licence) goes to
stdout, progress to stderr. Exit status: 0 when every selected set passed
(and, with --push, is on the registry), 1 otherwise, 2 for a usage error.

The registry host is UNEMU_REGISTRY_HOST, checked as upcli checks one: a
name with a dot or a port, or localhost, the port at most 65535. A dry run
without it names the family default registry.unemu.dev, which it never
contacts; a push needs it set. A push takes its credentials only from the
environment, UNEMU_REGISTRY_USER and UNEMU_REGISTRY_PASSWORD, and hands
them to the upcli push alone, through its environment: never on a command
line, never to a recipe, never to another upcli command. A recipe gets the
caller's environment without any variable whose name starts with UNEMU or
contains PASSWORD, TOKEN, SECRET or CREDENTIAL. upcli runs with an
environment of its own (PATH, a fresh HOME and TMPDIR in the run's work
directory, LC_ALL=C, TZ=UTC) and a registries file of its own: the target
host, public, unemu/ only.

The CLI pin is the unemupc commit in upcli.pin; the run prints it and
compares it with the full 40-digit commit `upcli --version` prints, when it
prints one (upcli at the pinned commit has no --version). The set digest
pins are the real check that the CLI builds the sets this repository pins.

Standard library only. Run it with -I; it finds lib/manifest.py next to
itself.
"""

import argparse
import dataclasses
import hashlib
import http.client
import importlib.util
import ipaddress
import json
import os
import pathlib
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


def _manifest_module():
    """lib/manifest.py, loaded by path: -I keeps this directory off
    sys.path."""
    path = HERE / "manifest.py"
    loaded = sys.modules.get("manifest")
    if loaded is not None and getattr(loaded, "__file__", None) == str(path):
        return loaded
    spec = importlib.util.spec_from_file_location("manifest", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


manifest = _manifest_module()

CLI_ENV = "UPCLI"
USER_ENV = "UNEMU_REGISTRY_USER"
PASSWORD_ENV = "UNEMU_REGISTRY_PASSWORD"
HOST_ENV = "UNEMU_REGISTRY_HOST"
# What a recipe never sees: every UNEMU... variable (the registry pair and
# host, UNEMU_REGISTRY, upcli's default-host override, and UNEMUPC_*), and
# anything that looks like a secret. upcli gets the pair and the host only
# for a push.
RECIPE_HIDDEN_PREFIX = "UNEMU"
RECIPE_HIDDEN_WORDS = ("PASSWORD", "TOKEN", "SECRET", "CREDENTIAL")
# upcli's family default registry (image_registry::reference::DEFAULT_REGISTRY).
DEFAULT_HOST = "registry.unemu.dev"
EXPERIMENTAL_NAMESPACE = "unemu/experimental/media"
PIN_FILE = "upcli.pin"
COMMIT = re.compile(r"[0-9a-f]{40}")
REPORTED_COMMIT = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{40}(?![0-9A-Za-z])")
RUN = re.compile(r"[1-9][0-9]{0,19}")
# What `upcli image media show` says last of a set the guard lets into
# unemu/.
PUSHABLE = "push\tany configured registry, including unemu/"
# The manifest types the anonymous read accepts: upcli pushes an OCI image
# manifest; anything else at the tag (an index, a Docker schema 2 manifest
# or list) is read too, and refused by its digest. A registry that cannot
# answer in an accepted type may say 404, so a 404 is confirmed through the
# tag list.
ACCEPT = ", ".join((
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
))
MAX_MANIFEST = 4 * 1024 * 1024
MAX_TAG_PAGE = 4 * 1024 * 1024
MAX_TAG_PAGES = 20
# A registry read: at most this long idle on the socket, and the whole body
# within the deadline (read in chunks).
SOCKET_TIMEOUT = 30
REQUEST_DEADLINE = 120
NEXT_LINK = re.compile(r'\s*<([^>]*)>\s*;\s*rel="?next"?\s*')
USER_AGENT = "unemu-media-publish"


class Failure(Exception):
    """A step that failed, and why."""


class RegistryError(Exception):
    """The registry could not be read, or answered something unexpected."""


@dataclasses.dataclass
class Item:
    """One selected set and what became of it."""

    recipe: str
    checked: object
    media_set: object
    repository: str
    tag: str
    outcome: str = "not checked"
    digest: str | None = None
    detail: str | None = None
    failed: bool = False

    @property
    def ref(self):
        """The set's own ref, also its tag in the scratch store."""
        return f"{self.checked.release.repository}:{self.media_set.tag}"

    def fail(self, outcome, detail):
        self.outcome, self.detail, self.failed = outcome, detail, True


def log(text):
    print(text, file=sys.stderr, flush=True)


# --- hosts --------------------------------------------------------------------


def registry_host(text):
    """TEXT as upcli takes a registry host (config::validate_registry_host
    and reference::validate_host): trimmed, lower case, a name with a dot or
    a port, or localhost, or a bracketed IPv6 literal; labels of letters,
    digits and inner '-'; a port of digits up to 65535; no scheme or path.
    None when it is not one."""
    host = text.strip().lower()
    if not host or "/" in host or not ("." in host or ":" in host or host == "localhost"):
        return None
    if host.startswith("["):
        address, closed, rest = host[1:].partition("]")
        if not closed or not address or not all(c in "0123456789abcdef:" for c in address):
            return None
        if rest and not rest.startswith(":"):
            return None
        port = rest[1:] if rest else None
    else:
        name, colon, port = host.rpartition(":")
        if not colon:
            name, port = host, None
        if not name or len(name) > 253:
            return None
        for label in name.split("."):
            if not label or label[0] == "-" or label[-1] == "-":
                return None
            if not all(c.isascii() and (c.isalnum() or c == "-") for c in label):
                return None
    if port is not None and (not port or not port.isascii() or not port.isdigit() or int(port) > 65535):
        return None
    return host


def is_loopback(host):
    """config::is_loopback: localhost or a loopback address, port aside."""
    if host.startswith("["):
        name = host[1:].split("]", 1)[0]
    else:
        name = host.rpartition(":")[0] or host
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


# --- the registry -------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A registry read stays where it was sent: a redirect is an error."""

    def redirect_request(self, *args, **kwargs):
        return None


def _ca_bundle(environ):
    for name in ("SSL_CERT_FILE", "NIX_SSL_CERT_FILE", "CURL_CA_BUNDLE"):
        path = environ.get(name)
        if path and os.path.isfile(path):
            return path
    return None


def manifest_digest(body, claimed):
    """The digest of a manifest's exact bytes; a Docker-Content-Digest
    header (CLAIMED, or None) must name the same."""
    if len(body) > MAX_MANIFEST:
        raise RegistryError(f"the manifest is larger than {MAX_MANIFEST} bytes")
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    if claimed is not None and claimed.strip() != digest:
        raise RegistryError(f"Docker-Content-Digest {claimed.strip()!r} does not match the bytes ({digest})")
    return digest


def _unique_keys(pairs):
    """A JSON object that names a key once: a repeated "tags" would let
    either list win."""
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("a repeated key")
    return dict(pairs)


def environ_proxy(environ, host):
    """The HTTPS proxy ENVIRON names for HOST, as urllib reads one from the
    process environment (https_proxy before HTTPS_PROXY; no_proxy, or
    NO_PROXY, bypasses it), or None. Only ENVIRON counts, never the
    process's own environment or system settings."""
    proxy = environ.get("https_proxy") or environ.get("HTTPS_PROXY")
    if not proxy:
        return None
    bypass = environ.get("no_proxy") or environ.get("NO_PROXY") or ""
    if bypass and urllib.request.proxy_bypass_environment(host, {"no": bypass}):
        return None
    return proxy


class _Reader:
    """Anonymous GETs on one registry: HTTPS (through the proxy ENVIRON
    names, if any), or plain HTTP to a loopback host when a test asks for
    it, never through a proxy; never a redirect, never credentials."""

    def __init__(self, host, environ, plain_http):
        if plain_http and not is_loopback(host):
            raise RegistryError(f"plain HTTP is only for a loopback registry, not {host}")
        self.base = f"{'http' if plain_http else 'https'}://{host}"
        handlers = [_NoRedirect()]
        if plain_http:
            handlers.append(urllib.request.ProxyHandler({}))
        else:
            proxy = environ_proxy(environ, host)
            handlers.append(urllib.request.ProxyHandler({"https": proxy} if proxy else {}))
            context = ssl.create_default_context(cafile=_ca_bundle(environ))
            handlers.append(urllib.request.HTTPSHandler(context=context))
        self.opener = urllib.request.build_opener(*handlers)

    def get(self, url, accept, limit):
        """(status, headers, body) for 200 and 404. Any other answer, a
        malformed one, a body over LIMIT bytes or shorter than its
        Content-Length, a socket idle for SOCKET_TIMEOUT seconds or a body
        not complete within REQUEST_DEADLINE seconds is a RegistryError."""
        deadline = time.monotonic() + REQUEST_DEADLINE
        try:
            request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": USER_AGENT})
            with self.opener.open(request, timeout=SOCKET_TIMEOUT) as response:
                status, headers = response.status, response.headers
                chunks, size = [], 0
                while size <= limit:
                    if time.monotonic() > deadline:
                        raise RegistryError(f"{url}: no complete answer within {REQUEST_DEADLINE} seconds")
                    chunk = response.read1(min(64 * 1024, limit + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                body = b"".join(chunks)
        except urllib.error.HTTPError as error:
            error.close()
            if error.code == 404:
                return 404, error.headers, b""
            raise RegistryError(f"{url}: HTTP {error.code}") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as error:
            reason = getattr(error, "reason", None) or error
            raise RegistryError(f"{url}: {type(error).__name__}: {reason}") from None
        if status != 200:
            raise RegistryError(f"{url}: HTTP {status}")
        if len(body) > limit:
            raise RegistryError(f"{url}: the answer is larger than {limit} bytes")
        declared = headers.get("Content-Length")
        digits = (declared or "").strip()
        if declared is not None and (not (digits.isascii() and digits.isdigit()) or len(body) != int(digits)):
            raise RegistryError(f"{url}: {len(body)} bytes, but Content-Length says {declared}")
        return status, headers, body

    def tags(self, repository):
        """Every tag the registry lists for REPOSITORY, following at most
        MAX_TAG_PAGES pages; an empty list when it has no such repository
        (404 on the first page)."""
        path = f"/v2/{repository}/tags/list"
        url = self.base + path
        listed = []
        for page in range(MAX_TAG_PAGES):
            status, headers, body = self.get(url, "application/json", MAX_TAG_PAGE)
            if status == 404:
                if page == 0:
                    return []
                raise RegistryError(f"{url}: HTTP 404 in the middle of the tag list")
            try:
                tags = json.loads(body, object_pairs_hook=_unique_keys).get("tags")
            except (ValueError, AttributeError, RecursionError):
                raise RegistryError(f"{url}: not a tag list") from None
            if tags is None:
                tags = []
            if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
                raise RegistryError(f"{url}: not a tag list")
            listed.extend(tags)
            link = headers.get("Link")
            if not link:
                return listed
            url = self.next_page(url, path, link)
        raise RegistryError(f"the tag list of {repository} runs past {MAX_TAG_PAGES} pages")

    def next_page(self, url, path, link):
        """The next page a Link header names: on this registry, on this
        repository's tag list only."""
        found = NEXT_LINK.fullmatch(link)
        if not found:
            raise RegistryError(f"{url}: a Link header that names no next page: {link!r}")
        try:
            target = urllib.parse.urljoin(url, found.group(1))
            parts, base = urllib.parse.urlsplit(target), urllib.parse.urlsplit(self.base)
        except ValueError:
            raise RegistryError(f"{url}: a Link header with an invalid URL: {link!r}") from None
        if (parts.scheme, parts.netloc, parts.path) != (base.scheme, base.netloc, path) or parts.fragment:
            raise RegistryError(f"{url}: the next page {target!r} is not this tag list")
        return target


def anonymous_digest(host, repository, tag, environ=os.environ, plain_http=False):
    """The digest HOST serves for REPOSITORY:TAG to an anonymous reader, or
    None when the tag is absent. The manifest read must answer 200 or 404;
    a 404 is confirmed through the tag list (a tag it lists, also up to case
    and surrounding space, is an error). Any other answer, a redirect, a
    malformed or slow one included, is a RegistryError: the read fails
    closed. HTTPS only, through the proxy ENVIRON names; PLAIN_HTTP is for
    tests on a loopback host."""
    reader = _Reader(host, environ, plain_http)
    url = f"{reader.base}/v2/{repository}/manifests/{tag}"
    status, headers, body = reader.get(url, ACCEPT, MAX_MANIFEST)
    if status == 200:
        return manifest_digest(body, headers.get("Docker-Content-Digest"))
    # A near match (case, surrounding space) is a registry fault for a
    # lower-case media tag: it fails closed too.
    wanted = tag.strip().casefold()
    for listed in reader.tags(repository):
        if listed.strip().casefold() == wanted:
            raise RegistryError(
                f"{url}: HTTP 404, but the tag list names {listed!r}: the tag holds "
                "something the read cannot see"
            )
    return None


# --- the CLI ------------------------------------------------------------------


def read_pin(root):
    """The unemupc commit in upcli.pin: one line of 40 lower-case hex digits;
    blank lines and lines starting with '#' are comments."""
    path = root / PIN_FILE
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise Failure(f"{PIN_FILE}: cannot read: {error}") from None
    values = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if len(values) != 1 or not COMMIT.fullmatch(values[0]):
        raise Failure(f"{PIN_FILE}: expected one line holding a full 40-digit commit hash")
    return values[0]


def run_cli(cli, args, env):
    """Run upcli; its output is UTF-8, and anything else is an error."""
    try:
        return subprocess.run([cli, *args], capture_output=True, encoding="utf-8", errors="strict",
                              env=env, check=False)
    except OSError as error:
        raise Failure(f"cannot run {cli}: {error.strerror or error}") from None


def reported_commit(cli, env):
    """The full commit `upcli --version` prints, or None when it prints
    none (or fails)."""
    done = run_cli(cli, ["--version"], env)
    if done.returncode != 0:
        return None
    found = REPORTED_COMMIT.search(done.stdout)
    return found.group(0) if found else None


def last_line(text):
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def why(done):
    """The reason a CLI run failed, in one line."""
    return last_line(done.stderr) or last_line(done.stdout) or f"exit status {done.returncode}"


def describe(error):
    """A step's error for the summary: our own reasons as they are, anything
    else with its type."""
    if isinstance(error, (Failure, RegistryError)):
        return str(error)
    return f"unexpected {type(error).__name__}: {error}"


# --- the steps ----------------------------------------------------------------


def stream():
    """The file descriptor a child's output goes to (stderr), or None when
    stderr has none (output is then captured and copied)."""
    try:
        sys.stderr.flush()
        return sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        return None


def recipe_environment(environ):
    """The caller's environment without anything named UNEMU... (the
    registry variables among them) and anything whose name holds PASSWORD,
    TOKEN, SECRET or CREDENTIAL, in any case."""

    def hidden(name):
        upper = name.upper()
        return (
            upper.startswith(RECIPE_HIDDEN_PREFIX)
            or any(word in upper for word in RECIPE_HIDDEN_WORDS)
        )

    return {name: value for name, value in environ.items() if not hidden(name)}


def build(root, recipe, environ):
    """Run the recipe's build.sh in recipe_environment(ENVIRON)."""
    env = recipe_environment(environ)
    script = root / "recipes" / recipe / "build.sh"
    out = stream()
    try:
        if out is None:
            done = subprocess.run(["bash", str(script)], env=env, capture_output=True, encoding="utf-8",
                                  errors="replace", check=False)
            sys.stderr.write(done.stdout + done.stderr)
        else:
            done = subprocess.run(["bash", str(script)], env=env, stdout=out, stderr=out, check=False)
    except OSError as error:
        raise Failure(f"cannot run bash: {error.strerror or error}") from None
    if done.returncode != 0:
        raise Failure(f"recipes/{recipe}/build.sh exited with status {done.returncode}")


def check_outputs(checked, out_dir):
    """Every pinned output is a regular file in OUT_DIR with its SHA-256."""
    real_out = os.path.realpath(out_dir)
    for path, expected in manifest.pins(checked):
        full = os.path.join(out_dir, *path.split("/"))
        try:
            mode = os.lstat(full).st_mode
        except OSError:
            raise Failure(f"missing output {path} in {out_dir}") from None
        if not stat.S_ISREG(mode) or not os.path.realpath(full).startswith(real_out + os.sep):
            raise Failure(f"output {path} is not a regular file inside {out_dir}")
        digest = hashlib.sha256()
        with open(full, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise Failure(f"output {path}: SHA-256 {digest.hexdigest()}, pinned {expected}")


def import_set(cli, item, out_dir, store, env):
    """Import the set and check `media show`; the set digest, the same in
    both."""
    request = manifest.import_request(item.checked, item.media_set.tag, str(out_dir))
    statement = manifest.open_statement(item.checked)
    args = manifest.import_arguments(request, statement) + ["--store", str(store), "--create"]
    done = run_cli(cli, args, env)
    if done.returncode != 0:
        raise Failure(f"import: {why(done)}")
    reported = last_line(done.stdout).split("\t")
    if len(reported) != 2 or reported[0] != item.ref or not manifest.DIGEST.fullmatch(reported[1]):
        raise Failure(f"import: unexpected last line {last_line(done.stdout)!r}")
    digest = reported[1]

    done = run_cli(cli, ["image", "media", "show", item.ref, "--store", str(store)], env)
    if done.returncode != 0:
        raise Failure(f"media show: {why(done)}")
    lines = done.stdout.splitlines()
    first = lines[0].split("\t") if lines else []
    if len(first) != 3 or first[:2] != [item.ref, digest]:
        raise Failure(f"media show: the first line {lines[:1]} does not name {item.ref} {digest}")
    if lines[-1] != PUSHABLE:
        raise Failure(f"media show: the last line is {lines[-1]!r}, not {PUSHABLE!r}")
    return digest


def dry_run(cli, item, target, store, config, digest, env):
    args = ["image", "push", item.ref, target, "--store", str(store), "--dry-run", "--config", str(config)]
    done = run_cli(cli, args, env)
    lines = done.stdout.splitlines()
    if done.returncode != 0 or "guard:allowed" not in lines:
        raise Failure(f"push --dry-run {target}: {why(done)}")
    if f"target:{target}" not in lines or f"image:{digest}" not in lines:
        raise Failure(f"push --dry-run {target}: the plan names another target or image")


def push_set(cli, item, target, store, config, env):
    """`upcli image push`, never with --move; what it did and the digest it
    reports."""
    args = ["image", "push", item.ref, target, "--store", str(store), "--config", str(config)]
    done = run_cli(cli, args, env)
    if done.returncode != 0:
        raise Failure(f"push {target}: {why(done)}")
    reported = last_line(done.stdout).split("\t")
    if len(reported) != 3 or reported[0] not in ("pushed", "unchanged") or reported[1] != target:
        raise Failure(f"push {target}: unexpected last line {last_line(done.stdout)!r}")
    return reported[0], reported[2]


def remove_tree(path):
    """Remove the directory PATH whatever modes the store left on its
    directories (a file needs only its directory writable)."""
    if not os.path.isdir(path) or os.path.islink(path):
        return
    os.chmod(path, stat.S_IRWXU)
    for directory, names, _ in os.walk(path):
        for name in names:
            full = os.path.join(directory, name)
            if not os.path.islink(full):
                os.chmod(full, stat.S_IRWXU)
    shutil.rmtree(path)


# --- the run ------------------------------------------------------------------


def parse_args(argv, environ):
    parser = argparse.ArgumentParser(
        prog="lib/publish.py",
        description="Build, import, check and (with --push) publish the media sets.",
    )
    parser.add_argument("--cli", default=environ.get(CLI_ENV),
                        help=f"the upcli binary (default: ${CLI_ENV})")
    parser.add_argument("--store", help="a new directory to import into, kept afterwards")
    parser.add_argument("--experimental", metavar="RUN",
                        help="target unemu/experimental/media/<work>:<media-tag>.ci<RUN>")
    parser.add_argument("--push", action="store_true", help="push the checked sets")
    parser.add_argument("--allow-unpinned", action="store_true",
                        help="a dry run passes with sets that have no digest pin yet")
    parser.add_argument("selectors", nargs="*", metavar="RECIPE|REF")
    options = parser.parse_args(argv)
    if not options.cli:
        parser.error(f"--cli UPCLI (or ${CLI_ENV}) is required")
    if options.experimental is not None and not RUN.fullmatch(options.experimental):
        parser.error("--experimental RUN takes the CI run number: 1 to 20 digits, not starting with 0")
    if options.push and options.allow_unpinned:
        parser.error("--allow-unpinned is for a dry run; a set without a digest pin is never pushed")
    return parser, options


def target_host(parser, options, environ):
    text = environ.get(HOST_ENV, "")
    if not text:
        if options.push:
            parser.error(f"--push needs {HOST_ENV}: the registry is never implied for a push")
        return DEFAULT_HOST
    host = registry_host(text)
    if host is None:
        parser.error(
            f"{HOST_ENV} is not a registry host: a name with a dot or a port, or localhost, "
            "the port at most 65535, without a scheme or path"
        )
    return host


def find_cli(parser, cli, environ):
    """CLI as a path; a name without a slash is looked up on PATH."""
    if os.sep not in cli and (os.altsep is None or os.altsep not in cli):
        found = shutil.which(cli, path=environ.get("PATH", os.defpath))
        if found is None:
            parser.error(f"--cli {cli}: not found on PATH")
        cli = found
    return os.path.abspath(cli)


def select(parser, root, selectors):
    """Every manifest checked, and the selected sets as Items (without a
    target yet)."""
    recipes_dir = root / "recipes"
    recipes = sorted(
        p.name for p in recipes_dir.iterdir() if (p / "media.toml").is_file()
    ) if recipes_dir.is_dir() else []
    if not recipes:
        raise Failure(f"no recipes in {recipes_dir}")
    log("== check manifests")
    checked = {}
    for recipe in recipes:
        path = recipes_dir / recipe / "media.toml"
        try:
            checked[recipe] = manifest.load(path)
        except manifest.Refusal as refusal:
            raise Failure(f"recipes/{recipe}/media.toml: {refusal}") from None
        for warning in checked[recipe].warnings:
            log(f"recipes/{recipe}/media.toml: warning: {warning}")
        log(f"recipes/{recipe}/media.toml: ok, {len(checked[recipe].sets)} sets")
    try:
        manifest.refs([(f"recipes/{r}/media.toml", checked[r]) for r in recipes])
    except manifest.Refusal as refusal:
        raise Failure(str(refusal)) from None

    every = [(r, s) for r in recipes for s in checked[r].sets]
    if not selectors:
        chosen = every
    else:
        wanted = set()
        for selector in selectors:
            matching = [
                (r, s) for r, s in every
                if selector == r or selector == f"{checked[r].release.repository}:{s.tag}"
            ]
            if not matching:
                parser.error(f"{selector!r} is neither a recipe nor a set's ref (unemu/media/<work>:<tag>)")
            wanted.update((r, s.tag) for r, s in matching)
        chosen = [(r, s) for r, s in every if (r, s.tag) in wanted]
    return [Item(r, checked[r], s, "", "") for r, s in chosen]


def summary(items, host, mode):
    rows = [("REPOSITORY", "TAG", "DIGEST", "OUTCOME", "LICENCE")]
    for item in items:
        rows.append((
            item.repository,
            item.tag,
            item.digest or "-",
            item.outcome,
            item.checked.release.license,
        ))
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    print(f"registry {host} ({mode})")
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row[:4])) + "  " + row[4])
    for item in items:
        if item.detail:
            print(f"{item.repository}:{item.tag}: {item.outcome}: {item.detail}")


def main(argv=None, root=ROOT, registry=None, environ=None):
    environ = dict(os.environ if environ is None else environ)
    registry = registry or (lambda host, repository, tag: anonymous_digest(host, repository, tag, environ))
    root = pathlib.Path(root)
    try:
        parser, options = parse_args(sys.argv[1:] if argv is None else argv, environ)
        host = target_host(parser, options, environ)
        if options.push and not (environ.get(USER_ENV) and environ.get(PASSWORD_ENV)):
            parser.error(f"--push needs {USER_ENV} and {PASSWORD_ENV} in the environment")
        cli = find_cli(parser, options.cli, environ)
        store_arg = None
        if options.store is not None:
            store_arg = pathlib.Path(options.store).resolve()
            if os.path.lexists(store_arg):
                parser.error(f"--store {options.store}: it exists; give a new directory")
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2

    try:
        pin = read_pin(root)
        items = select(parser, root, options.selectors)
    except Failure as failure:
        log(f"refused: {failure}")
        return 1
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2

    for item in items:
        if options.experimental is None:
            item.repository = item.checked.release.repository
            item.tag = item.media_set.tag
        else:
            item.repository = f"{EXPERIMENTAL_NAMESPACE}/{item.checked.release.work}"
            item.tag = f"{item.media_set.tag}.ci{options.experimental}"
            if not manifest.TAG.fullmatch(item.tag):
                log(f"refused: the target tag {item.tag!r} is longer than 128 characters")
                return 1

    (root / "build").mkdir(exist_ok=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix="publish.", dir=root / "build"))
    try:
        return run(options, root, cli, pin, host, items, work, store_arg, registry, environ)
    finally:
        remove_tree(work)


def run(options, root, cli, pin, host, items, work, store_arg, registry, environ):
    store = store_arg or work / "store"
    (work / "home").mkdir()
    (work / "tmp").mkdir()
    config = work / "registries.toml"
    config.write_text(
        "# lib/publish.py: the target registry for one run, public, unemu/ only.\n"
        "[[registries]]\n"
        f'host = "{host}"\n'
        'namespaces = [{ name = "unemu", private = false }]\n',
        encoding="ascii",
    )
    env = {
        "PATH": environ.get("PATH", os.defpath),
        "HOME": str(work / "home"),
        "TMPDIR": str(work / "tmp"),
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    mode = "push" if options.push else "dry run"
    log(f"upcli {cli}")
    log(f"upcli pin {pin} (unemupc commit, {PIN_FILE})")
    try:
        commit = reported_commit(cli, env)
    except Failure as failure:
        log(f"refused: {failure}")
        return 1
    if commit is None:
        log("upcli --version reports no commit; the set digest pins are the check")
    elif commit != pin:
        log(f"refused: upcli --version reports commit {commit}, {PIN_FILE} holds {pin}")
        return 1
    else:
        log(f"upcli --version reports commit {commit}, as pinned")
    log(f"store {store}")

    for recipe in dict.fromkeys(item.recipe for item in items):
        group = [item for item in items if item.recipe == recipe]
        out_dir = root / "out" / recipe
        log(f"== build {recipe}")
        try:
            build(root, recipe, environ)
            check_outputs(group[0].checked, out_dir)
        except Exception as error:  # noqa: BLE001 - every failure reaches the summary
            for item in group:
                item.fail("failed", describe(error))
            continue
        for item in group:
            target = f"{host}/{item.repository}:{item.tag}"
            log(f"== import {item.ref}")
            try:
                item.digest = import_set(cli, item, out_dir, store, env)
                dry_run(cli, item, target, store, config, item.digest, env)
            except Exception as error:  # noqa: BLE001 - every failure reaches the summary
                item.fail("failed", describe(error))
                continue
            pinned = item.media_set.digest
            if pinned is None:
                item.outcome = "unpinned"
                item.detail = "no digest pin in the manifest; never pushed"
            elif pinned != item.digest:
                item.fail("mismatch", f"upcli gives {item.digest}, the manifest pins {pinned}")
            else:
                item.outcome = "dry run"
            log(f"{item.ref}: {item.digest} -> {target}: {item.outcome}")

    status = 1 if any(item.failed for item in items) else 0
    if not options.allow_unpinned and any(item.outcome == "unpinned" for item in items):
        status = 1
    if options.push:
        status = push_all(cli, host, items, store, config, env, registry, environ) or status
    summary(items, host, mode)
    return status


def push_all(cli, host, items, store, config, env, registry, environ):
    """Push every set, after an anonymous read of its target, when every
    set passed and is pinned; stop at the first refusal or failure. 1
    unless every set ends on the registry."""
    if any(item.failed or item.outcome == "unpinned" for item in items):
        for item in items:
            if item.outcome == "dry run":
                item.outcome, item.detail = "held", "not pushed: another set failed or has no digest pin"
        return 1
    secrets = dict(env)
    secrets[USER_ENV] = environ[USER_ENV]
    secrets[PASSWORD_ENV] = environ[PASSWORD_ENV]
    secrets[HOST_ENV] = host
    status = 0
    for item in items:
        if status:
            item.outcome, item.detail = "held", "not pushed: an earlier push failed"
            continue
        target = f"{host}/{item.repository}:{item.tag}"
        log(f"== push {target}")
        push_one(cli, host, item, target, store, config, secrets, registry)
        if item.failed:
            status = 1
    return status


def push_one(cli, host, item, target, store, config, secrets, registry):
    """One set: the anonymous read, the push and the read-back. Whatever
    goes wrong marks the set; once upcli has pushed, a failed or other
    read-back marks it pushed but unconfirmed."""
    try:
        current = registry(host, item.repository, item.tag)
        if current == item.digest:
            item.outcome = "present"
            return
        if current is not None:
            item.fail("refused", f"the registry already holds {current} under this tag; a media tag never moves")
            return
        word, digest = push_set(cli, item, target, store, config, secrets)
    except Exception as error:  # noqa: BLE001 - every failure reaches the summary
        item.fail("failed", describe(error))
        return
    if digest != item.digest:
        item.fail("failed", f"upcli pushed {digest}, the set is {item.digest}")
        return
    try:
        served = registry(host, item.repository, item.tag)
    except Exception as error:  # noqa: BLE001 - every failure reaches the summary
        item.fail("unconfirmed", f"upcli {word} {digest}; the read-back failed: {describe(error)}")
        return
    if served != item.digest:
        item.fail("unconfirmed", f"upcli {word} {digest}; the registry serves {served}")
        return
    item.outcome = "pushed" if word == "pushed" else "present"


if __name__ == "__main__":
    sys.exit(main())
