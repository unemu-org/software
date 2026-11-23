# software

Reproducible builds of the open installation media sets
(`unemu/media/ibm-pc/<work>:<release>-rN`) and open firmware packages
(`unemu/firmware/ibm-pc/<catalogue id>:rN`) of the unemu emulators. Each
one is a recipe file of [unemu](https://github.com/unemu-org/unemu),
the family CLI: pinned upstream inputs, its licences and corresponding
sources, and a lock that pins every byte of the OCI artifact it builds,
which unemupc's library and machine templates pull.

Private recipes under `recipes/private/` also describe commercial and other
non-open titles for users to build from their own files: no title files or input
URLs live here, and CI checks these recipes statically without building or publishing
their outputs. Their recipe text artifacts are public under `unemu/recipes/`.

## Principles

- **Bytes as released.** Disk images and ROMs are the upstream bytes, and
  files placed on a built image are unmodified.
- **Everything pinned.** Every input by size and SHA-256, every set and
  package by its digest and lock, and the CLI that builds them by its
  release.
- **Every output has a published recipe.** Everything under `unemu/media/**`
  and `unemu/firmware/**` on the family registry comes from a pinned recipe
  of this repository and has its recipe artifact under `unemu/recipes/...`
  at the same revision tag. An output without its published recipe is incomplete.
- **Open only when every part is covered.** Real SPDX licences, a licence
  index for every program of a set, and the corresponding source of
  copyleft content. Licence doubts must record the uncertainty, evidence and
  interpretation in the index. Missing licences or incomplete copyleft source
  prevent publication; anything is taken down at a rights holder's request.
- **Reproducible.** The same recipe gives the same bytes and digests on
  every run and host.
- **An output revision tag never moves.** A published `rN` tag (`freedos:1.4-r1`,
  a package's `r1`) is never overwritten; a set's release alone
  (`freedos:1.4`) is the alias of its newest revision.

The full rules are in [AGENTS.md](AGENTS.md).

## Quick start

```sh
python3 -I lib/unemu.py fetch out/unemu                 # local: our SHA-256 and size pins, then commit line
python3 -I lib/recipes.py check --cli out/unemu/unemu   # check every recipe
python3 -I lib/publish.py --cli out/unemu/unemu --fetch # build all open recipes against their locks, dry run
```

Python 3.11 or later; `nix develop` provides it. The dry run needs no
credentials and never contacts a registry; it downloads the recipes' inputs
from the URLs they pin, each checked by size and SHA-256.

Locally, `lib/unemu.py fetch` verifies both the release archive and its
`unemu` member against our own SHA-256 and size pins in `unemu.toml`, then
checks the commit line. CI uses upstream `setup-unemu`, pinned to the
release commit: it checks the archive against the release's `SHA256SUMS`
and runs `unemu --version` to check its `commit:` line, without checking
our archive pins (the maintainer's decision of 2026-11-23). To change the
release, update `unemu.toml` and all three workflows together; the README
check command tests their agreement. See [publishing](docs/publishing.md#the-pinned-unemu).

## What can I build from my files?

`python3 -I lib/plan.py --cli out/unemu/unemu --library /path/to/files`
surveys every recipe using your unemu libraries and local store: `ready`,
`needs --fetch`, `missing` or `invalid`. It builds and downloads nothing.
Give recipe files or folders to narrow the survey, or `--json` for JSON;
the footer shows how to build and push to your own private registry.
See [docs/recipes.md](docs/recipes.md#planning-from-local-files).

Recipe format **1.0 is frozen** (unemupc decision-43); its schema is the
[recipe-schema-1.0.json release asset](https://github.com/unemu-org/unemu/releases/download/v0.0.4/recipe-schema-1.0.json).
Recipe artifacts publish through the approved `publish` workflow, after their
open outputs. Private recipes publish as public text; their outputs stay private.

The link is one-way: the recipe artifact names the output digest. The output
cannot name its recipe because the recipe's lock contains that output digest.
Lookup is by name: `unemu/media/X:tag` maps to `unemu/recipes/media/X:tag`,
and firmware follows the same rule. Publication belongs to this repository;
the unemu project pushes nothing to the family registry.

## Publishing

`lib/publish.py --push` pushes only when every selected set and package
passed its checks and its recipe pins its digest. The registry credentials
come from the environment and reach only the unemu push call. A
revision tag never moves: one that already holds another digest is
refused, and `--move` is never used. Each set's release alias is pushed
after its revision, and refused, never moved, when it names another image;
sets and packages have no `latest`. For CI, `--experimental RUN` targets
`unemu/experimental/` instead. Pushing is the maintainer's step. Details:
[docs/publishing.md](docs/publishing.md).

`lib/publish.py --recipes` checks every open and private recipe, dry-runs
its recipe guard and anonymously verifies that each open output's revision
tag serves its pinned digest. `--recipes --guard-only` runs offline for CI;
it defers that output gate and cannot push. `--recipes --push` publishes
the text artifacts and pulls each back anonymously for exact byte comparison.
The `publish` workflow defaults to `both`: outputs first, then their recipes,
in one approved run. Explicit `outputs` mode leaves publication incomplete
until the recipes follow; `recipes` mode supports recipe-only updates and
private recipes. Each recipe's first push needs the maintainer's approval
of that run. See
[recipe artifacts](docs/publishing.md#recipe-artifacts).

[CI](docs/publishing.md#ci) checks what a change touches, pushes it from
`main` to `unemu/experimental/` and publishes it on the maintainer's
approval; output publication never rebuilds or pushes outputs already published
at their pinned digests. Recipe text can be updated for the same output digest.

## Repository layout

- `recipes/media/<work>/`: the recipes of one work (one repository), its
  work file, its bundled texts and the locks; `recipes/firmware/`: one
  recipe per firmware package ([docs/recipes.md](docs/recipes.md)).
- `recipes/media/freedos/source/`: the script that makes the FreeDOS source
  archive, a prepared input; `lib/source.sh`: its helpers.
- `lib/publish.py`, `lib/recipes.py`, `lib/unemu.py`, `unemu.toml`: the
  publish pipeline, the recipe files as it reads them, and the pinned unemu
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

and, with the pinned unemu, `python3 -I lib/recipes.py check --cli
out/unemu/unemu`.

## Licence

The recipes, scripts and documentation in this repository are licensed under
the GNU Affero General Public License, version 3 or later ([LICENSE](LICENSE)).
The media sets and firmware packages they build carry the licences of their
contents.
