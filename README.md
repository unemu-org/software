# unemu-media

Reproducible builds of the open installation media sets
(`unemu/media/<work>:<tag>`) and open firmware packages
(`unemu/firmware/<catalogue id>:<revision>`) of the unemu emulators. Each
one is built from a pinned upstream release, carries its licences and
corresponding sources, and is published as an OCI artifact for
[unemupc](https://github.com/unemu-org/unemupc), whose library and machine
templates pull them.

## Principles

- **Bytes as released.** Disk images and ROMs are the upstream bytes, and
  files placed on a built image are unmodified.
- **Everything pinned.** Every input and output by SHA-256, every set and
  package by its digest, and the CLI that imports them by its release.
- **Open only when every part is covered.** Real SPDX licences, a licence
  index for every program of a set, and the corresponding source of
  copyleft content; when in doubt, nothing is published.
- **Reproducible.** The same recipe gives the same bytes and digests on
  every run and host.
- **A tag never moves.** A published media tag or package revision is never
  overwritten.

The full rules are in [AGENTS.md](AGENTS.md).

## Quick start

```sh
nix develop                                       # the pinned toolchain
bash recipes/freedos-1.4-floppy/build.sh          # build one recipe into out/
python3 -I lib/upcli.py fetch out/upcli           # the pinned upcli, verified
python3 -I lib/publish.py --cli out/upcli/upcli   # build, import and check all
```

The dry run needs no credentials and never contacts a registry; once the
recipes are built, it needs no network at all.

## Publishing

`lib/publish.py --push` pushes only when every selected set and package
passed its checks and its manifest pins its digest. The registry
credentials come from the environment and reach only the `upcli image push`
call. A tag never moves: a tag that already holds another digest is
refused, and `--move` is never used; the movable `latest` alias is not
pushed yet. For CI, `--experimental RUN` targets `unemu/experimental/`
instead. Pushing is the maintainer's step. Details:
[docs/publishing.md](docs/publishing.md).

[CI](docs/publishing.md#ci) checks what a change touches, pushes it from
`main` to `unemu/experimental/` and publishes it on the maintainer's
approval; the workflows that publish never rebuild or push what is already
published.

## Repository layout

- `recipes/<recipe>/`: one per upstream release, a `build.sh` and its
  manifest ([docs/recipes.md](docs/recipes.md)).
- `lib/recipe.sh`, `lib/manifest.py`: the recipe helpers and the manifest
  checker.
- `lib/publish.py`, `lib/upcli.py`, `upcli.toml`: the publish pipeline and
  the pinned upcli ([docs/publishing.md](docs/publishing.md)).
- `lib/changed.py`, `.github/workflows/`: the CI and the recipes a change
  touches.
- `tests/`: offline tests and example manifests.
- `docs/`: the reference documentation.
- `cache/`, `build/`, `out/`: downloads, scratch and outputs; never
  committed.

## Checks

Run inside the devShell:

```sh
nix develop --command bash -c '
  shellcheck -x lib/recipe.sh tests/*.sh recipes/*/build.sh &&
  bash tests/recipe-selftest.sh &&
  python3 -I tests/manifest_test.py &&
  python3 -I tests/publish_test.py &&
  python3 -I tests/upcli_test.py &&
  python3 -I tests/iso_extract_test.py &&
  python3 -I tests/changed_test.py &&
  for m in tests/fixtures/*/media.toml tests/fixtures/*/firmware.toml \
      recipes/*/media.toml recipes/*/firmware.toml; do
    python3 -I lib/manifest.py check "$m" || exit 1
  done &&
  python3 -I lib/manifest.py refs recipes/*/media.toml recipes/*/firmware.toml'
```

## Licence

The recipes, scripts and documentation in this repository are licensed under
the GNU Affero General Public License, version 3 or later ([LICENSE](LICENSE)).
The media sets and firmware packages they build carry the licences of their
contents.
