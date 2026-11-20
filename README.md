# software

Reproducible builds of the open installation media sets
(`unemu/media/ibm-pc/<work>:<release>-rN`) and open firmware packages
(`unemu/firmware/ibm-pc/<catalogue id>:rN`) of the unemu emulators. Each
one is a recipe file of [unemupc](https://github.com/unemu-org/unemupc)'s
CLI, `upcli`: pinned upstream inputs, its licences and corresponding
sources, and a lock that pins every byte of the OCI artifact it builds,
which unemupc's library and machine templates pull.

## Principles

- **Bytes as released.** Disk images and ROMs are the upstream bytes, and
  files placed on a built image are unmodified.
- **Everything pinned.** Every input by size and SHA-256, every set and
  package by its digest and lock, and the CLI that builds them by its
  release.
- **Open only when every part is covered.** Real SPDX licences, a licence
  index for every program of a set, and the corresponding source of
  copyleft content; when in doubt, nothing is published.
- **Reproducible.** The same recipe gives the same bytes and digests on
  every run and host.
- **A revision tag never moves.** A published `rN` tag (`freedos:1.4-r1`,
  a package's `r1`) is never overwritten; a set's release alone
  (`freedos:1.4`) is the alias of its newest revision.

The full rules are in [AGENTS.md](AGENTS.md).

## Quick start

```sh
python3 -I lib/upcli.py fetch out/upcli                 # the pinned upcli, verified
python3 -I lib/recipes.py check --cli out/upcli/upcli   # check every recipe
python3 -I lib/publish.py --cli out/upcli/upcli --fetch # build all against their locks, dry run
```

Python 3.11 or later; `nix develop` provides it. The dry run needs no
credentials and never contacts a registry; it downloads the recipes' inputs
from the URLs they pin, each checked by size and SHA-256.

## What can I build from my files?

`python3 -I lib/plan.py --cli out/upcli/upcli --library /path/to/files`
surveys every recipe using your upcli libraries and local store: `ready`,
`needs --fetch`, `missing` or `invalid`. It builds and downloads nothing.
Give recipe files or folders to narrow the survey, or `--json` for JSON;
the footer shows how to build and push to your own private registry.
See [docs/recipes.md](docs/recipes.md#planning-from-local-files).

## Publishing

`lib/publish.py --push` pushes only when every selected set and package
passed its checks and its recipe pins its digest. The registry credentials
come from the environment and reach only the `upcli image push` call. A
revision tag never moves: one that already holds another digest is
refused, and `--move` is never used. Each set's release alias is pushed
after its revision, and refused, never moved, when it names another image;
sets and packages have no `latest`. For CI, `--experimental RUN` targets
`unemu/experimental/` instead. Pushing is the maintainer's step. Details:
[docs/publishing.md](docs/publishing.md).

[CI](docs/publishing.md#ci) checks what a change touches, pushes it from
`main` to `unemu/experimental/` and publishes it on the maintainer's
approval; the workflows that publish never rebuild or push what is already
published.

## Repository layout

- `recipes/media/<work>/`: the recipes of one work (one repository), its
  work file, its bundled texts and the locks; `recipes/firmware/`: one
  recipe per firmware package ([docs/recipes.md](docs/recipes.md)).
- `recipes/media/freedos/source/`: the script that makes the FreeDOS source
  archive, a prepared input; `lib/source.sh`: its helpers.
- `lib/publish.py`, `lib/recipes.py`, `lib/upcli.py`, `upcli.toml`: the
  publish pipeline, the recipe files as it reads them, and the pinned upcli
  ([docs/publishing.md](docs/publishing.md)).
- `lib/changed.py`, `.github/workflows/`: the CI and what a change touches.
- `tests/`: offline tests.
- `docs/`: the reference documentation.
- `cache/`, `build/`, `out/`: downloads, scratch and outputs; never
  committed.

## Checks

Run inside the devShell:

```sh
nix develop --command bash -c '
  shellcheck -x lib/source.sh tests/*.sh recipes/*/*/source/build.sh &&
  bash tests/source-selftest.sh &&
  for test in tests/*_test.py; do python3 -I "$test" || exit 1; done'
```

and, with the pinned upcli, `python3 -I lib/recipes.py check --cli
out/upcli/upcli`.

## Licence

The recipes, scripts and documentation in this repository are licensed under
the GNU Affero General Public License, version 3 or later ([LICENSE](LICENSE)).
The media sets and firmware packages they build carry the licences of their
contents.
