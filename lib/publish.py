# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the media sets and firmware packages, import them with the pinned
unemupc CLI (upcli) into a scratch store, check them, and push them only when
asked.

    python3 -I lib/publish.py --cli UPCLI [--store DIR] [--experimental RUN]
                              [--skip-published] [--no-build]
                              [--push | --allow-unpinned] [RECIPE|REF]...

    --cli UPCLI         the upcli binary of the unemupc release in
                        upcli.toml (lib/upcli.py fetch), or one built at its
                        commit (default: the UPCLI environment variable; a
                        name without a slash is looked up on PATH)
    --store DIR         import into DIR, a new directory kept afterwards
                        (default: a fresh store under build/, removed
                        afterwards)
    --experimental RUN  target <host>/unemu/experimental/media/<work>:
                        <media-tag>.ci<RUN> and <host>/unemu/experimental/
                        firmware/<id>:<revision>.ci<RUN> (RUN: the CI run's
                        id, from 1) instead of <host>/unemu/media/<work>:
                        <media-tag> and <host>/unemu/firmware/<id>:<revision>
    --skip-published    read each pinned set's and package's own tag on the
                        registry (unemu/media/... or unemu/firmware/...,
                        with --experimental too) anonymously first: one that
                        already serves the pinned digest is `published` and
                        is neither built, imported nor pushed; one that
                        serves another digest is `refused`
    --no-build          run no build.sh: the outputs must already be in
                        out/<recipe>/ and match their pins
    --push              push every checked set and package; without it
                        nothing is written to any registry, and nothing is
                        read from one unless --skip-published asks
    --allow-unpinned    a dry run that passes with sets or packages whose
                        manifest pins no digest yet (to measure a new
                        recipe's digests); never with --push
    RECIPE|REF          a recipe (recipes/<recipe>/), one set's ref
                        (unemu/media/<work>:<tag>) or one package's ref
                        (unemu/firmware/<id>:<revision>); default: every
                        recipe

Each recipe has one manifest: media.toml, the sets of an installation media
release, or firmware.toml, one firmware package (lib/manifest.py). The
steps, in order; a set or package that fails one is not taken further:

 1. Every recipe's manifest checked as `manifest.py check` does, and every
    ref as `manifest.py refs` does across all of them (the same functions,
    called here). A refusal stops the run before anything else.
 2. With --skip-published, for each selected set or package with a digest
    pin, an anonymous HTTPS read of its own tag on the registry
    (anonymous_digest, as before a push), whatever the target: a tag that
    serves the pinned digest is `published` and goes no further, so a
    recipe whose sets and packages are all published is not built; a tag
    that serves another digest is `refused` (a tag never moves: a new one is
    needed); a missing tag goes on. A read that fails fails the set or
    package. One without a pin cannot be compared: it goes on, and ends
    `unpinned` as below.
 3. Each selected recipe's build.sh (whose fast path stops at once when its
    outputs already match their pins), then every output of the recipe
    checked here against its pin. With --no-build no build.sh runs: the
    outputs must already be in out/<recipe>/ with their pinned hashes, or
    the recipe's sets and packages fail. This is how a CI job that holds
    credentials, and runs no recipe code, pushes what a job without them
    built.
 4. Each set imported with `upcli image media import` (the arguments are
    manifest.import_arguments), then checked with `upcli image media show`;
    each package imported with `upcli image firmware import --for` (the
    arguments are manifest.firmware_import_arguments), then checked with
    `upcli image firmware show`: its title, vendor, version, description,
    labels, ROM, source archive and every layer's licence must be the
    manifest's. Either show's last line must report it pushable to unemu/.
    Then `upcli image push --dry-run` against the target, whose guard must
    say allowed. All run offline. The import, the show and the dry run must
    name the same store tag, target and digest.
 5. The digest compared with the manifest's digest pin: another digest fails
    the run. One without a pin is reported with its digest, is never
    pushed, and fails the run unless --allow-unpinned is given.
 6. With --push, and only when everything selected passed and is pinned:
    for each that is not `published`, an anonymous HTTPS read of the target
    tag on the registry first (anonymous_digest). A missing tag is pushed
    with `upcli image push` (never --move) and read back: a read-back that
    fails or names another digest leaves it `unconfirmed` (pushed, not
    confirmed). A tag that already names the same digest is skipped; a tag
    that names another digest is refused: a media tag and a package revision
    never move. Any failure fails the run, and nothing after it is pushed;
    everything still gets its row in the summary. The `latest` alias is
    never pushed.

A summary table (repository, tag, digest, outcome, licence) goes to
stdout, progress to stderr. Exit status: 0 when everything selected passed
(and, with --push, is on the registry), 1 otherwise, 2 for a usage error.

The registry host is UNEMU_REGISTRY_HOST, checked as upcli checks one: a
name with a dot or a port, or localhost, the port at most 65535. A dry run
without it names the family default registry.unemu.dev, which it contacts
only for the anonymous reads of --skip-published; a push needs it set. A
push takes its credentials only from the environment, UNEMU_REGISTRY_USER
and UNEMU_REGISTRY_PASSWORD, and hands them to the upcli push alone,
through its environment: never on a command line, never to a recipe, never
to another upcli command. A recipe gets the caller's environment without
any variable whose name starts with UNEMU, GITHUB_, RUNNER_ or ACTIONS_, is
BASH_ENV or ENV, or contains PASSWORD, TOKEN, SECRET or CREDENTIAL. upcli
runs with an environment of its own (PATH, a fresh HOME and TMPDIR in the
run's work directory, LC_ALL=C, TZ=UTC) and a registries file of its own:
the target host, public, unemu/ only.

The CLI pin is the unemupc release in upcli.toml (lib/upcli.py). Before
anything is built, `upcli --version` runs once, and its commit line, the
one line `commit: <40 lower-case hex digits>`, must name the pinned commit
(upcli.check_cli): another commit, a malformed or repeated commit line, a
failed --version, or none at all, refuses the run. A release build prints
that line; a source build prints it only when built with
UNEMUPC_SOURCE_COMMIT set to the commit, and is refused without it. There
is no way around this check, for a dry run either. The digest pins remain
the real check that the CLI builds the sets and packages this repository
pins.

Standard library only. Run it with -I; it finds lib/manifest.py and
lib/upcli.py next to itself.
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


def _module(name):
    """lib/<NAME>.py, loaded by path: -I keeps this directory off
    sys.path."""
    path = HERE / f"{name}.py"
    loaded = sys.modules.get(name)
    if loaded is not None and getattr(loaded, "__file__", None) == str(path):
        return loaded
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


manifest = _module("manifest")
upcli = _module("upcli")

CLI_ENV = "UPCLI"
USER_ENV = "UNEMU_REGISTRY_USER"
PASSWORD_ENV = "UNEMU_REGISTRY_PASSWORD"
HOST_ENV = "UNEMU_REGISTRY_HOST"
# What a recipe never sees: every UNEMU... variable (the registry pair and
# host, UNEMU_REGISTRY, upcli's default-host override, and UNEMUPC_*),
# anything that looks like a secret, and what would let it reach past its
# own run on a CI runner: the GitHub Actions files and settings (GITHUB_ENV,
# GITHUB_PATH, GITHUB_OUTPUT, GITHUB_STATE, RUNNER_TEMP, ACTIONS_*) and the
# start-up files of bash and sh (BASH_ENV, ENV). upcli gets the pair and
# the host only for a push.
RECIPE_HIDDEN_PREFIXES = ("UNEMU", "GITHUB_", "RUNNER_", "ACTIONS_")
RECIPE_HIDDEN_NAMES = ("BASH_ENV", "ENV")
RECIPE_HIDDEN_WORDS = ("PASSWORD", "TOKEN", "SECRET", "CREDENTIAL")
# upcli's family default registry (image_registry::reference::DEFAULT_REGISTRY).
DEFAULT_HOST = "registry.unemu.dev"
# Every repository lives in unemu/; an experimental target puts
# experimental/ after it (unemu/experimental/media/<work>,
# unemu/experimental/firmware/<id>).
OFFICIAL_PREFIX = "unemu/"
EXPERIMENTAL_PREFIX = "unemu/experimental/"
# A recipe's manifest: media.toml or firmware.toml, by kind.
MANIFEST_FILES = tuple(manifest.MANIFEST_NAMES)
PIN_FILE = upcli.PIN_FILE
RUN = re.compile(r"[1-9][0-9]{0,19}")
# What `upcli image media show` and `upcli image firmware show` say last of
# a set or package the guard lets into unemu/.
PUSHABLE = "push\tany configured registry, including unemu/"
# The licence of a package's config and README layers
# (pc_image::firmware::METADATA_LICENSE).
FIRMWARE_METADATA_LICENSE = "CC0-1.0"
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
    """One selected set or package (a manifest.Artifact) and what became of
    it; REPOSITORY and TAG are its target's."""

    recipe: str
    checked: object
    artifact: object
    repository: str
    tag: str
    outcome: str = "not checked"
    digest: str | None = None
    detail: str | None = None
    failed: bool = False

    @property
    def ref(self):
        """The set's or package's own ref, unemu/<kind>/<name>:<tag>."""
        return self.artifact.ref

    @property
    def store_tag(self):
        """Its tag in the scratch store: a set's ref, a package's
        firmware/<id>:1."""
        return self.artifact.store_tag

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
    """The unemupc release upcli.toml pins (upcli.read_pin)."""
    try:
        return upcli.read_pin(root / PIN_FILE)
    except upcli.Refusal as refusal:
        raise Failure(str(refusal)) from None


def run_cli(cli, args, env):
    """Run upcli; its output is UTF-8, and anything else is an error."""
    try:
        return subprocess.run([cli, *args], capture_output=True, encoding="utf-8", errors="strict",
                              env=env, check=False)
    except OSError as error:
        raise Failure(f"cannot run {cli}: {error.strerror or error}") from None


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
    registry variables among them), GITHUB_..., RUNNER_..., ACTIONS_...,
    BASH_ENV or ENV, and anything whose name holds PASSWORD, TOKEN, SECRET
    or CREDENTIAL, in any case."""

    def hidden(name):
        upper = name.upper()
        return (
            upper.startswith(RECIPE_HIDDEN_PREFIXES)
            or upper in RECIPE_HIDDEN_NAMES
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


def import_artifact(cli, item, out_dir, store, env):
    """Import the set or package into STORE and check what upcli shows of
    it; its digest."""
    args = [*item.artifact.arguments, "--store", str(store), "--create"]
    done = run_cli(cli, args, env)
    if done.returncode != 0:
        raise Failure(f"import: {why(done)}")
    if item.artifact.kind == "firmware":
        digest = imported_package(item, done.stdout)
        check_firmware_show(cli, item, digest, out_dir, store, env)
    else:
        digest = imported_set(item, done.stdout)
        check_media_show(cli, item, digest, store, env)
    return digest


def imported_set(item, stdout):
    """The set digest `image media import` reports last, for the set's
    ref."""
    reported = last_line(stdout).split("\t")
    if len(reported) != 2 or reported[0] != item.ref or not manifest.DIGEST.fullmatch(reported[1]):
        raise Failure(f"import: unexpected last line {last_line(stdout)!r}")
    return reported[1]


def check_media_show(cli, item, digest, store, env):
    done = run_cli(cli, ["image", "media", "show", item.store_tag, "--store", str(store)], env)
    if done.returncode != 0:
        raise Failure(f"media show: {why(done)}")
    lines = done.stdout.splitlines()
    first = lines[0].split("\t") if lines else []
    if len(first) != 3 or first[:2] != [item.store_tag, digest]:
        raise Failure(f"media show: the first line {lines[:1]} does not name {item.store_tag} {digest}")
    if lines[-1] != PUSHABLE:
        raise Failure(f"media show: the last line is {lines[-1]!r}, not {PUSHABLE!r}")


def imported_package(item, stdout):
    """The package digest `image firmware import --for` reports: its output
    must be exactly the complete line for the store tag and the pinned ROM,
    then the imported line. Anything else, a note above all (one that keeps
    the ROM user-supplied, say), or "already imported", fails."""
    tag, rom = item.store_tag, item.checked.rom.sha256
    lines = [line for line in stdout.splitlines() if line.strip()]
    if lines[:1] != [f"complete\t{tag}\t{rom}"] or len(lines) != 2:
        raise Failure(f"import: unexpected output {lines!r}; expected the complete line of {tag} "
                      "and the imported line only")
    reported = lines[1].split("\t")
    if len(reported) != 3 or reported[:2] != ["imported", tag] or not manifest.DIGEST.fullmatch(reported[2]):
        raise Failure(f"import: unexpected last line {lines[1]!r}")
    return reported[2]


def rust_debug(text):
    """TEXT as upcli's show prints a string, Rust's Debug of a str: in double
    quotes, with backslash, double quote and control characters escaped.
    Rust also escapes some non-ASCII characters (unprintable ones, grapheme
    extenders), which are kept here: such a text then fails the comparison,
    never passes it falsely."""
    escapes = {"\\": "\\\\", '"': '\\"', "\t": "\\t", "\r": "\\r", "\n": "\\n", "\0": "\\0"}
    out = []
    for char in text:
        if char in escapes:
            out.append(escapes[char])
        elif char < " " or char == "\x7f":
            out.append(f"\\u{{{ord(char):x}}}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def file_size(path):
    try:
        return os.stat(path).st_size
    except OSError as error:
        raise Failure(f"cannot read {path}: {error.strerror}") from None


def check_firmware_show(cli, item, digest, out_dir, store, env):
    """`upcli image firmware show` of the imported package states the
    manifest's package (pc-image's CLI image/firmware.rs, show): the store
    tag, digest and title first; one ROM, the pinned one with its size; the
    vendor, version, description and labels (sorted, as the import stores
    them); one source archive, the pinned one with its file name, format and
    size; every layer open, the ROM and the source archive under the
    manifest's licence and the config and README under CC0-1.0; and last,
    pushable to unemu/."""
    package, rom, source = item.checked.package, item.checked.rom, item.checked.source
    done = run_cli(cli, ["image", "firmware", "show", item.store_tag, "--store", str(store)], env)
    if done.returncode != 0:
        raise Failure(f"firmware show: {why(done)}")
    lines = done.stdout.splitlines()

    def refuse(what):
        raise Failure(f"firmware show: {what}")

    if not lines or lines[0].split("\t") != [item.store_tag, digest, rust_debug(package.title)]:
        refuse(f"the first line {lines[:1]} does not name {item.store_tag} {digest} {package.title!r}")
    rows = [line.split("\t") for line in lines[1:]]
    roms = [row for row in rows if len(row) == 4 and manifest.SHA256.fullmatch(row[3])]
    rom_size = file_size(os.path.join(out_dir, *rom.path.split("/")))
    if len(roms) != 1 or roms[0][2:] != [str(rom_size), rom.sha256]:
        refuse(f"the ROM lines {roms} are not the one ROM {rom.sha256} of {rom_size} bytes")
    labels = "[" + ", ".join(rust_debug(label) for label in sorted(package.labels)) + "]"
    for key, value in (("vendor", rust_debug(package.vendor)), ("version", rust_debug(package.version)),
                       ("description", rust_debug(package.description)), ("labels", labels)):
        found = [row[1:] for row in rows if row[0] == key]
        if found != [[value]]:
            refuse(f"{key} is {found}, not [{value!r}]")
    source_size = file_size(os.path.join(out_dir, *source.path.split("/")))
    source_format = "zip" if source.path.endswith(".zip") else "tar"
    sources = [row[1:5] for row in rows if row[0] == "source"]
    expected = [rust_debug(manifest.file_name(source.path)), source_format, str(source_size),
                f"sha256:{source.sha256}"]
    if sources != [expected]:
        refuse(f"the source lines {sources} are not the one archive {expected}")
    _, license = manifest.parse_license(package.license, "package.license")
    layers = [row[1:] for row in rows if row[0] == "layer"]
    wanted = {
        "rom": [["rom", f"sha256:{rom.sha256}", "open", license]],
        "source": [["source", f"sha256:{source.sha256}", "open", license]],
    }
    for kind, expected in wanted.items():
        found = [layer for layer in layers if layer[:1] == [kind]]
        if found != expected:
            refuse(f"the {kind} layers {found} are not {expected}")
    for layer in layers:
        if layer[:1] not in (["rom"], ["source"]) and layer[2:] != ["open", FIRMWARE_METADATA_LICENSE]:
            refuse(f"the {' '.join(layer[:1])} layer is {layer[2:]}, not open under {FIRMWARE_METADATA_LICENSE}")
    if lines[-1] != PUSHABLE:
        refuse(f"the last line is {lines[-1]!r}, not {PUSHABLE!r}")


def dry_run(cli, item, target, store, config, digest, env):
    args = ["image", "push", item.store_tag, target, "--store", str(store), "--dry-run", "--config", str(config)]
    done = run_cli(cli, args, env)
    lines = done.stdout.splitlines()
    if done.returncode != 0 or "guard:allowed" not in lines:
        raise Failure(f"push --dry-run {target}: {why(done)}")
    if f"target:{target}" not in lines or f"image:{digest}" not in lines:
        raise Failure(f"push --dry-run {target}: the plan names another target or image")


def push_artifact(cli, item, target, store, config, env):
    """`upcli image push`, never with --move; what it did and the digest it
    reports."""
    args = ["image", "push", item.store_tag, target, "--store", str(store), "--config", str(config)]
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
        description="Build, import, check and (with --push) publish the media sets and firmware packages.",
    )
    parser.add_argument("--cli", default=environ.get(CLI_ENV),
                        help=f"the upcli binary (default: ${CLI_ENV})")
    parser.add_argument("--store", help="a new directory to import into, kept afterwards")
    parser.add_argument("--experimental", metavar="RUN",
                        help="target unemu/experimental/media/<work>:<media-tag>.ci<RUN> and "
                             "unemu/experimental/firmware/<id>:<revision>.ci<RUN>")
    parser.add_argument("--skip-published", action="store_true",
                        help="read each pinned set's and package's own tag anonymously first; one that "
                             "already serves the pinned digest is neither built nor pushed")
    parser.add_argument("--no-build", action="store_true",
                        help="run no build.sh: the outputs must already be in out/<recipe>/ and match "
                             "their pins")
    parser.add_argument("--push", action="store_true", help="push the checked sets and packages")
    parser.add_argument("--allow-unpinned", action="store_true",
                        help="a dry run passes with sets or packages that have no digest pin yet")
    parser.add_argument("selectors", nargs="*", metavar="RECIPE|REF")
    options = parser.parse_args(argv)
    if not options.cli:
        parser.error(f"--cli UPCLI (or ${CLI_ENV}) is required")
    if options.experimental is not None and not RUN.fullmatch(options.experimental):
        parser.error("--experimental RUN takes the CI run id: 1 to 20 digits, not starting with 0")
    if options.push and options.allow_unpinned:
        parser.error("--allow-unpinned is for a dry run; nothing without a digest pin is ever pushed")
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


def recipe_manifest(recipes_dir, recipe):
    """The file name of the recipe's one manifest, or None when it has
    none; a Failure when it has both kinds."""
    found = [name for name in MANIFEST_FILES if (recipes_dir / recipe / name).is_file()]
    if len(found) > 1:
        raise Failure(f"recipes/{recipe} has both {' and '.join(found)}; a recipe has one manifest")
    return found[0] if found else None


def select(parser, root, selectors, out_root):
    """Every manifest checked, and the selected sets and packages as Items
    (without a target yet), their import arguments naming the outputs in
    OUT_ROOT/<recipe>."""
    recipes_dir = root / "recipes"
    found = {}
    if recipes_dir.is_dir():
        for path in recipes_dir.iterdir():
            name = recipe_manifest(recipes_dir, path.name)
            if name is not None:
                found[path.name] = name
    recipes = sorted(found)
    if not recipes:
        raise Failure(f"no recipes in {recipes_dir}")
    log("== check manifests")
    checked = {}
    for recipe in recipes:
        name = f"recipes/{recipe}/{found[recipe]}"
        try:
            checked[recipe] = manifest.load(recipes_dir / recipe / found[recipe])
        except manifest.Refusal as refusal:
            raise Failure(f"{name}: {refusal}") from None
        # The file name says the kind (manifest.load).
        is_package = isinstance(checked[recipe], manifest.FirmwareManifest)
        for warning in checked[recipe].warnings:
            log(f"{name}: warning: {warning}")
        if is_package:
            log(f"{name}: ok, firmware package")
        else:
            log(f"{name}: ok, {len(checked[recipe].sets)} sets")
    try:
        manifest.refs([(f"recipes/{r}/{found[r]}", checked[r]) for r in recipes])
    except manifest.Refusal as refusal:
        raise Failure(str(refusal)) from None

    every = [(r, a) for r in recipes for a in manifest.artifacts(checked[r], str(out_root / r))]
    if not selectors:
        chosen = every
    else:
        wanted = set()
        for selector in selectors:
            matching = [(r, a) for r, a in every if selector in (r, a.ref)]
            if not matching:
                parser.error(f"{selector!r} is neither a recipe nor a ref (unemu/media/<work>:<tag> "
                             "or unemu/firmware/<id>:<revision>)")
            wanted.update(a.ref for _, a in matching)
        chosen = [(r, a) for r, a in every if a.ref in wanted]
    return [Item(r, checked[r], a, "", "") for r, a in chosen]


def set_target(item, experimental):
    """Set the item's target repository and tag: its own, or with
    EXPERIMENTAL (a run id) unemu/experimental/<kind>/<name>:
    <tag>.ci<RUN>. A Failure when the experimental repository or tag is
    longer than a repository name or tag may be: the manifests keep their
    own within the limits, not the experimental one."""
    if experimental is None:
        item.repository, item.tag = item.artifact.repository, item.artifact.tag
        return
    repository = item.artifact.repository
    if not repository.startswith(OFFICIAL_PREFIX):
        raise Failure(f"{repository} is outside {OFFICIAL_PREFIX}")
    item.repository = EXPERIMENTAL_PREFIX + repository[len(OFFICIAL_PREFIX):]
    item.tag = f"{item.artifact.tag}.ci{experimental}"
    if len(item.repository) > manifest.MAX_REPOSITORY:
        raise Failure(
            f"the experimental repository {item.repository} of {item.ref} is "
            f"{len(item.repository)} bytes, longer than {manifest.MAX_REPOSITORY}"
        )
    if not manifest.TAG.fullmatch(item.tag):
        raise Failure(f"the target tag {item.tag!r} is longer than 128 characters")


def summary(items, host, mode):
    rows = [("REPOSITORY", "TAG", "DIGEST", "OUTCOME", "LICENCE")]
    for item in items:
        rows.append((
            item.repository,
            item.tag,
            item.digest or "-",
            item.outcome,
            item.artifact.license,
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
        items = select(parser, root, options.selectors, root / "out")
        for item in items:
            set_target(item, options.experimental)
    except Failure as failure:
        log(f"refused: {failure}")
        return 1
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2

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
    log(f"upcli pin {pin.commit} (unemupc {pin.tag}, {PIN_FILE})")
    try:
        upcli.check_cli(cli, pin.commit, env)
    except upcli.Refusal as refusal:
        log(f"refused: {refusal}")
        return 1
    log(f"upcli --version reports commit {pin.commit}, as pinned")
    log(f"store {store}")

    if options.skip_published:
        log(f"== read the published tags on {host}")
        for item in items:
            check_published(host, item, registry)

    for recipe in dict.fromkeys(item.recipe for item in items):
        selected = [item for item in items if item.recipe == recipe]
        # Only what is still to do: not published, not refused or failed.
        group = [item for item in selected if item.outcome == "not checked"]
        if not group:
            if all(item.outcome == "published" for item in selected):
                log(f"== skip {recipe}: published, not built")
            else:
                log(f"== skip {recipe}: nothing of it is left to build")
            continue
        out_dir = root / "out" / recipe
        try:
            if options.no_build:
                log(f"== check the outputs of {recipe} (--no-build)")
                existing_outputs(recipe, group[0].checked, out_dir)
            else:
                log(f"== build {recipe}")
                build(root, recipe, environ)
                check_outputs(group[0].checked, out_dir)
        except Exception as error:  # noqa: BLE001 - every failure reaches the summary
            for item in group:
                item.fail("failed", describe(error))
            continue
        for item in group:
            reference = f"{host}/{item.repository}:{item.tag}"
            log(f"== import {item.ref}")
            try:
                item.digest = import_artifact(cli, item, out_dir, store, env)
                dry_run(cli, item, reference, store, config, item.digest, env)
            except Exception as error:  # noqa: BLE001 - every failure reaches the summary
                item.fail("failed", describe(error))
                continue
            pinned = item.artifact.digest
            if pinned is None:
                item.outcome = "unpinned"
                item.detail = "no digest pin in the manifest; never pushed"
            elif pinned != item.digest:
                item.fail("mismatch", f"upcli gives {item.digest}, the manifest pins {pinned}")
            else:
                item.outcome = "dry run"
            log(f"{item.ref}: {item.digest} -> {reference}: {item.outcome}")

    status = 1 if any(item.failed for item in items) else 0
    if not options.allow_unpinned and any(item.outcome == "unpinned" for item in items):
        status = 1
    if options.push:
        status = push_all(cli, host, items, store, config, env, registry, environ) or status
    summary(items, host, mode)
    return status


def never_moves(item):
    """Why a tag that holds another digest is the end of the road."""
    if item.artifact.kind == "firmware":
        return "a package revision never moves"
    return "a media tag never moves"


def check_published(host, item, registry):
    """--skip-published: the anonymous read of the item's own tag (on
    unemu/media/ or unemu/firmware/, whatever the target). The same digest
    as the pin makes it `published`, another digest `refused`; a missing tag
    leaves it to be built. The row then names the tag that was read. An item
    without a pin is not read: nothing to compare it with."""
    pinned = item.artifact.digest
    if pinned is None:
        return
    repository, tag = item.artifact.repository, item.artifact.tag
    try:
        current = registry(host, repository, tag)
    except Exception as error:  # noqa: BLE001 - every failure reaches the summary
        item.fail("failed", f"reading {repository}:{tag} on {host}: {describe(error)}")
        return
    if current is None:
        log(f"{item.ref}: not on {host} yet")
        return
    item.repository, item.tag, item.digest = repository, tag, current
    if current == pinned:
        item.outcome = "published"
        log(f"{item.ref}: {host} serves the pinned {pinned}: published, not built or pushed")
        return
    other = "a new tag (-rN)" if item.artifact.kind == "media" else "the next revision"
    item.fail("refused", f"{host} already serves {current} under this tag, the manifest pins {pinned}; "
                         f"{never_moves(item)}: give it {other}")


def existing_outputs(recipe, checked, out_dir):
    """--no-build: every pinned output is already in OUT_DIR with its hash
    (check_outputs), or a Failure that says how to get them there."""
    try:
        check_outputs(checked, out_dir)
    except Failure as failure:
        raise Failure(f"--no-build: {failure}; build the recipe first (bash recipes/{recipe}/build.sh, "
                      "or this pipeline without --no-build)") from None


def push_all(cli, host, items, store, config, env, registry, environ):
    """Push every set and package, after an anonymous read of its target,
    when every one passed and is pinned; stop at the first refusal or
    failure. 1 unless every one ends on the registry. A `published` one is
    already there and is left alone."""
    if any(item.failed or item.outcome == "unpinned" for item in items):
        for item in items:
            if item.outcome == "dry run":
                item.outcome, item.detail = "held", "not pushed: another one failed or has no digest pin"
        return 1
    secrets = dict(env)
    secrets[USER_ENV] = environ[USER_ENV]
    secrets[PASSWORD_ENV] = environ[PASSWORD_ENV]
    secrets[HOST_ENV] = host
    status = 0
    for item in items:
        if item.outcome == "published":
            continue
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
    """One set or package: the anonymous read, the push and the read-back.
    Whatever goes wrong marks it; once upcli has pushed, a failed or other
    read-back marks it pushed but unconfirmed."""
    try:
        current = registry(host, item.repository, item.tag)
        if current == item.digest:
            item.outcome = "present"
            return
        if current is not None:
            item.fail("refused", f"the registry already holds {current} under this tag; {never_moves(item)}")
            return
        word, digest = push_artifact(cli, item, target, store, config, secrets)
    except Exception as error:  # noqa: BLE001 - every failure reaches the summary
        item.fail("failed", describe(error))
        return
    if digest != item.digest:
        item.fail("failed", f"upcli pushed {digest}, the {item.artifact.kind} digest is {item.digest}")
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
