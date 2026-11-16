# unemu-media

Reproducible builds of the **open installation media** of the unemu family of
emulators: floppy (and later CD) sets of freely redistributable software, built
byte for byte from pinned upstream releases, with their licences, corresponding
source archives and documentation, and published as OCI media sets to the
public `unemu/media/` namespace.

The first consumer is **unemupc98**, an IBM PC/AT-class emulator. Its library
and its official templates pull these sets; it no longer builds them itself.

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

The rules come from unemupc98's decision records (decision-26 and its
2026-11-15 amendments, decision-27), mirrored in this repository's
`AGENTS.md` and beads memories.

## Layout

- `lib/recipe.sh`: the Bash helpers every recipe sources: environment
  clearing, verified downloads, listed-member zip extraction, deterministic
  FAT images and source tars, pinned git exports, and the pin checks.
- `lib/manifest.py`: checks a set manifest, lists the sets' derived tags and
  prints its output pins (`python3 -I lib/manifest.py check|pins MANIFEST`);
  `refs MANIFEST...` lists every set's `unemu/media/<work>:<tag>` and refuses
  one that two sets share across manifests; `sets MANIFEST OUT_DIR` prints
  every set as JSON: its ref, licence, digest pin and the `upcli image media
  import` arguments.
- `lib/publish.py`: the import and publish pipeline: builds the selected
  sets, imports them with the pinned upcli into a scratch store, checks them
  and pushes them only when asked (see Publishing).
- `upcli.pin`: the unemupc commit whose CLI, `upcli`, imports and pushes the
  sets.
- `recipes/<recipe>/`: one directory per upstream release: `build.sh`, which
  pins the inputs and builds the outputs, and `media.toml`, the manifest of
  the sets it feeds, holding the output pins and each set's digest pin.
  Every set of a manifest is one work, `unemu/media/<work>`, under a tag
  derived from the upstream version, the edition, the format and the
  revision.
- `tests/`: the offline self-test of the helpers, the manifest tests, the
  pipeline tests (a fake upcli and a fake registry), the tests of the
  FreeDOS recipe's ISO 9660 reader and an example manifest
  (`tests/fixtures/`, no real release).

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
  python3 -I tests/iso_extract_test.py &&
  for m in tests/fixtures/example.toml recipes/*/media.toml; do
    python3 -I lib/manifest.py check "$m" || exit 1
  done &&
  python3 -I lib/manifest.py refs recipes/*/media.toml'
```

Build one recipe's outputs (the first run downloads its pinned inputs; a run
whose outputs already match their pins stops at once, without the network):

```sh
nix develop --command bash recipes/freedos-1.4-floppy/build.sh
```

## Publishing

The sets are imported and pushed with `upcli`, the command line of
unemupc, built from the commit in `upcli.pin` (in a unemupc checkout at that
commit: `cargo build -p cli --bin upcli`). Each set's `digest` in its
manifest is the set digest that CLI gives; the pipeline refuses another, so
moving the pin is checked by the digest pins. `upcli` at the pinned commit
has no `--version`; when a later one reports its commit, the pipeline
compares it with the pin.

A dry run builds the selected recipes (every recipe by default; or name
recipes, or single sets by their `unemu/media/<work>:<tag>`), checks every
output against its pin, imports each set into a fresh store under `build/`
(removed afterwards), and checks `upcli image media show` and the guard of
`upcli image push --dry-run` against the target. It needs no credentials
and never contacts a registry:

```sh
nix develop --command python3 -I lib/publish.py --cli /path/to/upcli
nix develop --command python3 -I lib/publish.py --cli /path/to/upcli freedos-1.4-floppy
```

`UPCLI` may name the binary instead of `--cli` (a name without a slash is
looked up on `PATH`); `--store DIR` imports into a new directory that is
kept. The registry host is `UNEMU_REGISTRY_HOST`, checked as upcli checks
one (a name with a dot or a port, or `localhost`); for a dry run, unset
means `registry.unemu.dev`. A recipe's build never sees a variable whose
name starts with `UNEMU` or contains `PASSWORD`, `TOKEN`, `SECRET` or
`CREDENTIAL`.

The run prints a table of repository, tag, digest, outcome and licence on
stdout: `dry run` (checked and pinned), `unpinned` (checked, but the
manifest pins no digest), `mismatch`, `failed`, and after a push `pushed`,
`present` (the registry already serves that digest under the tag:
skipped), `refused` (the registry holds another digest under the tag: a
media tag never moves), `unconfirmed` (pushed, but the read-back failed or
named another digest) or `held` (not pushed because another set failed or
is unpinned). It exits 0 only when every selected set passed, and with
`--push` is on the registry. An unpinned set fails a dry run too, unless
`--allow-unpinned` is given: that dry run measures a new recipe's digests
for its manifest, and is never valid with `--push`.

Pushing is the maintainer's step. `--push` takes the credentials from the
environment only, `UNEMU_REGISTRY_USER` and `UNEMU_REGISTRY_PASSWORD`, with
`UNEMU_REGISTRY_HOST` set explicitly, and hands them to the `upcli image
push` call alone. It pushes only when every selected set passed and is
pinned, and never passes `--move`. Before each push it reads the target tag
anonymously over HTTPS (OCI and Docker manifest types; a 404 is confirmed
through the repository's tag list, and a listed tag it cannot read is
refused); any other answer, a redirect included, stops the push:

```sh
export UNEMU_REGISTRY_HOST=registry.unemu.dev UNEMU_REGISTRY_USER=ACCOUNT
read -rs UNEMU_REGISTRY_PASSWORD && export UNEMU_REGISTRY_PASSWORD
nix develop --command python3 -I lib/publish.py --cli /path/to/upcli --push freedos-1.4-floppy
```

`--experimental RUN` targets `<host>/unemu/experimental/media/<work>:<media-tag>.ci<RUN>`
instead (RUN: the CI run number, from 1), for the CI flow that pushes there and
promotes by digest to `unemu/media/`. The `.ci<RUN>` suffix is not part of
the media tag. The `latest` alias is not pushed yet.

## Recipes

| Recipe | Sets |
| --- | --- |
| `recipes/freedos-1.4-floppy/` | FreeDOS 1.4 Floppy Edition: `unemu/media/freedos:1.4` (1.44M), `:1.4-1.2m`, `:1.4-720k` |

## Status

Bootstrapping: the recipes build and verify their outputs, and the pipeline
imports and checks the sets with the pinned upcli and pushes them on
request; no set is published yet, and CI is next. Open work is tracked in
beads (`bd ready`, prefix `um`).

## Licence

The recipes, scripts and documentation in this repository are licensed under
the GNU Affero General Public License, version 3 or later ([LICENSE](LICENSE)).
The media sets they build carry the licences of their contents.
