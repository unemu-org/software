# Recipes

A recipe turns pinned upstream inputs into one media set or one firmware
package, byte for byte the same on every run. It is a recipe file of
`upcli`, the CLI of the pinned unemupc release (`upcli.toml`): TOML 1.0, an
ordered list of closed steps that start no process, inputs found by size
and SHA-256, and a lock that pins every byte of the artifact it builds. The
format is unemupc's (its decisions 33 to 39 and
`backlog/docs/design/recipes.md`, recipe version `0.1`); the rules behind
our recipes (upstream bytes, licences, sources) are in
[AGENTS.md](../AGENTS.md).

## Where recipes live

| Path | What |
| --- | --- |
| `recipes/media/<work>/<work>-<release>.media.toml` | one media set, `unemu/media/ibm-pc/<work>:<release>-rN` |
| `recipes/media/<work>/<work>.work.toml` | the work file: what the releases of the work share |
| `recipes/media/<work>/licenses.txt` | a bundled input: the licence index, our own text |
| `recipes/media/<work>/source/` | a script that makes a prepared input (FreeDOS only) |
| `recipes/firmware/<catalogue id>.firmware.toml` | one firmware package, `unemu/firmware/ibm-pc/<catalogue id>:rN` |
| `recipes/private/media/<work>/<work>-<release>.media.toml` | a private media description, `private/media/ibm-pc/<work>:<release>-rN`, with its work file and our own bundled texts beside it |
| `recipes/private/firmware/<catalogue id>.firmware.toml` | a private firmware description, `private/firmware/ibm-pc/<catalogue id>:rN` |
| `<recipe file>.lock/sha256/<hex>` | the lock `upcli recipe pin` wrote beside the recipe |

A folder holds one work, because the work is the repository: a work file
lies beside the recipes that name it, and the last component of a
recipe's `name` must equal its work's id. A recipe file builds one
artifact under one tag. `lib/recipes.py` accepts open outputs under
`unemu/media/` or `unemu/firmware/`, and private outputs under
`private/media/` or `private/firmware/`; it refuses two recipes that name
one tag or one alias. The separate `recipes/private/` tree keeps private
descriptions apart from open recipes and their prepared-input scripts.

Private recipes have no `[license]` and no `urls` key on any input (even an
empty list is refused); open recipes must have `[license]`. No title file
belongs in the repository or lock. `bundle = true` is only for our own
text: a regular file directly beside the recipe, not a link or a file set,
at most 1 MiB, UTF-8 without NUL. The static check enforces the size, path
and text limits; review must establish authorship. Users supply all title
files from their own libraries and pin with `upcli recipe pin FILE
--library DIR`; no download of the title is permitted here.

`python3 -I lib/recipes.py check --cli out/upcli/upcli` checks open and
private recipes alike: the pinned CLI, every recipe and work file's `ok`
line, no findings or drift, and a digest and valid lock for each recipe.
It builds and fetches no input. `check.yml` runs that check on every recipe,
but builds and conforms only the open selection. `lib/changed.py --json`
includes changed private descriptions, locks, bundled texts and work files
for static checking; `--json --open-only` supplies the build selection.
Experimental and publication runs never build, fetch or publish private outputs;
`lib/publish.py` skips them by default and refuses an explicit selection
that includes any (including a mixed folder).

The tag is never written: it is `<release>-r<revision>` from `[media]`
(`r<revision>` for a package, which has no release). A release is parts
joined by `-` (unemupc's decision-38), in this order and only when they
apply: the version as the software shows it, the edition, the format when
it is not the release's own (`1.2m`, `720k`, `360k`, `iso`). FreeDOS 1.4
gives `1.4`, `1.4-1.2m` and `1.4-720k`; Ken's Labyrinth 2.01, the work's
primary edition, `2.01`. The release alone is the alias of its newest
revision.

## What a recipe holds

A media recipe (`*.media.toml`):

| Table | What ours hold |
| --- | --- |
| `recipe`, `work` | `"0.1"`; the work's id, which names `<work>.work.toml` beside it |
| `[media]` | `name` (the repository), `platform = "ibm-pc"`, `release`, `revision`, and `digest` once pinned |
| `[title]` | the release's own fields: `version`, `edition`, `year`, `description`, `requirements`, and labels the work does not share |
| `[license]` | `expression` (SPDX, or one `LicenseRef-`), `terms` (the extra holding the licence text) and, for copyleft content, `source` (the extra holding the corresponding source) |
| `[input.<id>]` | `name`, `size`, `sha256`, `urls` (HTTPS, mirrors in order); `bundle = true` for our own text beside the recipe |
| `[[step]]` | `unpack` (listed members of a zip), `fat` (a FAT volume from a tree), `text` (our text with DOS line ends) |
| `[[medium]]` | the disks in installer order: `id`, `label`, `format`, `file_name`, `from` |
| `[[extra]]` | the licence index, the terms text and the source archive: `id`, `file_name`, `dos_name`, `kind`, `from` |

A firmware recipe (`*.firmware.toml`) has `[firmware]` instead of
`[media]`, `[package]` instead of `[title]` (`title`, `vendor`, `version`,
`date`, `description`, `notes`, `labels`, `[package.fits]`), one `[[rom]]`
taken unmodified from its input, and the source archive as its one
`[[extra]]`. A package's licence must be the one unemupc's firmware
catalogue records for the ROM, and the title, description, vendor, version
and labels are the words of unemupc's template build
(`pc_image::template::FIRMWARE`), so the two build one package.

How our recipes use the steps:

- **Disks as released.** An upstream disk image is a member of an
  `unpack` step with its size and SHA-256, and a `[[medium]]` takes it
  unmodified (FreeDOS).
- **An archive without disks.** A `fat` step builds the diskette from the
  archive's files, unmodified, in the archive's order (`order = "listed"`)
  and with the archive's times, written into the `unpack` member list;
  the volume label, serial and label time are the recipe's (Ken's
  Labyrinth). The bytes are upcli's FAT builder's, with unemupc's own boot
  sector, which reports a non-system disk and retries the boot.
- **The licence index.** `licenses.txt`, our own CC0-1.0 text, is a
  bundled input; a `text` step gives it DOS line ends, and the extra
  `LICENSES.TXT` carries it.

## Work files

`<work>.work.toml` holds what every release of the work shares:
`[work] id`, a `[title]` with `title`, `publishers`, `developers`,
`genres`, `software_platform`, `labels` and `synopsis`, the work's database
ids (`[ids]`: `wikidata`, `mobygames`, `igdb`) and `[needs]`. A recipe's
own single values win, its lists of people replace the work's, labels are
the union, and `synopsis` lives in the work file only (unemupc's
decision-39). `upcli recipe show FILE` prints the merged record a build
writes.

A pinned recipe builds its lock's record, never the work file's: an edit to
a work file reaches a published set only with a new pin. `upcli recipe
check` reports such a difference as drift; `lib/recipes.py check` (and so
CI) refuses it, so a work file never silently disagrees with what is
published. It also refuses a run in which upcli prints no `ok` line for a
recipe, or for a work file a recipe names.

## Metadata

Every value is cited from the upstream release or its author; nothing is
guessed, and a value without a source is left out. The sources are named in
the comments of each file.

| Field | Where | Rule |
| --- | --- | --- |
| work id | recipe `work`, work file `[work] id` | the repository's last component |
| synopsis | work file | what the work is, after its author's own words |
| work ids | work file `[ids]` | Wikidata first; MobyGames and IGDB as Wikidata records them |
| needs | work file `[needs]` | only the kinds and values the release's own documents state (unemupc's `ibm-pc` vocabulary: `cpu`, `fpu`, `video`, `sound`, `memory`, `ems`, `xms`, `input`) |
| requirements | recipe `[title]` | the release's own words, quoted |
| package date | firmware `[package] date` | the date the ROM itself carries (its BIOS date or version string) |
| board | firmware `[package.fits]` | what the firmware catalogue records; stating it changes no byte |

upcli 0.0.3's recipe format has no keys for a package's provenance (how
the ROM was made, from where, at which revision) or for the licence of an
extra other than the source archive; our packages need neither for now.

## Inputs

An input is its size and SHA-256; its name, other names and URLs are hints.
`upcli recipe build --fetch` downloads a missing input from its URLs in
order (HTTPS only, at most its size) and checks it before any step reads
it; a library (`--library DIR`) is searched by size and hash first,
whatever the files are called. Nothing downloaded is ever executed.

URLs name only sources that distribute the bytes with the rights holder's
permission: the author's or publisher's own site or release, an official
mirror, an archive the rights holder approved, or an archive.org capture of
one of these. A site that offers software without that permission is never
named, even for one of its legitimate titles, because a recipe with
`--fetch` is a tool that downloads, not only a link. A recipe without an
open licence (output under `private/`) has no `urls` at all: its inputs are
sizes, SHA-256s and dump database ids, and the user supplies the files from
their own library (`--library`). See AGENTS.md, "Only authorised sources".

A **prepared input** is a file a recipe takes by hash that no step can
make. The FreeDOS source archive is one: it needs an ISO 9660 reader, git
and a tar writer. `recipes/media/freedos/source/build.sh` (with
`iso-extract.py` and the helpers of `lib/source.sh`) makes it from pinned
upstream inputs, in `nix develop`, and stops unless the result has the pin
the three FreeDOS recipes take (`tests/recipes_test.py` keeps the two
equal). The archive is a release asset of this repository, tag
`freedos-1.4-floppy`, the URL the recipes name (created by the maintainer
on 2026-11-20).

```sh
nix develop --command bash recipes/media/freedos/source/build.sh
# out/freedos-1.4-floppy-src/freedos-1.4-floppy-src.tar, as pinned
```

## Planning from local files

`lib/plan.py` surveys every `*.media.toml` and `*.firmware.toml` under
`recipes/`, or the files and folders given, by running the supplied
upcli's `recipe plan FILE --json` once per recipe. It accepts private
recipes in `recipes/private/` and outside this repository too; it does not
apply `lib/recipes.py`'s repository policy. Python 3.11 or later is required.

```sh
python3 -I lib/plan.py --cli out/upcli/upcli --library /path/to/files
python3 -I lib/plan.py --cli out/upcli/upcli recipes/firmware --json
python3 -I lib/plan.py --cli out/upcli/upcli recipes/media/kenslab \
  --library /path/to/files --store /path/to/store --config /path/to/preferences.toml
```

`--cli` defaults to `$UPCLI`. Unlike the publishing pipeline, the survey
keeps your HOME and upcli preferences, including registered libraries;
`--config FILE` chooses another preferences file. `--library DIR` is
repeatable and adds to those libraries, and `--store DIR` chooses an
existing store. upcli searches by size and SHA-256, including zip members,
the store and its download cache, bundled files beside the recipe, and
libraries. The survey builds and downloads nothing, changes no images and
contacts no registry. **Cache limitation:** upcli 0.0.3 may update its
library and decoded-content index caches in an existing store as its own
bookkeeping; it has no option to disable those writes. The wrapper itself
writes only its report to stdout. It keeps `LC_ALL=C` and `TZ=UTC`.

Each row names the recipe and its revision ref, with one of these statuses:

| Status | Meaning |
| --- | --- |
| `ready` | Every needed input is local, or found members or locked output payloads replace the inputs that would make them. |
| `needs --fetch` | Every missing required input has URLs; a build can try them with `--fetch`. Their availability is not tested. |
| `missing` | At least one required input has neither a local copy nor URLs; its name, size, SHA-256 and any dump-database ids are listed. |
| `invalid` | upcli refused the recipe (the first three findings are shown), or returned an unusable plan. |

Missing optional attachments do not prevent `ready`. A survey exits 0
even with missing or invalid recipes; usage errors and inability to run
upcli are nonzero. `--json` prints an array of rows (`recipe`, `refs`,
`status`, `missing`, `findings`, `summary`, `warnings`) without the footer.
`missing` holds the required inputs still needed, with their `input` id,
`name`, `size`, `sha256`, dump `ids` and `urls`; file sets list their missing
files individually. upcli's store and library read warnings are retained.

The text footer gives a build command with the survey's library, store and
config options, and a push command targeting `private/...` on your own
registry. For example, after a `ready` Ken's Labyrinth row:

```sh
out/upcli/upcli recipe build recipes/media/kenslab/kenslab-2.01.media.toml \
  --library /path/to/files --store /path/to/store
out/upcli/upcli image push unemu/media/ibm-pc/kenslab:2.01-r1 \
  private/media/ibm-pc/kenslab:2.01-r1 --registry registry.example \
  --store /path/to/store --config /path/to/preferences.toml
```

These are commands for you to run: the survey never runs them. The push
uses your registry configuration and authentication; the recipe's local
`unemu/...` ref does not select a registry. A `ready` plan describes local
availability, while the build still verifies bytes and the recipe lock.

## The lock

`upcli recipe pin FILE` is the author's build: it builds the recipe as a
draft, writes the artifact's manifest, config and metadata (a package: its
README) into `FILE.lock/sha256/`, and its manifest digest into the recipe as
`digest`. The lock holds no payload byte. Every later `upcli recipe build`
makes the payload, checks each blob against the locked manifest, and
publishes the locked manifest unchanged: a later upcli gives the pinned
digest whatever it would write itself, and a step that no longer writes the
same bytes fails the build. `upcli recipe conformance` builds every pinned
recipe from libraries alone, without finding anything instead of making it,
and compares the digests.

A pinned recipe is never pinned again. To change a published set or
package:

- before unemupc's first public release, it is replaced under its tags:
  remove `digest` and the lock and pin again with the same revision; then,
  in this order, the prepared inputs it downloads are created or
  confirmed, the change is merged (and tested on `unemu/experimental/`),
  the maintainer deletes its old revision tag and, for a set, its release
  alias on the registry, and it is published ([publishing.md](publishing.md),
  Replacing a published set or package);
- after it, raise `revision`, remove `digest` and the lock, and pin: the
  next revision gets its own tag. Its release alias then has to move to
  it, which the pipeline does not do yet ([publishing.md](publishing.md),
  Names and tags).

## Adding a recipe

For a non-open title use `recipes/private/` and the static-only procedure
above; the build and publication steps below are for open recipes.

1. Write the recipe in `recipes/media/<work>/` (with its work file) or
   `recipes/firmware/`. `upcli recipe scaffold FILE...` prints the input
   tables of files you have, and an archive's member list with sizes,
   hashes and times.
2. Check it: `upcli recipe check FILE`, then `upcli recipe show FILE`.
3. Build the draft into a scratch store and review the result:
   `upcli recipe build FILE --fetch --store DIR`, then `upcli image media
   show` (or `image firmware show`) of the tag it prints.
4. Pin it with the pinned upcli, in a fresh store: `upcli recipe pin FILE
   --fetch --store DIR`.
5. Pin a copy of the draft again from the first store's downloads alone
   (`--library DIR/cache/downloads`, a new store): the recipe file and its
   lock must be byte-identical (`diff -r`).
6. `python3 -I lib/recipes.py check --cli UPCLI` and the dry run,
   `python3 -I lib/publish.py --cli UPCLI --fetch FILE`: every row must
   read `dry run`.

Run upcli with a fresh `HOME`, `LC_ALL=C` and `TZ=UTC`, as lib/publish.py
does. See [publishing.md](publishing.md) for the pipeline and the pinned
upcli.

## Recipes on the registry

upcli can publish a pinned recipe as an artifact of its own
(`upcli recipe push`, to `unemu/recipes/<kind>/ibm-pc/<name>:<tag>`, the
recipe file, its work file, its bundled texts and its lock, no payload).
unemupc publishes no recipe before its recipe format is frozen (its
decision-35, point 8), and neither does this repository: the sets and
packages are published, their recipes are not yet. Publishing them will
need a registry account that may write `unemu/recipes/**`, which is the
maintainer's step.
