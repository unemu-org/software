# unemu-media

Reproducible builds of the **open installation media** and the **open
firmware packages** of the unemu family of emulators:

- floppy (and later CD) sets of freely redistributable software, built byte
  for byte from pinned upstream releases, with their licences, corresponding
  source archives and documentation, published as OCI media sets to the
  public `unemu/media/` namespace;
- the open firmware packages the machine templates use (system BIOS, video
  BIOS, fixed-disk option ROM), each a ROM its own project releases with its
  corresponding source archive, published as OCI firmware packages to
  `unemu/firmware/<catalogue id>`.

The first consumer is **unemupc**, an IBM PC/AT-class emulator. Its library
and its official templates pull these sets and packages; it no longer builds
them itself.

## What a set is

- **Media:** the upstream disk images, unmodified, in the upstream order, each
  pinned by SHA-256. When upstream ships an archive instead of images, the
  recipe builds a deterministic image (fixed volume serial, label and
  timestamps) holding the files exactly as released.
- **Licences:** an SPDX expression for the whole set and a licence index
  naming every program and its licence. A set is open only when every part is
  covered; otherwise it is not published.
- **Sources:** for copyleft content, the corresponding source archive, built
  deterministically and attached to the set, linked from every copyleft layer.
- **Documentation:** manuals and README files that carry a licence, attached
  as extras. Documents that state no licence are not attached.
- **Metadata:** title, publisher, developer, year, genre, platform and a short
  factual description, also written as standard OCI annotations so registry
  web UIs show them.

The rules come from unemupc's decision records (decision-26 and its
2026-11-15 amendments, decision-27), mirrored in this repository's
`AGENTS.md` and beads memories.

## What a firmware package is

- **ROM:** one ROM image of unemupc's firmware catalogue, whose SHA-256 and
  size are the ROM's identity there. Each comes as a release asset of its own
  project: the unemu-org forks build the ROM and its source archive in their
  release workflows and attach both to the release; the recipe downloads
  those two assets, checks them against their pins and installs them
  unmodified. No recipe builds firmware from source.
- **Licence and source:** the SPDX expression the catalogue records for the
  ROM, and the corresponding source archive the catalogue pins, attached to
  the package.
- **Metadata:** title, description, vendor, version and labels, word for word
  as unemupc's template build states them, so the package digest is the one
  that build gives.
- **Tag:** the package revision, a whole number from 1, raised only when the
  statement or the metadata changes for the same ROM bytes; a revision never
  moves.

## Layout

- `lib/recipe.sh`: the Bash helpers every recipe sources: environment
  clearing, verified downloads, a release's assets fetched and staged
  unmodified (`fetch_release_assets`, which the firmware recipes use),
  listed-member zip extraction, deterministic FAT images and source tars,
  pinned git exports, and the pin checks.
- `lib/manifest.py`: checks a set or package manifest, lists its refs and
  prints its output pins (`python3 -I lib/manifest.py check|pins MANIFEST`);
  `refs MANIFEST...` lists every set's `unemu/media/<work>:<tag>` and every
  package's `unemu/firmware/<id>:<revision>`, and refuses one that two
  manifests share (and two package manifests for one catalogue id);
  `sets MANIFEST OUT_DIR` prints every set or package as JSON: its kind, ref,
  store tag, licence, digest pin and the `upcli image media import` or
  `upcli image firmware import` arguments. The file name says the kind: a
  set manifest is named `media.toml`, a package manifest `firmware.toml`;
  every command refuses another name, and a file holding the other kind's
  tables.
- `lib/publish.py`: the import and publish pipeline: builds the selected
  recipes, imports their sets and packages with the pinned upcli into a
  scratch store, checks them and pushes them only when asked (see
  Publishing).
- `lib/upcli.py`: reads `upcli.toml`, checks the commit a upcli reports,
  and fetches the pinned release binary (`python3 -I lib/upcli.py fetch
  DIR`; see Publishing).
- `upcli.toml`: the unemupc release whose CLI, `upcli`, imports and pushes
  the sets and packages: its commit, tag and version, and for each
  platform the SHA-256 and size of the release archive (`sha256`, `size`)
  and of the `upcli` inside it (`upcli_sha256`, `upcli_size`).
- `recipes/<recipe>/`: one directory per upstream release: `build.sh`, which
  pins the inputs and builds the outputs, and one manifest holding the
  output pins and the digest pins:
  - `media.toml`, the sets the release feeds. Every set of a manifest is one
    work, `unemu/media/<work>`, under a tag derived from the upstream
    version, the edition, the format and the revision.
  - `firmware.toml`, one firmware package: `unemu/firmware/<catalogue id>`
    under its revision.
- `tests/`: the offline self-test of the helpers, the manifest tests, the
  pipeline tests (a fake upcli and a fake registry), the tests of the pin
  and of fetch (fake release archives served from `file://`), the tests of
  the FreeDOS recipe's ISO 9660 reader and an example set manifest and
  package manifest (`tests/fixtures/example/media.toml`,
  `tests/fixtures/example-firmware/firmware.toml`; no real release).

Recipes cache downloads in `cache/<recipe>/`, build in a fresh directory
under `build/`, and install their outputs into `out/<recipe>/` only once every
pin matches; none of these is committed.

Run the checks inside the devShell:

```sh
nix develop --command bash -c '
  shellcheck -x lib/recipe.sh tests/*.sh recipes/*/build.sh &&
  bash tests/recipe-selftest.sh &&
  python3 -I tests/manifest_test.py &&
  python3 -I tests/publish_test.py &&
  python3 -I tests/upcli_test.py &&
  python3 -I tests/iso_extract_test.py &&
  for m in tests/fixtures/*/media.toml tests/fixtures/*/firmware.toml \
      recipes/*/media.toml recipes/*/firmware.toml; do
    python3 -I lib/manifest.py check "$m" || exit 1
  done &&
  python3 -I lib/manifest.py refs recipes/*/media.toml recipes/*/firmware.toml'
```

Build one recipe's outputs (the first run downloads its pinned inputs; a run
whose outputs already match their pins stops at once, without the network):

```sh
nix develop --command bash recipes/freedos-1.4-floppy/build.sh
nix develop --command bash recipes/at-bios-1.0.2-unemu.3/build.sh
```

## Publishing

The sets and packages are imported and pushed with `upcli`, the command line
of unemupc, of the unemupc release pinned in `upcli.toml`. Each set's or
package's `digest` in its manifest is the digest that CLI gives; the
pipeline refuses another, so moving the pin is checked by the digest pins.

`upcli.toml` holds exactly these keys (its comments explain them):
`commit`, the full commit the release's tag points to; `tag` and `version`,
the release's; and one `[archive.<target triple>]` table per platform, for
`x86_64-unknown-linux-gnu` and `aarch64-apple-darwin`, with the archive's
`file` name (`unemupc-<version>-<triple>.tar.gz`), `sha256` and `size` in
bytes, and the SHA-256 and size of its `upcli` member, `upcli_sha256` and
`upcli_size`. `lib/upcli.py` refuses any other key, a missing or malformed
one, and, as TOML does, a key given twice. To move the pin, take the commit
from the release's tag, measure each archive's SHA-256 and size on the
downloaded file and those of its `upcli` member by reading it out of the
checked archive, without running it (never from the release's
`SHA256SUMS`), then run the dry run below: every digest pin must still
hold, or change deliberately.

There are two ways to get the pinned upcli:

- The release binary, for Linux x86_64 and macOS arm64 hosts (any other is
  refused):

  ```sh
  nix develop --command python3 -I lib/upcli.py fetch /path/to/new-dir
  ```

  reads the host's archive into memory once, from `cache/upcli/` or else
  downloaded from the GitHub release over HTTPS into a temporary directory,
  and checks those bytes, the ones it goes on to use, against the size and
  SHA-256 in `upcli.toml`, our own pins, never against the release's
  `SHA256SUMS`. Only a checked download is written into the cache; a cached
  copy is checked again on every run, and refused, never replaced, when it
  differs. Only the member `unemupc-<version>-<triple>/upcli` is taken out
  of the checked bytes: it must be the one member of that name, a regular
  file, not a link, with the pinned size and SHA-256 of the binary, and an
  archive that names any member by an absolute path or with a `..`
  component is refused whole. It is written into the new directory with
  mode 0755, and its `--version` runs (with only `PATH`, a fresh `HOME` and
  `TMPDIR`, `LC_ALL=C` and `TZ=UTC`, in a scratch directory): unless the
  commit line of its stdout names the pinned commit, the directory is
  removed again. The binary's path is printed on stdout. This is the one
  downloaded program this repository runs (`AGENTS.md`, Hard rules). Under
  an x86_64 Python on Apple silicon (Rosetta) the host reads as macOS
  x86_64 and is refused: pass `--triple aarch64-apple-darwin`.
- A source build, in a clean unemupc checkout at the pinned commit, with no
  local changes (inside unemupc's devShell; the crates need a recent
  rustc):

  ```sh
  UNEMUPC_SOURCE_COMMIT=$(git rev-parse HEAD) cargo build -p cli --bin upcli
  ```

  `UNEMUPC_SOURCE_COMMIT` gives the build the commit line the pipeline
  checks; without it, a source build has none and is refused.

Before anything is built, the pipeline runs `upcli --version` once and
reads the commit line of its stdout: the one line that starts with
`commit:`, which must be exactly `commit: ` and the 40 lower-case hex
digits of the pinned commit. Another commit, a malformed or repeated
commit line, a failing `--version`, or no commit line at all (a source
build without `UNEMUPC_SOURCE_COMMIT`, or a upcli older than unemupc 0.0.2)
refuses the run, a dry run as well as a push; there is no option to skip
the check. Nothing else in the output is read: its third-party notices name
upstream commits of their own.

A dry run builds the selected recipes (every recipe by default; or name
recipes, or single sets and packages by their ref), checks every output
against its pin, imports each set or package into a fresh store under
`build/` (removed afterwards), and checks `upcli image media show` or
`upcli image firmware show` and the guard of `upcli image push --dry-run`
against the target. It needs no credentials and never contacts a registry:

```sh
nix develop --command python3 -I lib/publish.py --cli /path/to/upcli
nix develop --command python3 -I lib/publish.py --cli /path/to/upcli freedos-1.4-floppy
nix develop --command python3 -I lib/publish.py --cli /path/to/upcli unemu/firmware/xub-r638-xtp-ata1f0:1
```

A set is imported with `upcli image media import`, under its ref in the
store. A package is imported with `upcli image firmware import ROM --for ID`
with the manifest's title, description, vendor, version, labels, `--open
--license SPDX` and `--source ARCHIVE`; upcli tags it `firmware/<id>:1` in
the store, and refuses a licence other than the one its catalogue records.
`upcli image firmware show` must then state the manifest's title, vendor,
version, description and labels, the pinned ROM and source archive, the
manifest's licence on both and an open config and README; the output of the
import must be exactly its `complete` and `imported` lines (a note, such as
one that keeps a ROM user-supplied, fails it).

`UPCLI` may name the binary instead of `--cli` (a name without a slash is
looked up on `PATH`); `--store DIR` imports into a new directory that is
kept. The registry host is `UNEMU_REGISTRY_HOST`, checked as upcli checks
one (a name with a dot or a port, or `localhost`); for a dry run, unset
means `registry.unemu.dev`. A recipe's build never sees a variable whose
name starts with `UNEMU` or contains `PASSWORD`, `TOKEN`, `SECRET` or
`CREDENTIAL`.

The targets are:

| Kind | Target | Experimental target (`--experimental RUN`) |
| --- | --- | --- |
| media set | `<host>/unemu/media/<work>:<media-tag>` | `<host>/unemu/experimental/media/<work>:<media-tag>.ci<RUN>` |
| firmware package | `<host>/unemu/firmware/<id>:<revision>` | `<host>/unemu/experimental/firmware/<id>:<revision>.ci<RUN>` |

The run prints a table of repository, tag, digest, outcome and licence on
stdout: `dry run` (checked and pinned), `unpinned` (checked, but the
manifest pins no digest), `mismatch`, `failed`, and after a push `pushed`,
`present` (the registry already serves that digest under the tag:
skipped), `refused` (the registry holds another digest under the tag: a
media tag and a package revision never move), `unconfirmed` (pushed, but
the read-back failed or named another digest) or `held` (not pushed because
another set or package failed or is unpinned). It exits 0 only when
everything selected passed, and with `--push` is on the registry. An
unpinned set or package fails a dry run too, unless `--allow-unpinned` is
given: that dry run measures a new recipe's digests for its manifest, and is
never valid with `--push`.

Pushing is the maintainer's step. `--push` takes the credentials from the
environment only, `UNEMU_REGISTRY_USER` and `UNEMU_REGISTRY_PASSWORD`, with
`UNEMU_REGISTRY_HOST` set explicitly, and hands them to the `upcli image
push` call alone. It pushes only when everything selected passed and is
pinned, and never passes `--move`. Before each push it reads the target tag
anonymously over HTTPS (OCI and Docker manifest types; a 404 is confirmed
through the repository's tag list, and a listed tag it cannot read is
refused); any other answer, a redirect included, stops the push:

```sh
export UNEMU_REGISTRY_HOST=registry.unemu.dev UNEMU_REGISTRY_USER=ACCOUNT
read -rs UNEMU_REGISTRY_PASSWORD && export UNEMU_REGISTRY_PASSWORD
nix develop --command python3 -I lib/publish.py --cli /path/to/upcli --push freedos-1.4-floppy
```

`--experimental RUN` (RUN: the CI run number, from 1) is for the CI flow
that pushes to `unemu/experimental/` and promotes by digest to `unemu/media/`
and `unemu/firmware/`. The `.ci<RUN>` suffix is not part of the media tag or
the package revision. An experimental repository longer than 255 bytes, or
a tag longer than 128, is refused before anything is built. The `latest` alias is not pushed, for sets or
packages: it is a movable tag, and the pipeline only writes tags that never
move.

## Recipes

| Recipe | Sets or package |
| --- | --- |
| `recipes/freedos-1.4-floppy/` | FreeDOS 1.4 Floppy Edition: `unemu/media/freedos:1.4` (1.44M), `:1.4-1.2m`, `:1.4-720k` |
| `recipes/at-bios-1.0.2-unemu.3/` | unemu AT BIOS 1.0.2-unemu.3 (unemu-org/at-bios v1.0.2-unemu.3): `unemu/firmware/unemu-at-bios-1.0.2-unemu.3:1` |
| `recipes/vgabios-0.9d-unemu.1/` | unemu VGA BIOS 0.9d-unemu.1, standard VGA (unemu-org/vgabios v0.9d-unemu.1): `unemu/firmware/vgabios-0.9d-unemu.1-std:1` |
| `recipes/xtide-universal-bios-r638-unemu.1/` | XTIDE Universal BIOS r638, XT+ build for ATA at 1F0h (unemu-org/xtide-universal-bios r638-unemu.1): `unemu/firmware/xub-r638-xtp-ata1f0:1` |

## Status

Bootstrapping: the recipes build and verify their outputs, and the pipeline
imports and checks the sets and packages with the pinned upcli, that of the
unemupc 0.0.2 release (a preview), and pushes them on request. The package
digests equal the ones unemupc's template build gave at commit 3634f02.
The release's macOS arm64 upcli gives the same digest for every set and
package; its Linux x86_64 upcli is to be checked the same way in CI
(um-7jx.6). Nothing is published yet, and CI is next. Open work is tracked
in beads (`bd ready`, prefix `um`).

## Licence

The recipes, scripts and documentation in this repository are licensed under
the GNU Affero General Public License, version 3 or later ([LICENSE](LICENSE)).
The media sets and firmware packages they build carry the licences of their
contents.
