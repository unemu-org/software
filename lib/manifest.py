# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check a recipe's manifest and print what the shell needs from it.

    python3 -I lib/manifest.py check MANIFEST   validate; exit 1 on a refusal;
                                                warnings on stderr
    python3 -I lib/manifest.py pins MANIFEST    "PATH SHA256" per pinned output
    python3 -I lib/manifest.py refs MANIFEST... every unemu/media/<work>:<tag>
                                                and unemu/firmware/<id>:
                                                <revision>; exit 1 if two of
                                                the manifests share one
    python3 -I lib/manifest.py sets MANIFEST OUT_DIR
                                                every set or package as JSON:
                                                its kind, ref, store tag,
                                                licence, digest pin and the
                                                upcli import arguments, the
                                                outputs found in OUT_DIR

A recipe has one manifest of one of two kinds: a set manifest (media.toml)
for installation media sets, or a package manifest (firmware.toml) for one
open firmware package; see "Firmware packages" below. The file name says
the kind: every command refuses another name, and a file that holds the
other kind's tables.

A set manifest (recipes/<recipe>/media.toml) describes every installation
set a recipe feeds, as unemupc98's `image media import` takes them
(media_import::Request and OpenStatement, built as its template code builds
them: MediaRelease, MediaSource and media_request in
crates/pc-image/src/template.rs). Every set of a manifest is one work, the
repository unemu/media/<work>, each under its own derived tag.

    [release]                 what every set of the release shares
    work                      the repository name under unemu/media/: a short,
                              stable, lower-case slug
    version                   the upstream release version (1.4), lower case,
                              no '-'
    default_format            the upstream release's own format (an archive
                              without disks: 1.44M); a set in another format
                              names it in its tag
    edition_slug              optional: the edition in the tag, absent for the
                              work's primary edition
    edition                   optional: the edition as the title record states
                              it (Floppy Edition, Shareware)
    series, sort_title        optional display fields (not imported yet)
    publishers, developers    non-empty lists
    year                      release year, 1 to 9999
    genres                    non-empty list
    platform                  one of unemupc98's software platforms ("DOS")
    labels                    optional: the sets' labels, lower case, from the
                              vocabulary below plus free words
    license                   SPDX expression, or one LicenseRef- for a
                              vendor's own terms
    terms                     file name of the extra holding the licence text
    source                    file name of the extra holding the corresponding
                              source; required unless license is one
                              LicenseRef-

    [[release.extra]]         path, sha256, dos_name (DOS 8.3), kind (document,
                              archive, executable or other: the kind the import
                              infers from the file name's extension)

    [[set]]                   title (the work's title only), description,
                              format (1.44M, 1.2M, 720K, 360K or iso-2048),
                              revision (optional, from 1: a corrected set),
                              digest (optional, "sha256:<64 hex>": the set
                              digest the pinned upcli gives; lib/publish.py
                              refuses another and never pushes a set
                              without one)
    [[set.disk]]              path, sha256, label; in installer order

The tag is derived, never written: VERSION[-EDITION_SLUG][-FORMAT][-rN], the
format only when it is not the default format, -rN only from revision 2.
Paths are relative to out/<recipe>/. The output pins live only here, so the
recipe's fast path, its install step and the import read one source of
truth.

Firmware packages. A package manifest (recipes/<recipe>/firmware.toml)
describes one open firmware package of unemupc's firmware catalogue, the
repository unemu/firmware/<catalogue id>, as unemupc's `image firmware
import --for` takes it (firmware_import::Statement, built as its template
build builds it: FirmwareSource::statement in
crates/pc-image/src/template.rs):

    [package]
    catalogue_id              the catalogue entry the ROM is (unemupc
                              crates/assembly/src/firmware.rs); also the
                              repository name under unemu/firmware/
    revision                  the package revision, the registry tag: a whole
                              number from 1, raised only when the statement
                              or the metadata changes for the same ROM bytes
    digest                    optional, "sha256:<64 hex>": the package digest
                              the pinned upcli gives; lib/publish.py refuses
                              another and never pushes a package without one
    title, vendor, version    1 to 128 characters each
    description               up to 4096 characters, one line
    labels                    non-empty list of lower-case ids (letters,
                              digits, '.', '_' and '-', at most 64 bytes)
    license                   the SPDX expression the catalogue records for
                              the ROM; the import states it (--open
                              --license), and upcli refuses another

    [rom]                     path, sha256: the ROM image
    [source]                  path (a .tar or .zip), sha256: its corresponding
                              source archive, the one the catalogue pins

The ref is unemu/firmware/<catalogue id>:<revision>. upcli tags an imported
package firmware/<catalogue id>:1 in its store, whatever the revision, so a
catalogue id has one manifest: a new revision replaces the old one in it
(the old revision's tag stays on the registry and never moves).

Standard library only (tomllib, Python 3.11 or later). Run it with -I, so no
PYTHON* variable, user site directory or file next to this one changes what
it does. It reads the manifest and nothing else; it never opens the outputs.
"""

import dataclasses
import json
import os.path
import re
import sys
import tomllib

NAMESPACE = "unemu/media"
FIRMWARE_NAMESPACE = "unemu/firmware"
# A manifest's kind, by its file name; and the top-level tables of each kind.
MANIFEST_NAMES = {"media.toml": "media", "firmware.toml": "firmware"}
SET_TABLES = ("release", "set")
PACKAGE_TABLES = ("package", "rom", "source")
# The store tag `upcli image firmware import` gives a package
# (firmware_import::Candidate::tag): always revision 1.
FIRMWARE_STORE_TAG = "firmware/{}:1"
# A firmware label, as image_store::media_set::valid_id takes one.
FIRMWARE_LABEL = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
MAX_FIRMWARE_LABELS = 64
# image_store::firmware::valid_long_text: a description's limit.
MAX_FIRMWARE_DESCRIPTION = 4096
# The archives upcli takes as a package's source (image_store::firmware::
# ArchiveFormat).
SOURCE_ARCHIVE_SUFFIXES = (".tar", ".zip")

# unemupc98: assembly::descriptors::FLOPPY_TYPES, then "iso-2048"; with the
# slug a set's tag gives each.
FORMAT_SLUGS = {"1.44M": "1.44m", "1.2M": "1.2m", "720K": "720k", "360K": "360k", "iso-2048": "iso"}
FORMATS = tuple(FORMAT_SLUGS)
# image_store::media_set::ExtraKind.
KINDS = ("document", "archive", "executable", "other")
# image_store::metadata::SOFTWARE_PLATFORMS.
PLATFORMS = (
    "DOS",
    "Windows 3.x",
    "Windows 9x",
    "CP/M",
    "MSX",
    "ZX Spectrum",
    "C64",
    "Atari 8-bit",
    "Atari ST",
    "other",
)
# The label vocabulary of the naming scheme: every set names its kind and
# its licence class (a warning otherwise), and may add free words.
LABEL_KINDS = ("game", "operating-system", "application", "utility", "development", "driver")
LABEL_LICENCE_CLASSES = ("open-source", "freeware", "shareware", "public-domain")

SHA256 = re.compile(r"[0-9a-f]{64}")
# An OCI digest as upcli prints a set's: the manifest's SHA-256.
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
# An OCI distribution path component (unemupc98 image_store::store).
REPOSITORY = re.compile(r"[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*")
MAX_REPOSITORY = 255
# A tag's parts: '-' separates them, so neither the version nor the edition
# slug holds one; an edition slug starts with a letter and is never rN, so a
# tag reads back one way only.
VERSION = re.compile(r"[0-9](?:[0-9a-z.]*[0-9a-z])?")
EDITION_SLUG = re.compile(r"[a-z][a-z0-9]*")
REVISION_LIKE = re.compile(r"r[0-9]+")
# unemupc98 image_store::store, a local tag: at most 128 bytes.
TAG = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
# A path component: letters, digits or '_' first (so no ".", "..", hidden
# file or option-like name), and no character a shell would split or expand;
# the pins are printed for the shell.
PATH_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._+-]*")
DOS_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&'()-@^_`{}~")
# Rust's char::is_whitespace (Unicode White_Space): what unemupc98's CLI
# trims and its licence parser splits on.
WHITESPACE = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u0085\u00a0"
    "\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f"
    "\u3000"
)

# The licence rules of unemupc98's image_store::license (license.rs at
# 4f73f3d): the closed lists of identifiers and exceptions it accepts
# besides LicenseRef-<idstring>, at most 1024 bytes of input and 256 bytes
# of canonical expression.
LICENSES = frozenset((
    "0BSD", "AFL-3.0", "AGPL-1.0-only", "AGPL-1.0-or-later", "AGPL-3.0-only",
    "AGPL-3.0-or-later", "Apache-1.1", "Apache-2.0", "Artistic-1.0", "Artistic-2.0",
    "BSD-1-Clause", "BSD-2-Clause", "BSD-2-Clause-Patent", "BSD-3-Clause",
    "BSD-3-Clause-Clear", "BSD-4-Clause", "BSL-1.0", "CC-BY-3.0", "CC-BY-4.0",
    "CC-BY-SA-3.0", "CC-BY-SA-4.0", "CC0-1.0", "CDDL-1.0", "CDDL-1.1", "EPL-1.0",
    "EPL-2.0", "EUPL-1.1", "EUPL-1.2", "GFDL-1.1-only", "GFDL-1.1-or-later",
    "GFDL-1.2-only", "GFDL-1.2-or-later", "GFDL-1.3-only", "GFDL-1.3-or-later",
    "GPL-1.0-only", "GPL-1.0-or-later", "GPL-2.0-only", "GPL-2.0-or-later",
    "GPL-3.0-only", "GPL-3.0-or-later", "Info-ZIP", "ISC", "LGPL-2.0-only",
    "LGPL-2.0-or-later", "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only",
    "LGPL-3.0-or-later", "MIT", "MIT-0", "MPL-1.0", "MPL-1.1", "MPL-2.0",
    "MPL-2.0-no-copyleft-exception", "NCSA", "OFL-1.1", "Unlicense", "UPL-1.0",
    "Watcom-1.0", "WTFPL", "X11", "Zlib",
))
EXCEPTIONS = frozenset((
    "Autoconf-exception-2.0", "Autoconf-exception-3.0", "Bison-exception-2.2",
    "Classpath-exception-2.0", "eCos-exception-2.0", "Font-exception-2.0",
    "GCC-exception-2.0", "GCC-exception-3.1", "LLVM-exception", "Linux-syscall-note",
))
LICENSE_REF = re.compile(r"LicenseRef-[A-Za-z0-9.-]+")
MAX_LICENSE_INPUT = 4 * 256
MAX_LICENSE_CANONICAL = 256
MAX_LICENSE_NESTING = 32
# A conservative check, not a licence judgement: any identifier of these
# families counts as copyleft, so the set must carry its source archive
# (unemupc98 names GPL, LGPL, AGPL and MPL; GFDL and EPL are added here).
COPYLEFT_PREFIXES = ("GPL-", "LGPL-", "AGPL-", "MPL-", "GFDL-", "EPL-")


class Refusal(Exception):
    """A manifest that must not be built from or imported, and why."""


@dataclasses.dataclass(frozen=True)
class Extra:
    path: str
    sha256: str
    dos_name: str
    kind: str


@dataclasses.dataclass(frozen=True)
class Disk:
    path: str
    sha256: str
    label: str


@dataclasses.dataclass(frozen=True)
class MediaSet:
    tag: str
    title: str
    description: str
    format: str
    revision: int
    disks: tuple
    # The set digest pin, "sha256:<hex>", or None.
    digest: str | None = None


@dataclasses.dataclass(frozen=True)
class Release:
    work: str
    version: str
    default_format: str
    edition_slug: str | None
    edition: str | None
    series: str | None
    sort_title: str | None
    publishers: tuple
    developers: tuple
    year: int
    genres: tuple
    platform: str
    labels: tuple
    license: str
    terms: str
    source: str | None
    extras: tuple

    @property
    def repository(self):
        return f"{NAMESPACE}/{self.work}"


@dataclasses.dataclass(frozen=True)
class Manifest:
    release: Release
    sets: tuple
    # What `check` reports without refusing.
    warnings: tuple = ()


# What unemupc98's import takes, field for field (media_import::Request,
# Source, ExtraSource, OpenStatement and image_store::metadata::Title).
@dataclasses.dataclass(frozen=True)
class Title:
    title: str
    description: str
    publishers: tuple
    developers: tuple
    release_year: int
    genres: tuple
    platform: str
    version: str
    edition: str | None


@dataclasses.dataclass(frozen=True)
class Source:
    path: str
    label: str
    format: str


@dataclasses.dataclass(frozen=True)
class ExtraSource:
    path: str
    dos_name: str
    kind: str


@dataclasses.dataclass(frozen=True)
class Request:
    tag: str
    title: Title
    labels: tuple
    media: tuple
    extras: tuple
    compress: bool


@dataclasses.dataclass(frozen=True)
class OpenStatement:
    license: str
    terms: str
    source: str | None


@dataclasses.dataclass(frozen=True)
class Output:
    """A pinned output file: its path in out/<recipe>/ and SHA-256."""

    path: str
    sha256: str


@dataclasses.dataclass(frozen=True)
class Package:
    """What a package manifest states: the catalogue id, the revision, the
    digest pin and the fields firmware_import::Statement takes."""

    catalogue_id: str
    revision: int
    title: str
    description: str
    vendor: str
    version: str
    labels: tuple
    license: str
    # The package digest pin, "sha256:<hex>", or None.
    digest: str | None = None

    @property
    def repository(self):
        return f"{FIRMWARE_NAMESPACE}/{self.catalogue_id}"

    @property
    def tag(self):
        return str(self.revision)

    @property
    def store_tag(self):
        return FIRMWARE_STORE_TAG.format(self.catalogue_id)


@dataclasses.dataclass(frozen=True)
class FirmwareManifest:
    package: Package
    rom: Output
    source: Output
    warnings: tuple = ()


@dataclasses.dataclass(frozen=True)
class Artifact:
    """One set or package as the pipeline handles it: its kind ("media" or
    "firmware"), its ref (repository:tag), the tag upcli's import writes in
    the store, the licence expression, the digest pin (or None) and the
    upcli import arguments (without --store and --create)."""

    kind: str
    ref: str
    repository: str
    tag: str
    store_tag: str
    license: str
    digest: str | None
    arguments: tuple


# --- field checks -----------------------------------------------------------


def fields(value, where, required, optional=()):
    """A table with exactly the known keys and every required one."""
    if not isinstance(value, dict):
        raise Refusal(f"{where}: expected a table")
    for key in value:
        if key not in required and key not in optional:
            raise Refusal(f"{where}: unknown key {key!r}")
    for key in required:
        if key not in value:
            raise Refusal(f"{where}: missing required key {key!r}")
    return value


def tables(value, where):
    """A non-empty array of tables."""
    if not isinstance(value, list) or not value:
        raise Refusal(f"{where}: expected a non-empty array of tables")
    for item in value:
        if not isinstance(item, dict):
            raise Refusal(f"{where}: expected a non-empty array of tables")
    return value


def string(value, where):
    if not isinstance(value, str):
        raise Refusal(f"{where}: expected a string")
    return value


def no_controls(text):
    return not any(c <= "\x1f" or c == "\x7f" for c in text)


def valid_text(text):
    """image_store::media_set::valid_text: bounded text without controls."""
    return bool(text.strip(WHITESPACE)) and len(text) <= 128 and no_controls(text)


def as_option(value, where):
    """A value unemupc98's CLI takes as an option value: it trims it (so the
    manifest must hold it trimmed, or the import would store other text)
    and refuses one that starts with '--'."""
    if value != value.strip(WHITESPACE):
        raise Refusal(f"{where}: {value!r} starts or ends with white space")
    if value.startswith("--"):
        raise Refusal(f"{where}: {value!r} starts with '--', which the import takes for an option")
    return value


def text(value, where):
    value = string(value, where)
    if not valid_text(value):
        raise Refusal(f"{where}: expected 1 to 128 characters without control characters")
    return as_option(value, where)


def long_text(value, where):
    value = string(value, where)
    if not value.strip(WHITESPACE) or not no_controls(value):
        raise Refusal(f"{where}: expected non-empty text without control characters")
    return as_option(value, where)


def text_list(value, where, non_empty):
    if not isinstance(value, list) or (non_empty and not value):
        kind = "a non-empty list" if non_empty else "a list"
        raise Refusal(f"{where}: expected {kind} of strings")
    items = tuple(text(item, f"{where}[{i}]") for i, item in enumerate(value, 1))
    if len(set(items)) != len(items):
        raise Refusal(f"{where}: duplicate entry")
    return items


def optional_text(value, key, where):
    return text(value[key], f"{where}.{key}") if key in value else None


def choice(value, where, choices):
    value = string(value, where)
    if value not in choices:
        raise Refusal(
            f"{where}: {value!r} is not one of {', '.join(repr(c) for c in choices)}"
        )
    return value


def slug(value, where, pattern, what):
    value = string(value, where)
    if not pattern.fullmatch(value):
        raise Refusal(f"{where}: {value!r} is not {what}")
    return value


def sha256(value, where):
    value = string(value, where)
    if not SHA256.fullmatch(value):
        raise Refusal(f"{where}: expected 64 lowercase hexadecimal digits")
    return value


def out_path(value, where):
    """A path that stays inside out/<recipe>/ and survives the shell."""
    value = string(value, where)
    if not all(PATH_PART.fullmatch(part) for part in value.split("/")):
        raise Refusal(
            f"{where}: {value!r} is not a path inside out/<recipe>/: use relative "
            "components of letters, digits, '.', '_', '+' and '-', each starting "
            "with a letter, a digit or '_'"
        )
    return value


def file_name(path):
    return path.rsplit("/", 1)[-1]


def valid_dos_name(name):
    """machine_file::pc_names::valid_dos_name: canonical upper-case 8.3."""
    parts = name.split(".")
    if len(parts) > 2:
        return False

    def valid(part, limit):
        return 0 < len(part) <= limit and all(c in DOS_CHARS for c in part)

    stem = parts[0]
    if not valid(stem, 8) or (len(parts) == 2 and not valid(parts[1], 3)):
        return False
    if stem in ("CON", "NUL", "AUX", "PRN", "CLOCK$"):
        return False
    return not (len(stem) == 4 and stem[:3] in ("COM", "LPT") and stem[3] in "123456789")


def inferred_kind(path):
    """media_import::extra_kind: the kind the import gives this file name."""
    extension = os.path.splitext(file_name(path))[1][1:].lower()
    if extension in ("txt", "pdf", "md", "doc"):
        return "document"
    if extension in ("zip", "7z", "arj", "rar", "tar", "tgz", "gz"):
        return "archive"
    if extension in ("exe", "com", "bat"):
        return "executable"
    return "other"


# --- licence expressions (unemupc98 image_store::license) ---------------------


def join(nodes, conjunction):
    """Node::join: nested operands of the same operator lifted into it."""
    flat = []
    for node in nodes:
        if node[0] == ("and" if conjunction else "or"):
            flat.extend(node[1])
        else:
            flat.append(node)
    if len(flat) == 1:
        return flat[0]
    return ("and" if conjunction else "or", flat)


def render(node):
    """Node::render: single spaces, parentheses only around an OR inside an
    AND."""
    if node[0] == "license":
        return node[1]
    if node[0] == "with":
        return f"{node[1]} WITH {node[2]}"
    conjunction = node[0] == "and"
    parts = []
    for operand in node[1]:
        text_ = render(operand)
        parts.append(f"({text_})" if conjunction and operand[0] == "or" else text_)
    return (" AND " if conjunction else " OR ").join(parts)


def valid_license(identifier):
    return identifier in LICENSES or bool(LICENSE_REF.fullmatch(identifier))


def parse_license(expression, where):
    """Expression::parse: the parsed tree and its canonical spelling, or a
    refusal. A tree node is ("license", id), ("with", id, exception),
    ("and", [nodes]) or ("or", [nodes])."""
    if len(expression.encode("utf-8")) > MAX_LICENSE_INPUT:
        raise Refusal(f"{where}: the licence expression is too long")
    spaced = expression.replace("(", " ( ").replace(")", " ) ")
    tokens = [t for t in re.split(f"[{re.escape(WHITESPACE)}]+", spaced) if t]
    # A fixed bound on nesting, checked before parsing, keeps the recursive
    # parser far from Python's recursion limit; no real statement nests
    # deeply. (unemupc98 has no such bound; this refuses only what it would
    # accept at an absurd depth.)
    depth = deepest = 0
    for token in tokens:
        depth += {"(": 1, ")": -1}.get(token, 0)
        deepest = max(deepest, depth)
    if deepest > MAX_LICENSE_NESTING:
        raise Refusal(
            f"{where}: the licence expression nests parentheses {deepest} deep "
            f"(at most {MAX_LICENSE_NESTING})"
        )
    position = 0

    def peek():
        return tokens[position] if position < len(tokens) else None

    def take():
        nonlocal position
        token = peek()
        if token is not None:
            position += 1
        return token

    def disjunction():
        nodes = [conjunction()]
        while peek() == "OR":
            take()
            nodes.append(conjunction())
        return join(nodes, False)

    def conjunction():
        nodes = [term()]
        while peek() == "AND":
            take()
            nodes.append(term())
        return join(nodes, True)

    def term():
        token = take()
        if token == "(":
            node = disjunction()
            if take() != ")":
                raise Refusal(f"{where}: unbalanced parentheses in {expression!r}")
            return node
        if token is None:
            raise Refusal(f"{where}: incomplete licence expression {expression!r}")
        if not valid_license(token):
            raise Refusal(
                f"{where}: unknown SPDX licence identifier {token!r}; use the canonical "
                "SPDX id unemupc98 accepts or LicenseRef-<name>"
            )
        if peek() == "WITH":
            take()
            exception = take()
            if exception not in EXCEPTIONS:
                raise Refusal(f"{where}: unknown SPDX licence exception {exception!r}")
            return ("with", token, exception)
        return ("license", token)

    try:
        node = disjunction()
    except RecursionError as error:
        raise Refusal(f"{where}: the licence expression nests too deeply") from error
    if position != len(tokens):
        raise Refusal(f"{where}: invalid SPDX licence expression {expression!r}")
    canonical = render(node)
    if len(canonical.encode("utf-8")) > MAX_LICENSE_CANONICAL:
        raise Refusal(f"{where}: the licence expression is too long")
    return node, canonical


def license_identifiers(node):
    if node[0] in ("license", "with"):
        return [node[1]]
    return [i for operand in node[1] for i in license_identifiers(operand)]


def is_reference(node):
    """Expression::is_reference: one LicenseRef-<idstring>."""
    return node[0] == "license" and node[1].startswith("LicenseRef-")


def named_extra(extras, name, where, what):
    """media_import::named_extra: the one extra whose file name is NAME."""
    named = [i for i, extra in enumerate(extras) if file_name(extra.path) == name]
    if not named:
        known = ", ".join(repr(file_name(extra.path)) for extra in extras)
        raise Refusal(f"{where}: {name!r} is not the file name of an extra ({known})")
    if len(named) > 1:
        raise Refusal(f"{where}: {name!r} names {len(named)} extras; give the {what} a file name of its own")
    return named[0]


def whole_word(needle, haystack):
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack, re.IGNORECASE) is not None


# --- the manifest -----------------------------------------------------------


def parse_extra(value, where):
    fields(value, where, ("path", "sha256", "dos_name", "kind"))
    path = out_path(value["path"], f"{where}.path")
    if not valid_text(file_name(path)):
        raise Refusal(f"{where}.path: the file name {file_name(path)!r} is longer than 128 characters")
    dos_name = string(value["dos_name"], f"{where}.dos_name")
    if not valid_dos_name(dos_name):
        raise Refusal(
            f"{where}.dos_name: {dos_name!r} is not a canonical upper-case DOS 8.3 "
            "name (no reserved device names)"
        )
    kind = choice(value["kind"], f"{where}.kind", KINDS)
    if kind != inferred_kind(path):
        raise Refusal(
            f"{where}.kind: {kind!r} differs from {inferred_kind(path)!r}, the kind "
            f"unemupc98's import gives {file_name(path)!r} by its extension"
        )
    return Extra(path, sha256(value["sha256"], f"{where}.sha256"), dos_name, kind)


def parse_release(value, warnings):
    where = "release"
    fields(
        value,
        where,
        ("work", "version", "default_format", "publishers", "developers", "year",
         "genres", "platform", "license", "terms", "extra"),
        ("edition_slug", "edition", "series", "sort_title", "labels", "source"),
    )
    work = slug(value["work"], f"{where}.work", REPOSITORY,
                "a lower-case slug (letters and digits, joined by '.', '_' or '-')")
    if len(f"{NAMESPACE}/{work}") > MAX_REPOSITORY:
        raise Refusal(f"{where}.work: {NAMESPACE}/{work} is longer than {MAX_REPOSITORY} bytes")
    version = slug(value["version"], f"{where}.version", VERSION,
                   "an upstream version: lower-case letters, digits and '.', starting with a "
                   "digit and ending with a letter or digit")
    if ".." in version:
        raise Refusal(f"{where}.version: {version!r} has an empty part between dots")
    edition_slug = None
    if "edition_slug" in value:
        edition_slug = slug(value["edition_slug"], f"{where}.edition_slug", EDITION_SLUG,
                            "a lower-case slug of letters and digits, starting with a letter")
        if REVISION_LIKE.fullmatch(edition_slug):
            raise Refusal(f"{where}.edition_slug: {edition_slug!r} reads as a revision in a tag")
        if edition_slug in FORMAT_SLUGS.values():
            raise Refusal(f"{where}.edition_slug: {edition_slug!r} reads as a format in a tag")
        if "edition" not in value:
            raise Refusal(f"{where}.edition_slug: a tagged edition needs release.edition, the "
                          "edition the title record states")
    year = value["year"]
    if isinstance(year, bool) or not isinstance(year, int) or not 1 <= year <= 9999:
        raise Refusal(f"{where}.year: expected a whole number from 1 to 9999")

    extras = tuple(
        parse_extra(item, f"{where}.extra[{i}]")
        for i, item in enumerate(tables(value["extra"], f"{where}.extra"), 1)
    )
    seen = {}
    for i, extra in enumerate(extras, 1):
        if extra.dos_name in seen:
            raise Refusal(
                f"{where}.extra[{i}].dos_name: {extra.dos_name!r} is also the DOS name "
                f"of release.extra[{seen[extra.dos_name]}]"
            )
        seen[extra.dos_name] = i

    license = as_option(string(value["license"], f"{where}.license"), f"{where}.license")
    tree, _ = parse_license(license, f"{where}.license")
    terms = as_option(string(value["terms"], f"{where}.terms"), f"{where}.terms")
    terms_index = named_extra(extras, terms, f"{where}.terms", "terms text")
    source = None
    if "source" in value:
        source = as_option(string(value["source"], f"{where}.source"), f"{where}.source")
    copyleft = [i for i in license_identifiers(tree) if i.startswith(COPYLEFT_PREFIXES)]
    if copyleft and source is None:
        raise Refusal(
            f"{where}.license: {license!r} names copyleft licences "
            f"({', '.join(copyleft)}); the set must carry its corresponding source "
            "archive: name it in release.source"
        )
    if source is None and not is_reference(tree):
        raise Refusal(
            f"{where}.license: {license!r} is not one LicenseRef-; unemupc98 opens a "
            "set under an SPDX expression only together with its source archive: "
            "name it in release.source"
        )
    if source is not None:
        source_index = named_extra(extras, source, f"{where}.source", "source archive")
        if source_index == terms_index:
            raise Refusal(f"{where}.source: {source!r} is the terms text, not a source archive")
        if extras[source_index].kind != "archive":
            raise Refusal(
                f"{where}.source: {source!r} is a {extras[source_index].kind} extra, "
                "not an archive"
            )

    labels = text_list(value.get("labels", []), f"{where}.labels", False)
    for i, label in enumerate(labels, 1):
        if label != label.lower():
            raise Refusal(f"{where}.labels[{i}]: {label!r} is not lower case")
    if not any(label in LABEL_KINDS for label in labels):
        warnings.append(f"{where}.labels: no kind label ({', '.join(LABEL_KINDS)})")
    if not any(label in LABEL_LICENCE_CLASSES for label in labels):
        warnings.append(
            f"{where}.labels: no licence class label ({', '.join(LABEL_LICENCE_CLASSES)})"
        )

    return Release(
        work=work,
        version=version,
        default_format=choice(value["default_format"], f"{where}.default_format", FORMATS),
        edition_slug=edition_slug,
        edition=optional_text(value, "edition", where),
        series=optional_text(value, "series", where),
        sort_title=optional_text(value, "sort_title", where),
        publishers=text_list(value["publishers"], f"{where}.publishers", True),
        developers=text_list(value["developers"], f"{where}.developers", True),
        year=year,
        genres=text_list(value["genres"], f"{where}.genres", True),
        platform=as_option(choice(value["platform"], f"{where}.platform", PLATFORMS), f"{where}.platform"),
        # The import checks every set label, as every disk label, with
        # valid_text (media_import::plan_with); its CLI trims them and
        # refuses a repeat.
        labels=labels,
        license=license,
        terms=terms,
        source=source,
        extras=extras,
    )


def derived_tag(release, media_format, revision):
    """VERSION[-EDITION_SLUG][-FORMAT][-rN]: the format only when it is not
    the release's own, the revision only from 2."""
    parts = [release.version]
    if release.edition_slug is not None:
        parts.append(release.edition_slug)
    if media_format != release.default_format:
        parts.append(FORMAT_SLUGS[media_format])
    if revision >= 2:
        parts.append(f"r{revision}")
    return "-".join(parts)


def parse_set(value, where, release, warnings):
    fields(value, where, ("title", "description", "format", "disk"), ("revision", "digest"))
    media_format = choice(value["format"], f"{where}.format", FORMATS)
    revision = value.get("revision", 1)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise Refusal(f"{where}.revision: expected a whole number from 1")
    digest = None
    if "digest" in value:
        digest = string(value["digest"], f"{where}.digest")
        if not DIGEST.fullmatch(digest):
            raise Refusal(f"{where}.digest: expected 'sha256:' and 64 lowercase hexadecimal digits")
    tag = derived_tag(release, media_format, revision)
    if not TAG.fullmatch(tag):
        raise Refusal(f"{where}: the derived tag {tag!r} is not a valid tag (at most 128 bytes)")
    title = text(value["title"], f"{where}.title")
    if whole_word(release.version, title):
        warnings.append(f"{where}.title: {title!r} holds the version {release.version!r}; the title is the work's only")
    if release.edition is not None and whole_word(release.edition, title):
        warnings.append(f"{where}.title: {title!r} holds the edition {release.edition!r}; the title is the work's only")
    disks = []
    for i, item in enumerate(tables(value["disk"], f"{where}.disk"), 1):
        disk = f"{where}.disk[{i}]"
        fields(item, disk, ("path", "sha256", "label"))
        disks.append(
            Disk(
                out_path(item["path"], f"{disk}.path"),
                sha256(item["sha256"], f"{disk}.sha256"),
                text(item["label"], f"{disk}.label"),
            )
        )
    return MediaSet(
        tag=tag,
        title=title,
        description=long_text(value["description"], f"{where}.description"),
        format=media_format,
        revision=revision,
        disks=tuple(disks),
        digest=digest,
    )


def validate(data):
    """A checked Manifest from parsed TOML, or a Refusal naming the field."""
    fields(data, "manifest", ("release", "set"))
    warnings = []
    release = parse_release(data["release"], warnings)
    sets = tuple(
        parse_set(item, f"set[{i}]", release, warnings)
        for i, item in enumerate(tables(data["set"], "set"), 1)
    )
    seen = {}
    for i, media_set in enumerate(sets, 1):
        if media_set.tag in seen:
            raise Refusal(
                f"set[{i}]: the derived tag {media_set.tag!r} is also set[{seen[media_set.tag]}]'s"
            )
        seen[media_set.tag] = i
    manifest = Manifest(release, sets, tuple(warnings))
    pinned = {}
    folded = {}
    for where, path, digest in pinned_outputs(manifest):
        if pinned.setdefault(path, (where, digest))[1] != digest:
            raise Refusal(f"{where}: {path!r} is pinned with another SHA-256 in {pinned[path][0]}")
        # One file on a case-insensitive file system.
        other = folded.setdefault(path.lower(), path)
        if other != path:
            raise Refusal(f"{where}: {path!r} and {other!r} differ only in case")
    return manifest


def pinned_outputs(manifest):
    for i, media_set in enumerate(manifest.sets, 1):
        for j, disk in enumerate(media_set.disks, 1):
            yield f"set[{i}].disk[{j}].sha256", disk.path, disk.sha256
    for i, extra in enumerate(manifest.release.extras, 1):
        yield f"release.extra[{i}].sha256", extra.path, extra.sha256


def pins(manifest):
    """Every pinned output once, as (path, sha256): of a set manifest the
    disks in set and installer order, then the extras; of a package
    manifest the ROM, then the source archive."""
    if isinstance(manifest, FirmwareManifest):
        return [(manifest.rom.path, manifest.rom.sha256), (manifest.source.path, manifest.source.sha256)]
    seen = {}
    for _, path, digest in pinned_outputs(manifest):
        seen.setdefault(path, digest)
    return list(seen.items())


# --- firmware packages --------------------------------------------------------


def parse_output(value, where):
    fields(value, where, ("path", "sha256"))
    path = out_path(value["path"], f"{where}.path")
    if not valid_text(file_name(path)):
        raise Refusal(f"{where}.path: the file name {file_name(path)!r} is longer than 128 characters")
    return Output(path, sha256(value["sha256"], f"{where}.sha256"))


def parse_package(value):
    where = "package"
    fields(
        value,
        where,
        ("catalogue_id", "revision", "title", "description", "vendor", "version", "labels", "license"),
        ("digest",),
    )
    catalogue_id = slug(value["catalogue_id"], f"{where}.catalogue_id", REPOSITORY,
                        "a catalogue id (lower-case letters and digits, joined by '.', '_' or '-')")
    if len(f"{FIRMWARE_NAMESPACE}/{catalogue_id}") > MAX_REPOSITORY:
        raise Refusal(
            f"{where}.catalogue_id: {FIRMWARE_NAMESPACE}/{catalogue_id} is longer than {MAX_REPOSITORY} bytes"
        )
    revision = value["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise Refusal(f"{where}.revision: expected a whole number from 1")
    digest = None
    if "digest" in value:
        digest = string(value["digest"], f"{where}.digest")
        if not DIGEST.fullmatch(digest):
            raise Refusal(f"{where}.digest: expected 'sha256:' and 64 lowercase hexadecimal digits")
    description = long_text(value["description"], f"{where}.description")
    if len(description) > MAX_FIRMWARE_DESCRIPTION:
        raise Refusal(f"{where}.description: longer than {MAX_FIRMWARE_DESCRIPTION} characters")
    labels = value["labels"]
    if not isinstance(labels, list) or not labels:
        raise Refusal(f"{where}.labels: expected a non-empty list of strings")
    if len(labels) > MAX_FIRMWARE_LABELS:
        raise Refusal(f"{where}.labels: more than {MAX_FIRMWARE_LABELS} labels")
    for i, label in enumerate(labels, 1):
        slug(label, f"{where}.labels[{i}]", FIRMWARE_LABEL,
             "a label: lower-case letters, digits, '.', '_' and '-', starting with a letter "
             "or digit, at most 64 bytes")
    if len(set(labels)) != len(labels):
        raise Refusal(f"{where}.labels: duplicate entry")
    license = as_option(string(value["license"], f"{where}.license"), f"{where}.license")
    parse_license(license, f"{where}.license")
    return Package(
        catalogue_id=catalogue_id,
        revision=revision,
        title=text(value["title"], f"{where}.title"),
        description=description,
        vendor=text(value["vendor"], f"{where}.vendor"),
        version=text(value["version"], f"{where}.version"),
        labels=tuple(labels),
        license=license,
        digest=digest,
    )


def validate_firmware(data):
    """A checked FirmwareManifest from parsed TOML, or a Refusal naming the
    field."""
    fields(data, "manifest", ("package", "rom", "source"))
    package = parse_package(data["package"])
    rom = parse_output(data["rom"], "rom")
    source = parse_output(data["source"], "source")
    if not source.path.endswith(SOURCE_ARCHIVE_SUFFIXES):
        raise Refusal(f"source.path: {source.path!r} is not a .tar or .zip archive")
    if rom.path.lower() == source.path.lower():
        raise Refusal(f"source.path: {source.path!r} is also the ROM's path (up to case)")
    return FirmwareManifest(package, rom, source)


def holds(data, keys):
    """Whether parsed TOML has any of the top-level KEYS."""
    return isinstance(data, dict) and any(key in data for key in keys)


def load(path):
    """A checked set manifest (Manifest) from a file named media.toml, or a
    package manifest (FirmwareManifest) from one named firmware.toml. The
    file name says the kind; a file of the other kind's tables is refused
    by name, and so is any other file name."""
    name = os.path.basename(path)
    kind = MANIFEST_NAMES.get(name)
    if kind is None:
        raise Refusal(
            f"{name!r} is not a manifest name: a set manifest is named media.toml, "
            "a package manifest firmware.toml"
        )
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as error:
        raise Refusal(f"cannot read: {error.strerror}") from error
    except tomllib.TOMLDecodeError as error:
        raise Refusal(f"not valid TOML: {error}") from error
    if kind == "firmware":
        if holds(data, SET_TABLES):
            raise Refusal(
                "firmware.toml holds a set manifest ([release], [[set]]); a package manifest "
                "has [package], [rom] and [source], and a set manifest is named media.toml"
            )
        return validate_firmware(data)
    if holds(data, PACKAGE_TABLES):
        raise Refusal(
            "media.toml holds a package manifest ([package], [rom], [source]); a set manifest "
            "has [release] and [[set]], and a package manifest is named firmware.toml"
        )
    return validate(data)


def import_request(manifest, tag, out_dir):
    """What unemupc98's `image media import` takes for the set with the
    derived TAG, as its template code builds it (template.rs, media_request
    with local_tag): the repository and tag, the disks in installer order
    with their labels and the set's format, the release's extras with their
    DOS names and kinds, and the title record. Paths are joined to OUT_DIR,
    the recipe's out/<recipe>/. The statement that opens the set is
    open_statement(manifest)."""
    matching = [s for s in manifest.sets if s.tag == tag]
    if not matching:
        raise Refusal(f"no set with the tag {tag!r} in the manifest")
    media_set = matching[0]
    release = manifest.release

    def join_out(path):
        return os.path.join(out_dir, *path.split("/"))

    return Request(
        tag=f"{release.repository}:{media_set.tag}",
        title=Title(
            title=media_set.title,
            description=media_set.description,
            publishers=release.publishers,
            developers=release.developers,
            release_year=release.year,
            genres=release.genres,
            platform=release.platform,
            version=release.version,
            edition=release.edition,
        ),
        labels=release.labels,
        media=tuple(Source(join_out(d.path), d.label, media_set.format) for d in media_set.disks),
        extras=tuple(ExtraSource(join_out(e.path), e.dos_name, e.kind) for e in release.extras),
        compress=True,
    )


def open_statement(manifest):
    """media_import::OpenStatement: the licence, the terms text's file name
    and the source archive's, when there is one."""
    release = manifest.release
    return OpenStatement(release.license, release.terms, release.source)


def import_arguments(request, statement):
    """The arguments of `upcli image media import` (after the program name)
    that import REQUEST opened by STATEMENT, without --store and --create:
    the disks in installer order, each with its label and its format, the
    extras with their DOS names, the title record, the set labels and the
    open statement. Repeated options keep the manifest's order, which the
    set digest depends on. An explicit --format asserts the manifest's
    format; upcli builds the same set from it as from the format it detects
    by size (media_import::plan_with)."""
    title = request.title
    args = ["image", "media", "import"]
    args += [source.path for source in request.media]
    args += ["--title", title.title, "--tag", request.tag]
    for i, source in enumerate(request.media, 1):
        args += ["--label", f"{i}={source.label}", "--format", f"{i}={source.format}"]
    for extra in request.extras:
        args += ["--extra", f"{extra.path}:{extra.dos_name}"]
    for publisher in title.publishers:
        args += ["--publisher", publisher]
    for developer in title.developers:
        args += ["--developer", developer]
    args += ["--year", str(title.release_year)]
    for genre in title.genres:
        args += ["--genre", genre]
    args += ["--platform", title.platform, "--description", title.description]
    args += ["--version", title.version]
    if title.edition is not None:
        args += ["--edition", title.edition]
    for label in request.labels:
        args += ["--set-label", label]
    args += ["--open", "--license", statement.license, "--license-text", statement.terms]
    if statement.source is not None:
        args += ["--source", statement.source]
    if not request.compress:
        args.append("--no-compress")
    return args


def firmware_import_arguments(manifest, out_dir):
    """The arguments of `upcli image firmware import` (after the program
    name) that import the package of MANIFEST, a FirmwareManifest, as
    unemupc's template build imports it (FirmwareSource::statement): the ROM
    for its catalogue id, the title, description, vendor and version, the
    labels in the manifest's order (the import sorts them), the open
    statement and the source archive, with the outputs joined to OUT_DIR;
    without --store and --create.

    The template build states no licence: the catalogue records the ROM's,
    and the package is open because the source archive is the one the
    catalogue pins. --open --license states the same licence explicitly
    (firmware_import::rom_license), so the package digest is the template
    build's (measured at unemupc 3634f02 for every package here; the
    upcli of the 0.0.2 release gives the same digests), and upcli
    refuses a licence other than the catalogue's. lib/publish.py also
    checks the licence the package states against the manifest's."""
    package = manifest.package

    def join_out(path):
        return os.path.join(out_dir, *path.split("/"))

    args = ["image", "firmware", "import", join_out(manifest.rom.path), "--for", package.catalogue_id]
    args += ["--title", package.title, "--description", package.description]
    args += ["--vendor", package.vendor, "--version", package.version]
    for label in package.labels:
        args += ["--label", label]
    args += ["--open", "--license", package.license, "--source", join_out(manifest.source.path)]
    return args


def artifacts(manifest, out_dir):
    """Every set of a set manifest, or the package of a package manifest, as
    an Artifact whose import arguments name the outputs in OUT_DIR."""
    if isinstance(manifest, FirmwareManifest):
        package = manifest.package
        return [Artifact(
            kind="firmware",
            ref=f"{package.repository}:{package.tag}",
            repository=package.repository,
            tag=package.tag,
            store_tag=package.store_tag,
            license=package.license,
            digest=package.digest,
            arguments=tuple(firmware_import_arguments(manifest, out_dir)),
        )]
    statement = open_statement(manifest)
    result = []
    for media_set in manifest.sets:
        request = import_request(manifest, media_set.tag, out_dir)
        result.append(Artifact(
            kind="media",
            ref=request.tag,
            repository=manifest.release.repository,
            tag=media_set.tag,
            store_tag=request.tag,
            license=statement.license,
            digest=media_set.digest,
            arguments=tuple(import_arguments(request, statement)),
        ))
    return result


def describe(manifest, out_dir):
    """Every set or package of MANIFEST as `sets` prints it: its kind, ref,
    repository and tag, the tag the import writes in the store, the licence
    expression, the digest pin (None without one) and the import arguments,
    with the outputs' paths joined to OUT_DIR."""
    return [
        {
            "kind": artifact.kind,
            "ref": artifact.ref,
            "repository": artifact.repository,
            "tag": artifact.tag,
            "store_tag": artifact.store_tag,
            "license": artifact.license,
            "digest": artifact.digest,
            "import": list(artifact.arguments),
        }
        for artifact in artifacts(manifest, out_dir)
    ]


def refs(manifests):
    """Every reference across MANIFESTS (pairs of a name and a checked
    manifest), in order: each set's unemu/media/<work>:<tag> and each
    package's unemu/firmware/<catalogue id>:<revision>. A Refusal when two
    of them share one, as two manifests for one work can (another default
    format, for one), since a published tag never moves; and when two
    package manifests name one catalogue id, since upcli imports both under
    one store tag."""
    seen = {}
    packages = {}
    result = []
    for name, checked in manifests:
        if isinstance(checked, FirmwareManifest):
            package = checked.package
            entries = [(f"{name}: package", f"{package.repository}:{package.tag}")]
        else:
            entries = [
                (f"{name}: set[{i}]", f"{checked.release.repository}:{media_set.tag}")
                for i, media_set in enumerate(checked.sets, 1)
            ]
        for where, ref in entries:
            if ref in seen:
                raise Refusal(f"{where}: {ref} is also {seen[ref]}")
            seen[ref] = where
            result.append(ref)
        if isinstance(checked, FirmwareManifest):
            if package.catalogue_id in packages:
                raise Refusal(
                    f"{name}: package: {package.catalogue_id} is also the catalogue id of "
                    f"{packages[package.catalogue_id]}; upcli imports both as {package.store_tag}: "
                    "keep one manifest per catalogue id (a new revision replaces the old one)"
                )
            packages[package.catalogue_id] = name
    return result


USAGE = (
    "usage: python3 -I lib/manifest.py check|pins MANIFEST, refs MANIFEST..., "
    "or sets MANIFEST OUT_DIR"
)


def main(argv):
    if len(argv) == 4 and argv[1] == "sets":
        try:
            checked = load(argv[2])
        except Refusal as refusal:
            print(f"{argv[2]}: {refusal}", file=sys.stderr)
            return 1
        described = describe(checked, os.path.abspath(argv[3]))
        print(json.dumps(described, indent=2, ensure_ascii=True))
        return 0
    if len(argv) >= 3 and argv[1] == "refs":
        checked = []
        for path in argv[2:]:
            try:
                checked.append((path, load(path)))
            except Refusal as refusal:
                print(f"{path}: {refusal}", file=sys.stderr)
                return 1
        try:
            listed = refs(checked)
        except Refusal as refusal:
            print(refusal, file=sys.stderr)
            return 1
        for ref in listed:
            print(ref)
        return 0
    if len(argv) != 3 or argv[1] not in ("check", "pins"):
        print(USAGE, file=sys.stderr)
        return 2
    command, path = argv[1], argv[2]
    try:
        manifest = load(path)
    except Refusal as refusal:
        print(f"{path}: {refusal}", file=sys.stderr)
        return 1
    if command == "check" and isinstance(manifest, FirmwareManifest):
        package = manifest.package
        print(f"{path}: ok, firmware package {package.catalogue_id} revision {package.revision}, "
              f"{len(pins(manifest))} outputs")
        print(f"  {package.repository}:{package.tag}")
    elif command == "check":
        for warning in manifest.warnings:
            print(f"{path}: warning: {warning}", file=sys.stderr)
        disks = sum(len(s.disks) for s in manifest.sets)
        print(
            f"{path}: ok, {len(manifest.sets)} sets, {disks} disks, "
            f"{len(manifest.release.extras)} extras"
        )
        for media_set in manifest.sets:
            print(f"  {manifest.release.repository}:{media_set.tag}")
    else:
        for output, digest in pins(manifest):
            print(output, digest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
