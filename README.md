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
  one that two sets share across manifests.
- `recipes/<recipe>/`: one directory per upstream release: `build.sh`, which
  pins the inputs and builds the outputs, and `media.toml`, the manifest of
  the sets it feeds, holding the output pins. Every set of a manifest is one
  work, `unemu/media/<work>`, under a tag derived from the upstream version,
  the edition, the format and the revision.
- `tests/`: the offline self-test of the helpers, the manifest tests and an
  example manifest (`tests/fixtures/`, no real release).

Recipes cache downloads in `cache/<recipe>/`, build in a fresh directory
under `build/`, and install their outputs into `out/<recipe>/` only once every
pin matches; none of these is committed.

Run the checks inside the devShell:

```sh
nix develop --command bash -c '
  shellcheck lib/recipe.sh tests/*.sh &&
  bash tests/recipe-selftest.sh &&
  python3 -I tests/manifest_test.py &&
  python3 -I lib/manifest.py check tests/fixtures/example.toml'
```

## Status

Bootstrapping. The first recipe is the official FreeDOS 1.4 Floppy Edition
(1.44M, 1.2M and 720K sets), moving here from unemupc98. Open work is tracked
in beads (`bd ready`, prefix `um`).

## Licence

The recipes, scripts and documentation in this repository are licensed under
the GNU Affero General Public License, version 3 or later ([LICENSE](LICENSE)).
The media sets they build carry the licences of their contents.
