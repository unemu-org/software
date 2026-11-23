# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build the media sets and firmware packages from their recipes with the
pinned unemu CLI (unemu) into a store, check them, and push them only when
asked.

    python3 -I lib/publish.py --cli UNEMU [--store DIR] [--fetch] [--library DIR]...
                              [--no-build] [--experimental RUN] [--skip-published]
                              [--push] [--report FILE] [RECIPE|FOLDER|REF]...

    --cli UNEMU         the unemu binary of the unemu release in
                        unemu.toml (lib/unemu.py fetch), or one built at its
                        commit (default: the UNEMU environment variable; a
                        name without a slash is looked up on PATH)
    --store DIR         build into DIR, a new directory kept afterwards
                        (default: a fresh store under build/, removed
                        afterwards); with --no-build, the existing store an
                        earlier run built
    --fetch             let `unemu recipe build` download a missing input
                        from the URLs its recipe pins (its consent)
    --library DIR       a folder where `unemu recipe build` looks for inputs
                        by size and SHA-256 (repeatable)
    --no-build          build nothing: every selected set and package must
                        already be in --store under its own tag, every blob
                        of it checked here against the digest; this is how
                        a CI job that holds credentials pushes what a job
                        without them built
    --experimental RUN  target <host>/unemu/experimental/media/ibm-pc/<work>:
                        <release>-ci<RUN>-rN (and its alias <release>-ci<RUN>)
                        and <host>/unemu/experimental/firmware/ibm-pc/<id>:
                        ci<RUN>-rN (RUN: the CI run's id, from 1) instead of
                        <host>/unemu/media/ibm-pc/<work>:<release>-rN (and
                        its alias <release>) and <host>/unemu/firmware/
                        ibm-pc/<id>:rN
    --skip-published    read each pinned set's and package's own tag on the
                        registry (unemu/media/... or unemu/firmware/...,
                        with --experimental too) anonymously first, and a
                        set's alias: one that already serves the pinned
                        digest under both is `published` and is neither
                        built nor pushed; one that serves another digest is
                        `refused` (with --experimental: noted, and built
                        and pushed to its experimental tags)
    --push              push every checked set and package; without it
                        nothing is written to any registry, and nothing is
                        read from one unless --skip-published asks
    --report FILE       also write the summary's rows to FILE as JSON
    RECIPE|FOLDER|REF   a recipe file (recipes/media/freedos/freedos-1.4.media.toml),
                        a folder of recipes (recipes/media/freedos), or one
                        set's or package's ref (unemu/media/ibm-pc/freedos:1.4-r1,
                        unemu/firmware/ibm-pc/<id>:r1); default: every open recipe

Private recipes are descriptions only. The default skips them; an explicit
selection including one is refused before any unemu or registry call.

A recipe (lib/recipes.py, docs/recipes.md) is a recipe file of unemu: one
set or package, its ref <repository>:<tag> from its identity table. Tags
follow unemupc's decision-38: a tag that ends in rN names one set or package
for ever and never moves; a set's release alone (1.4 of 1.4-r1) is an alias
that names its newest revision, and the pipeline pushes it after the
revision; a package tag names no release and has no alias. Sets and
packages have no `latest`. The steps, in order; a set or package that fails
one is not taken further:

 1. Every recipe file read (lib/recipes.py): its identity, and no ref or
    alias named twice. A refusal stops the run before anything else.
 2. The version check of unemu (below), then `unemu recipe check` over every
    recipe: a finding stops the run; drift is reported and is no refusal
    here, as a build writes the lock's record (CI refuses it,
    lib/recipes.py check).
 3. With --skip-published, for each selected set or package with a digest
    pin, an anonymous HTTPS read of its own tag on the registry
    (anonymous_digest, as before a push), whatever the target, and of a
    set's own alias once its tag serves the pin: a tag (and alias) that
    serves the pinned digest is `published` and goes no further; a tag or
    alias that serves another digest is `refused` (a revision tag never
    moves, an alias is never moved here, and no next revision is published
    for now: the maintainer decides); a missing tag, or a missing alias of
    a published tag, goes on and is pushed. A read that fails fails the set
    or package. A draft cannot be compared: it goes on, and ends
    `unpinned`. With --experimental, another digest under the official tag
    or alias refuses nothing: the set or package goes on to its
    experimental tags, and its row's detail notes what the official tag
    still serves, so a replacement can be tested before the maintainer
    deletes the old one.
 4. Each set or package built with `unemu recipe build FILE --store STORE`
    (with --fetch and --library as given), which checks every byte it makes
    against the recipe's lock and tags only the locked artifact; a draft is
    built as a draft. With --no-build nothing is built: the store (an OCI
    layout an earlier job built) must name the ref, and before unemu reads
    it, the manifest it names and every blob that manifest names are
    checked here against their digests and sizes and copied into a clean
    store, the only one unemu reads (take_from_store), and the
    manifest's digest against the recipe's pin. Then `unemu image media
    show` or `image firmware show` of the ref must name it with the digest
    and report it pushable to unemu/, and `unemu image push --dry-run`
    against the target, and a
    set's alias target, must say the guard allows it. All offline; the dry
    run checks no tag rule, which only a push does on the registry.
 5. The digest compared with the recipe's digest pin: another digest is a
    `mismatch`; a draft is `unpinned` and is never pushed. Either fails the
    run.
 6. With --push, and only when everything selected passed and is pinned:
    for each that is not `published`, an anonymous HTTPS read of the target
    tag, and of a set's target alias, on the registry first
    (anonymous_digest). Either naming another digest refuses the set or
    package before anything of it is pushed: a revision tag never moves, and
    an alias is never moved here. Then the missing ones are pushed with
    `unemu image push` (never --move), the revision first, then the alias,
    which unemu lets onto the newest revision the registry has; each is
    read back: a read-back that fails or names another digest leaves it
    `unconfirmed` (pushed, not confirmed). A set whose revision tag is on
    the registry with its digest but whose alias push or read-back fails is
    `partial`, and its detail names the confirmed revision tag. A tag or
    alias that already names the same digest is skipped. Any failure fails
    the run, and nothing after it is pushed; everything still gets its row
    in the summary.

A summary table (repository, tag, alias, digest, outcome, licence) goes to
stdout, progress to stderr. Exit status: 0 when everything selected passed
(and, with --push, is on the registry), 1 otherwise, 2 for a usage error.

The registry host is UNEMU_REGISTRY_HOST, checked as unemu checks one: a
name with a dot or a port, or localhost, the port at most 65535. A dry run
without it names the family default registry.unemu.dev, which it contacts
only for the anonymous reads of --skip-published; a push needs it set. A
push takes its credentials only from the environment, UNEMU_REGISTRY_USER
and UNEMU_REGISTRY_PASSWORD, and hands them to the unemu push alone,
through its environment: never on a command line, never to another unemu
command. unemu runs with an environment of its own (PATH, a fresh HOME and
TMPDIR in the run's work directory, LC_ALL=C, TZ=UTC) and a registries file
of its own: the target host, public, unemu/ only. A recipe is data: no step
of it starts a process, and every input it takes is checked by size and
SHA-256 before use.

The CLI pin is the unemu release in unemu.toml (lib/unemu.py). Before
anything is built, `unemu --version` runs once, and its commit line, the
one line `commit: <40 lower-case hex digits>`, must name the pinned commit
(unemu.check_cli): another commit, a malformed or repeated commit line, a
failed --version, or none at all, refuses the run. A release build prints
that line; a source build prints it only when built with
UNEMUPC_SOURCE_COMMIT set to the commit, and is refused without it. There
is no way around this check, for a dry run either.

Standard library only. Run it with -I; it finds lib/recipes.py and
lib/unemu.py next to itself.
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


recipes = _module("recipes")
unemu = _module("unemu")

CLI_ENV = "UNEMU"
USER_ENV = "UNEMU_REGISTRY_USER"
PASSWORD_ENV = "UNEMU_REGISTRY_PASSWORD"
HOST_ENV = "UNEMU_REGISTRY_HOST"
# unemu's family default registry (image_registry::reference::DEFAULT_REGISTRY).
DEFAULT_HOST = "registry.unemu.dev"
# Every repository lives in unemu/; an experimental target puts
# experimental/ after it (unemu/experimental/media/ibm-pc/<work>,
# unemu/experimental/firmware/ibm-pc/<id>), and ci<RUN> as the last part of
# its tag's release (<release>-ci<RUN>-rN, ci<RUN>-rN): a revision tag of
# its own per run, which never moves, and for a set the alias
# <release>-ci<RUN>, pushed as the official alias is.
OFFICIAL_PREFIX = "unemu/"
EXPERIMENTAL_PREFIX = "unemu/experimental/"
PIN_FILE = unemu.PIN_FILE
RUN = re.compile(r"[1-9][0-9]{0,19}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
# What `unemu image media show` and `unemu image firmware show` say last of
# a set or package the guard lets into unemu/.
PUSHABLE = "push\tany configured registry, including unemu/"
# What `unemu recipe build` says last: built or already built, the ref, the
# digest, and locked (a pinned recipe, built from its lock) or draft.
BUILT = re.compile(r"(?:already )?built ([^\t]+)\t(sha256:[0-9a-f]{64})\t(locked|draft)")
# The manifest types the anonymous read accepts: unemu pushes an OCI image
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
USER_AGENT = "unemu-software-publish"


class Failure(Exception):
    """A step that failed, and why."""


class RegistryError(Exception):
    """The registry could not be read, or answered something unexpected."""


@dataclasses.dataclass
class Item:
    """One selected set or package (a recipes.Recipe) and what became of
    it; REPOSITORY, TAG and ALIAS (a set's, None for a package) are its
    target's."""

    recipe: object
    repository: str
    tag: str
    alias: str | None = None
    outcome: str = "not checked"
    digest: str | None = None
    detail: str | None = None
    failed: bool = False

    @property
    def ref(self):
        """The set's or package's own ref, unemu/<kind>/ibm-pc/<name>:<tag>,
        which is also its tag in the store."""
        return self.recipe.ref

    @property
    def store_tag(self):
        return self.recipe.ref

    @property
    def kind(self):
        return self.recipe.kind

    @property
    def license(self):
        return self.recipe.license or "-"

    def tags(self):
        """The target's tags to push, in order: the revision tag, then a
        set's alias, which unemu moves onto the newest revision only."""
        return [self.tag] + ([self.alias] if self.alias is not None else [])

    def fail(self, outcome, detail):
        self.outcome, self.detail, self.failed = outcome, detail, True


def log(text):
    print(text, file=sys.stderr, flush=True)


# --- hosts --------------------------------------------------------------------


def registry_host(text):
    """TEXT as unemu takes a registry host (config::validate_registry_host
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
    """The unemupc release unemu.toml pins (unemu.read_pin)."""
    try:
        return unemu.read_pin(root / PIN_FILE)
    except unemu.Refusal as refusal:
        raise Failure(str(refusal)) from None


def run_cli(cli, args, env):
    """Run unemu; its output is UTF-8, and anything else is an error."""
    try:
        return subprocess.run([cli, *args], capture_output=True, encoding="utf-8", errors="strict",
                              env=env, check=False)
    except OSError as error:
        raise Failure(f"cannot run {cli}: {error.strerror or error}") from None
    except UnicodeDecodeError:
        raise Failure(f"unemu {' '.join(args[:2])}: output that is not UTF-8") from None


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


def check_recipes(cli, root, every, env):
    """`unemu recipe check` over every recipe file: a finding is a Failure.
    Drift is reported: a build writes the lock's record, whatever the work
    file says now."""
    if not every:
        log("no open recipes to check")
        return
    done = run_cli(cli, ["recipe", "check", *(str(root / r.path) for r in every)], env)
    for line in done.stdout.splitlines():
        if line.strip().startswith(recipes.DRIFT):
            log(f"warning: {line.strip()}")
    if done.returncode != 0:
        sys.stderr.write(done.stdout + done.stderr)
        raise Failure(f"unemu recipe check: {why(done)}")
    missing = recipes.not_passed(done.stdout, [root / r.path for r in every])
    if missing:
        raise Failure("unemu recipe check printed no ok line for " + ", ".join(str(m) for m in missing))
    log(last_line(done.stdout))


def build_item(cli, root, item, store, options, env):
    """`unemu recipe build` of the item's recipe into STORE; its digest and
    whether the recipe is locked (pinned) or a draft. Its report goes to
    stderr."""
    args = ["recipe", "build", str(root / item.recipe.path), "--store", str(store)]
    if options.fetch:
        args.append("--fetch")
    for library in options.library:
        args += ["--library", library]
    done = run_cli(cli, args, env)
    sys.stderr.write(done.stdout + done.stderr)
    if done.returncode != 0:
        raise Failure(f"recipe build: {why(done)}")
    found = BUILT.fullmatch(last_line(done.stdout))
    if not found or found.group(1) != item.ref:
        raise Failure(f"recipe build: unexpected last line {last_line(done.stdout)!r}; expected the "
                      f"built line of {item.ref}")
    return found.group(2), found.group(3)


def check_show(cli, item, digest, store, env):
    """`unemu image media show` or `image firmware show` of the item in
    STORE names it first, with DIGEST (None: any digest, which is then
    returned), and reports it pushable to unemu/ last."""
    done = run_cli(cli, ["image", item.kind, "show", item.store_tag, "--store", str(store)], env)
    if done.returncode != 0:
        raise Failure(f"{item.kind} show: {why(done)}")
    lines = done.stdout.splitlines()
    first = lines[0].split("\t") if lines else []
    if len(first) < 3 or first[0] != item.store_tag or not DIGEST.fullmatch(first[1]) \
            or digest not in (None, first[1]):
        raise Failure(f"{item.kind} show: the first line {lines[:1]} does not name {item.store_tag} "
                      f"{digest or ''}".rstrip())
    if lines[-1] != PUSHABLE:
        raise Failure(f"{item.kind} show: the last line is {lines[-1]!r}, not {PUSHABLE!r}")
    return first[1]


# What unemu 0.0.4 writes into a store's store.toml and images/oci-layout;
# a clean store (--no-build) is made of these, the index entries it pushes
# and the blobs they name, every one checked.
STORE_TOML = b"schema = 1\n"
OCI_LAYOUT = b'{"imageLayoutVersion":"1.0.0"}'


def copy_blob(source, clean, digest, size):
    """The blob DIGEST of the store SOURCE (an OCI layout, untrusted),
    copied into the store CLEAN as it is read and checked: a regular file
    inside SOURCE (no link), with that SHA-256 and SIZE. Only bytes that
    passed reach CLEAN. Its bytes when it is at most MAX_MANIFEST long,
    else None."""
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise Failure(f"the store names a blob {digest!r} that is not sha256:<64 hex digits>")
    if type(size) is not int or size < 0:
        raise Failure(f"the store gives the blob {digest} the size {size!r}")
    name = digest.split(":")[1]
    path = pathlib.Path(source) / "images" / "blobs" / "sha256" / name
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        raise Failure(f"the store lacks the blob {digest}") from None
    if not stat.S_ISREG(mode) or not os.path.realpath(path).startswith(os.path.realpath(source) + os.sep):
        raise Failure(f"the blob {digest} is not a regular file inside the store")
    target = pathlib.Path(clean) / "images" / "blobs" / "sha256" / name
    part = target.with_name(name + ".part")
    hashed, length, kept = hashlib.sha256(), 0, []
    with open(path, "rb") as handle, open(part, "wb") as out:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hashed.update(chunk)
            length += len(chunk)
            if length > size:
                break
            out.write(chunk)
            if length <= MAX_MANIFEST:
                kept.append(chunk)
    if length != size:
        part.unlink()
        raise Failure(f"the store's blob {digest} is not the {size} bytes its descriptor says")
    if hashed.hexdigest() != name:
        part.unlink()
        raise Failure(f"the store's blob {digest} holds other bytes (SHA-256 {hashed.hexdigest()})")
    os.replace(part, target)
    return b"".join(kept) if length <= MAX_MANIFEST else None


def clean_store(clean):
    """A new, empty store at CLEAN, as unemu 0.0.4 lays one out."""
    (pathlib.Path(clean) / "images" / "blobs" / "sha256").mkdir(parents=True)
    (pathlib.Path(clean) / "locks").mkdir()
    (pathlib.Path(clean) / "store.toml").write_bytes(STORE_TOML)
    (pathlib.Path(clean) / "images" / "oci-layout").write_bytes(OCI_LAYOUT)
    write_index(clean, [])


def write_index(clean, entries):
    index = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": entries}
    (pathlib.Path(clean) / "images" / "index.json").write_text(json.dumps(index, indent=2) + "\n",
                                                               encoding="utf-8")


def read_index(source):
    """The manifests the index of the store SOURCE lists (untrusted), read
    under copy_blob's rules: a regular file (no link) inside the store, at
    most MAX_MANIFEST bytes."""
    path = pathlib.Path(source) / "images" / "index.json"
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("not a regular file")
        if not os.path.realpath(path).startswith(os.path.realpath(source) + os.sep):
            raise OSError("not inside the store")
        if info.st_size > MAX_MANIFEST:
            raise ValueError(f"larger than {MAX_MANIFEST} bytes")
        with open(path, "rb") as handle:
            data = handle.read(MAX_MANIFEST + 1)
        if len(data) > MAX_MANIFEST:
            raise ValueError(f"larger than {MAX_MANIFEST} bytes")
        entries = json.loads(data, object_pairs_hook=_unique_keys)["manifests"]
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise ValueError("no list of manifests")
        return entries
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as error:
        raise Failure(f"the store's index cannot be read: {error}") from None


def take_from_store(source, clean, ref, pinned):
    """--no-build: the digest under which the store SOURCE (an OCI layout
    an earlier job built, untrusted here) names REF. When it is PINNED,
    REF is copied into the store CLEAN, where unemu
    reads it, and only what passed is copied: the manifest the index names
    under REF, with that digest, and every blob the manifest names, config
    and layers, each with its digest and size. So unemu reads no file of
    SOURCE, and the bytes a push uploads are the ones the pin names."""
    named = [entry for entry in read_index(source)
             if isinstance(entry.get("annotations"), dict)
             and entry["annotations"].get("org.opencontainers.image.ref.name") == ref]
    if len(named) != 1:
        raise Failure(f"the store names {ref} {len(named)} times, not once")
    digest, size = named[0].get("digest"), named[0].get("size")
    if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
        raise Failure(f"the store names {ref} with {digest!r}, not sha256:<64 hex digits>")
    if digest != pinned:
        return digest
    body = copy_blob(source, clean, digest, size)
    try:
        manifest = json.loads(body, object_pairs_hook=_unique_keys)
        media_type = manifest["mediaType"]
        blobs = [(d["digest"], d["size"]) for d in [manifest["config"], *manifest["layers"]]]
        if not isinstance(media_type, str):
            raise ValueError("no media type")
    except (ValueError, KeyError, TypeError, RecursionError) as error:
        raise Failure(f"the manifest {digest} of {ref} cannot be read: {error}") from None
    for blob, length in blobs:
        copy_blob(source, clean, blob, length)
    entries = read_index(clean)
    entries.append({"mediaType": media_type, "digest": digest, "size": size,
                    "annotations": {"org.opencontainers.image.ref.name": ref}})
    write_index(clean, entries)
    return digest


def dry_run(cli, item, target, store, config, digest, env):
    args = ["image", "push", item.store_tag, target, "--store", str(store), "--dry-run", "--config", str(config)]
    done = run_cli(cli, args, env)
    lines = done.stdout.splitlines()
    if done.returncode != 0 or "guard:allowed" not in lines:
        raise Failure(f"push --dry-run {target}: {why(done)}")
    if f"target:{target}" not in lines or f"image:{digest}" not in lines:
        raise Failure(f"push --dry-run {target}: the plan names another target or image")


def push_artifact(cli, item, target, store, config, env):
    """`unemu image push`, never with --move; what it did and the digest it
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
        description="Build, check and (with --push) publish the media sets and firmware packages.",
    )
    parser.add_argument("--cli", default=environ.get(CLI_ENV),
                        help=f"the unemu binary (default: ${CLI_ENV})")
    parser.add_argument("--store", help="a new directory to build into, kept afterwards; with --no-build, "
                                        "the store an earlier run built")
    parser.add_argument("--fetch", action="store_true",
                        help="let unemu recipe build download missing inputs from their pinned URLs")
    parser.add_argument("--library", action="append", default=[], metavar="DIR",
                        help="a folder where unemu recipe build looks for inputs (repeatable)")
    parser.add_argument("--no-build", action="store_true",
                        help="build nothing: every selected set and package must be in --store already")
    parser.add_argument("--experimental", metavar="RUN",
                        help="target unemu/experimental/media/ibm-pc/<work>:<release>-ci<RUN>-rN and "
                             "unemu/experimental/firmware/ibm-pc/<id>:ci<RUN>-rN")
    parser.add_argument("--skip-published", action="store_true",
                        help="read each pinned set's and package's own tag (and a set's alias) anonymously "
                             "first; one that already serves the pinned digest is neither built nor pushed")
    parser.add_argument("--push", action="store_true", help="push the checked sets and packages")
    parser.add_argument("--report", metavar="FILE", help="also write the summary's rows to FILE as JSON")
    parser.add_argument("selectors", nargs="*", metavar="RECIPE|FOLDER|REF")
    options = parser.parse_args(argv)
    if not options.cli:
        parser.error(f"--cli UNEMU (or ${CLI_ENV}) is required")
    if options.experimental is not None and not RUN.fullmatch(options.experimental):
        parser.error("--experimental RUN takes the CI run id: 1 to 20 digits, not starting with 0")
    if options.no_build:
        if options.store is None:
            parser.error("--no-build needs --store DIR, the store an earlier run built")
        if options.fetch or options.library:
            parser.error("--no-build builds nothing: --fetch and --library are for a build")
    for library in options.library:
        if not os.path.isdir(library):
            parser.error(f"--library {library}: not a folder")
    options.library = [os.path.abspath(library) for library in options.library]
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
    """Read every recipe, but pass only open ones to unemu.

    An explicit selector that includes a private recipe is refused before
    any unemu invocation, registry read or store access.
    """
    log("== read the recipes")
    try:
        every = recipes.load(root)
    except recipes.Refusal as refusal:
        raise Failure(str(refusal)) from None
    except OSError as error:
        raise Failure(f"cannot read the recipes: {error}") from None
    if not every:
        raise Failure(f"no recipes in {root / recipes.RECIPES}")
    for recipe in every:
        log(f"{recipe.path}: {recipe.ref}{' (private: static check only)' if recipe.private else ''}"
            f"{'' if recipe.digest else ' (a draft)'}")
    open_recipes = [r for r in every if not r.private]
    if not selectors:
        chosen = open_recipes
    else:
        wanted = set()
        for selector in selectors:
            path = selector.rstrip("/")
            matching = [r for r in every if selector == r.ref or path == r.path or r.path.startswith(path + "/")]
            if not path or not matching:
                parser.error(f"{selector!r} is neither a recipe, a folder of recipes nor a ref "
                             "(unemu/media/ibm-pc/<work>:<release>-rN or unemu/firmware/ibm-pc/<id>:rN)")
            private = [r.path for r in matching if r.private]
            if private:
                parser.error("private recipes are static check only; never built, fetched or published: "
                             + ", ".join(private))
            wanted.update(r.path for r in matching)
        chosen = [r for r in open_recipes if r.path in wanted]
    return open_recipes, [Item(r, "", "") for r in chosen]


def experimental_tag(tag, run):
    """The revision tag TAG (recipes.parse_revision) of run RUN: ci<RUN> as
    the last part of its release, <release>-ci<RUN>-rN or ci<RUN>-rN; and
    that release, which is the alias of a set's experimental tag."""
    parsed = recipes.parse_revision(tag)
    if parsed is None:
        raise Failure(f"the tag {tag!r} is not a revision tag")
    release, number = parsed
    release = f"ci{run}" if release is None else f"{release}-ci{run}"
    return f"{release}-r{number}", release


def set_target(item, experimental):
    """Set the item's target repository, tag and alias: its own, or with
    EXPERIMENTAL (a run id) unemu/experimental/<kind>/ibm-pc/<name>:
    <release>-ci<RUN>-rN (experimental_tag), with the alias
    <release>-ci<RUN> for a set. A Failure when the experimental repository
    or tag is longer than a repository name or tag may be: the recipes keep
    their own within the limits, not the experimental one."""
    recipe = item.recipe
    if experimental is None:
        item.repository, item.tag, item.alias = recipe.repository, recipe.tag, recipe.alias
        return
    if not recipe.repository.startswith(OFFICIAL_PREFIX):
        raise Failure(f"{recipe.repository} is outside {OFFICIAL_PREFIX}")
    item.repository = EXPERIMENTAL_PREFIX + recipe.repository[len(OFFICIAL_PREFIX):]
    item.tag, release = experimental_tag(recipe.tag, experimental)
    item.alias = release if recipe.alias is not None else None
    if len(item.repository) > recipes.MAX_REPOSITORY:
        raise Failure(
            f"the experimental repository {item.repository} of {item.ref} is "
            f"{len(item.repository)} bytes, longer than {recipes.MAX_REPOSITORY}"
        )
    if not recipes.TAG.fullmatch(item.tag):
        raise Failure(f"the target tag {item.tag!r} is longer than 128 characters")


def rows(items):
    return [{
        "ref": item.ref,
        "recipe": item.recipe.path,
        "repository": item.repository,
        "tag": item.tag,
        "alias": item.alias,
        "digest": item.digest,
        "outcome": item.outcome,
        "licence": item.license,
        "detail": item.detail,
    } for item in items]


def summary(items, host, mode):
    table = [("REPOSITORY", "TAG", "ALIAS", "DIGEST", "OUTCOME", "LICENCE")]
    for item in items:
        table.append((item.repository, item.tag, item.alias or "-", item.digest or "-", item.outcome,
                      item.license))
    widths = [max(len(row[i]) for row in table) for i in range(5)]
    print(f"registry {host} ({mode})")
    for row in table:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row[:5])) + "  " + row[5])
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
            if options.no_build and not store_arg.is_dir():
                parser.error(f"--store {options.store}: --no-build needs the store an earlier run built")
            if not options.no_build and os.path.lexists(store_arg):
                parser.error(f"--store {options.store}: it exists; give a new directory")
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2

    try:
        pin = read_pin(root)
        every, items = select(parser, root, options.selectors)
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
        status = run(options, root, cli, pin, host, every, items, work, store_arg, registry, environ)
    finally:
        remove_tree(work)
    if options.report:
        try:
            pathlib.Path(options.report).write_text(json.dumps(rows(items), indent=1) + "\n", encoding="utf-8")
        except OSError as error:
            log(f"cannot write the report {options.report}: {error}")
            return 1
    return status


def run(options, root, cli, pin, host, every, items, work, store_arg, registry, environ):
    # With --no-build, unemu reads a clean store of its own, into which only
    # checked bytes of the given store are copied (take_from_store).
    store = work / "store" if options.no_build or store_arg is None else store_arg
    if options.no_build:
        clean_store(store)
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
    log(f"unemu {cli}")
    log(f"unemu pin {pin.commit} (unemu {pin.tag}, {PIN_FILE})")
    try:
        unemu.check_cli(cli, pin.commit, env)
    except unemu.Refusal as refusal:
        log(f"refused: {refusal}")
        return 1
    log(f"unemu --version reports commit {pin.commit}, as pinned")
    log("== check the recipes")
    try:
        check_recipes(cli, root, every, env)
    except Failure as failure:
        log(f"refused: {failure}")
        return 1
    log(f"store {store}")

    if options.skip_published:
        log(f"== read the published tags on {host}")
        for item in items:
            check_published(host, item, registry, experimental=options.experimental is not None)

    for item in items:
        if item.outcome != "not checked":
            if item.outcome == "published":
                log(f"== skip {item.ref}: published, not built")
            continue
        reference = f"{host}/{item.repository}:{item.tag}"
        if item.alias is not None:
            reference += f" (alias {item.alias})"
        try:
            if options.no_build:
                pinned = item.recipe.digest
                if pinned is None:
                    # Nothing of a draft is read from the store, so no byte
                    # without a pin reaches unemu where it can push.
                    item.fail("unpinned", "a draft: the recipe has no digest pin (unemu recipe pin); never "
                                          "taken from the store or pushed")
                    log(f"{item.ref}: {item.outcome}")
                    continue
                log(f"== take {item.ref} from the store (--no-build)")
                item.digest = take_from_store(store_arg, store, item.ref, pinned)
                if item.digest != pinned:
                    item.fail("mismatch", f"the store holds {item.digest} under {item.ref}, the recipe pins {pinned}")
                    log(f"{item.ref}: {item.outcome}")
                    continue
                check_show(cli, item, item.digest, store, env)
                state = "locked"
            else:
                log(f"== build {item.recipe.path}")
                item.digest, state = build_item(cli, root, item, store, options, env)
                check_show(cli, item, item.digest, store, env)
            for tag in item.tags():
                dry_run(cli, item, f"{host}/{item.repository}:{tag}", store, config, item.digest, env)
        except Exception as error:  # noqa: BLE001 - every failure reaches the summary
            item.fail("failed", describe(error))
            continue
        pinned = item.recipe.digest
        if pinned is None or state == "draft":
            item.fail("unpinned", "a draft: the recipe has no digest pin (unemu recipe pin); never pushed")
        elif pinned != item.digest:
            item.fail("mismatch", f"unemu gives {item.digest}, the recipe pins {pinned}")
        else:
            item.outcome = "dry run"
        log(f"{item.ref}: {item.digest} -> {reference}: {item.outcome}")

    status = 1 if any(item.failed for item in items) else 0
    if options.push:
        status = push_all(cli, host, items, store, config, env, registry, environ) or status
    summary(items, host, mode)
    return status


# Why a tag or alias that names another digest is the end of the road. No
# next revision is advised: none is published before unemupc's first public
# release (its decision-32, point 5), and moving a set's alias onto a newer
# revision, which takes unemu's --move, is not supported here.
NEVER_MOVES = "a revision tag never moves: stop and ask the maintainer"
ALIAS_STAYS = "this pipeline never moves an alias: stop and ask the maintainer"


def check_published(host, item, registry, experimental=False):
    """--skip-published: the anonymous read of the item's own tag (on
    unemu/media/ or unemu/firmware/, whatever the target), then of a set's
    own alias. The pinned digest under both makes it `published`, another
    digest under either `refused`; a missing tag, or the missing alias of a
    tag that serves the pin, leaves it to be built and pushed. The row then
    names the tag that was read. A draft is not read: nothing to compare it
    with.

    With EXPERIMENTAL (the push goes to unemu/experimental/ only) another
    digest under the official tag or alias refuses nothing: the set or
    package goes on, to be built and pushed to its experimental tags, and
    its detail says what the official tag still serves. So a replacement
    can be tested before the maintainer deletes what it replaces."""
    pinned = item.recipe.digest
    if pinned is None:
        return
    repository, tag, alias = item.recipe.repository, item.recipe.tag, item.recipe.alias
    served = {}
    for name in [tag] + ([alias] if alias is not None else []):
        try:
            served[name] = registry(host, repository, name)
        except Exception as error:  # noqa: BLE001 - every failure reaches the summary
            item.fail("failed", f"reading {repository}:{name} on {host}: {describe(error)}")
            return
        if served[name] != pinned:
            break
    current = served[tag]
    if current is None:
        log(f"{item.ref}: not on {host} yet")
        return
    if current != pinned:
        if experimental:
            item.detail = (f"note: {host}/{repository}:{tag} still serves {current}, the recipe pins {pinned}; "
                           "replacing it needs the maintainer (delete its tag and alias, then publish)")
            log(f"{item.ref}: {item.detail}")
            return
        item.repository, item.tag, item.alias, item.digest = repository, tag, alias, current
        item.fail("refused", f"{host} already serves {current} under this tag, the recipe pins {pinned}; "
                             f"{NEVER_MOVES}")
        return
    if alias is not None and served[alias] is None:
        log(f"{item.ref}: {host} serves the pinned {pinned}, but not under its alias {alias} yet: "
            "built and pushed again to add the alias")
        return
    if alias is not None and served[alias] != pinned:
        if experimental:
            item.detail = (f"note: {host}/{repository}:{alias}, the alias, still names {served[alias]}, the "
                           f"recipe pins {pinned}; moving it needs the maintainer")
            log(f"{item.ref}: {item.detail}")
            return
        item.repository, item.tag, item.alias, item.digest = repository, tag, alias, current
        item.fail("refused", f"{host} serves the pinned {pinned} under this tag, but its alias {alias} names "
                             f"{served[alias]}; {ALIAS_STAYS}")
        return
    item.repository, item.tag, item.alias, item.digest = repository, tag, alias, current
    item.outcome = "published"
    log(f"{item.ref}: {host} serves the pinned {pinned}: published, not built or pushed")


def push_all(cli, host, items, store, config, env, registry, environ):
    """Push every set and package, after an anonymous read of its targets,
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
        push_one(cli, host, item, store, config, secrets, registry)
        if item.failed:
            status = 1
    return status


def push_one(cli, host, item, store, config, secrets, registry):
    """One set or package: the anonymous read of its tag and of a set's
    alias, then for each that is missing, the revision first, the push and
    the read-back. Another digest under either refuses it before anything
    is pushed. Whatever goes wrong marks it; once unemu has pushed, a failed
    or other read-back marks it pushed but unconfirmed. When the revision
    tag is on the registry with the digest (read back, or there already)
    but its alias step fails, it is `partial`, and the detail says so."""
    try:
        current = {name: registry(host, item.repository, name) for name in item.tags()}
    except Exception as error:  # noqa: BLE001 - every failure reaches the summary
        item.fail("failed", describe(error))
        return
    for name, digest in current.items():
        if digest not in (None, item.digest):
            if name == item.tag:
                item.fail("refused", f"the registry already holds {digest} under this tag; {NEVER_MOVES}")
            else:
                item.fail("refused", f"the registry's alias {name} names {digest}; {ALIAS_STAYS}")
            return
    # The tags the registry serves with the digest, confirmed by a read.
    confirmed = [name for name in item.tags() if current[name] == item.digest]
    pushed = False
    for name in item.tags():
        if name in confirmed:
            continue

        def fail(outcome, detail, name=name):
            if item.tag in confirmed:
                outcome = "partial"
                detail += (f"; the revision tag {item.tag} is on the registry with {item.digest}, "
                           f"its alias {name} is not confirmed")
            item.fail(outcome, detail)

        target = f"{host}/{item.repository}:{name}"
        log(f"== push {target}")
        try:
            word, digest = push_artifact(cli, item, target, store, config, secrets)
        except Exception as error:  # noqa: BLE001 - every failure reaches the summary
            fail("failed", describe(error))
            return
        if digest != item.digest:
            fail("failed", f"unemu pushed {digest} to :{name}, the {item.kind} digest is {item.digest}")
            return
        try:
            served = registry(host, item.repository, name)
        except Exception as error:  # noqa: BLE001 - every failure reaches the summary
            fail("unconfirmed", f"unemu {word} {digest} to :{name}; the read-back failed: {describe(error)}")
            return
        if served != item.digest:
            fail("unconfirmed", f"unemu {word} {digest} to :{name}; the registry serves {served}")
            return
        confirmed.append(name)
        pushed = pushed or word == "pushed"
    item.outcome = "pushed" if pushed else "present"


if __name__ == "__main__":
    sys.exit(main())
