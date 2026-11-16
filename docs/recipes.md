# Recipes

A recipe turns one upstream release into the outputs of one or more media
sets, or of one firmware package, byte for byte the same on every run. The
rules behind them (upstream bytes, licences, sources) are in
[AGENTS.md](../AGENTS.md).

## What a recipe is

`recipes/<recipe>/`, named after the upstream release
(`freedos-1.4-floppy`, `at-bios-1.0.2-unemu.3`), holds:

- `build.sh`, which pins and fetches the inputs and builds the outputs;
- one manifest, which pins every output and the digest of every set or
  package. Its file name says its kind:
  - `media.toml`: the installation media sets the release feeds, published
    as `unemu/media/<work>:<tag>`;
  - `firmware.toml`: one firmware package, published as
    `unemu/firmware/<catalogue id>:<revision>`;
- any helper files the build needs (the FreeDOS recipe has an ISO 9660
  reader and its licence index).

A recipe has exactly one manifest. `lib/manifest.py` refuses a manifest
under any other name, or one holding the other kind's tables, and the
pipeline refuses a recipe with both.

What the outputs are:

- **A media set:** the upstream disk images, unmodified and in installer
  order (or, when upstream ships an archive, a deterministic image of its
  files as released), a licence index, the corresponding source archive for
  copyleft content, and documents that state a licence.
- **A firmware package:** a ROM and its corresponding source archive, both
  release assets of the ROM's own project, installed unmodified. No recipe
  builds firmware from source.

## Where files go

| Directory | What | Committed |
| --- | --- | --- |
| `cache/<recipe>/` | downloads, checked against their pins on every use | no |
| `build/work.XXXXXX/` | a fresh work directory per run, removed on exit | no |
| `out/<recipe>/` | the outputs, installed only once every pin matches | no |

When every output in `out/<recipe>/` already matches its pin, `build.sh`
stops at once, without the network.

## Set manifests (media.toml)

Every set of a manifest is one work, `unemu/media/<work>`, under its own
tag. Output paths are relative to `out/<recipe>/`.

| Table and key | Meaning |
| --- | --- |
| `[release]` `work` | the repository name under `unemu/media/`: a short, stable, lower-case slug |
| `version` | the upstream version, lower case, no `-` |
| `default_format` | the release's own format (`1.44M` for an archive without disks) |
| `edition_slug`, `edition` | optional: the edition in the tag, and as the title states it |
| `series`, `sort_title` | optional display fields |
| `publishers`, `developers`, `genres` | non-empty lists |
| `year` | the release year |
| `platform` | a software platform upcli knows (`DOS`) |
| `labels` | optional; should include a kind (`game`, `operating-system`, `application`, `utility`, `development`, `driver`) and a licence class (`open-source`, `freeware`, `shareware`, `public-domain`), or the check warns |
| `license` | an SPDX expression, or one `LicenseRef-` for a vendor's own terms |
| `terms` | the extra holding the licence text |
| `source` | the extra holding the corresponding source; required unless `license` is one `LicenseRef-` |
| `[[release.extra]]` | `path`, `sha256`, `dos_name` (DOS 8.3), `kind` (`document`, `archive`, `executable` or `other`) |
| `[[set]]` | `title`, `description`, `format` (`1.44M`, `1.2M`, `720K`, `360K` or `iso-2048`), optional `revision` (from 1) and `digest` (`sha256:...`, the set digest the pinned upcli gives) |
| `[[set.disk]]` | `path`, `sha256`, `label`; in installer order |

The tag is derived, never written: `VERSION[-EDITION_SLUG][-FORMAT][-rN]`,
the format only when it is not the default one (`1.44m`, `1.2m`, `720k`,
`360k`, `iso`), `-rN` only from revision 2. FreeDOS 1.4 gives `1.4`,
`1.4-1.2m` and `1.4-720k`.

## Package manifests (firmware.toml)

One package of unemupc's firmware catalogue, `unemu/firmware/<catalogue
id>`, as unemupc's own template build states it.

| Table and key | Meaning |
| --- | --- |
| `[package]` `catalogue_id` | the catalogue entry the ROM is; also the repository name |
| `revision` | the tag: a whole number from 1, raised only when the statement or metadata changes for the same ROM |
| `digest` | optional: `sha256:...`, the package digest the pinned upcli gives |
| `title`, `vendor`, `version` | 1 to 128 characters each |
| `description` | one line, up to 4096 characters |
| `labels` | non-empty list of lower-case ids |
| `license` | the SPDX expression the catalogue records for the ROM |
| `[rom]` | `path`, `sha256`: the ROM image |
| `[source]` | `path` (a `.tar` or `.zip`), `sha256`: its corresponding source archive, the one the catalogue pins |

A catalogue id has one manifest: a new revision replaces the old one in it,
and the old revision's tag stays on the registry.

## Writing build.sh

A recipe starts with `set -euo pipefail`, sources `lib/recipe.sh`, stops
early when `outputs_match` says the outputs are already there, and calls
`work_dir`. A recipe whose helpers write files of their own (images, source
tars, CRLF texts) exports its own `SOURCE_DATE_EPOCH` first; the library
clears the caller's. No here-document or here-string longer than 512
bytes: the devShell's bash hangs on them on macOS. The helpers:

| Helper | What it does |
| --- | --- |
| `fetch DEST SHA256 MAX_BYTES URL...` | a verified download into the cache, mirrors tried in turn |
| `fetch_release_assets` | a release's assets, fetched with the manifest's pins and staged unmodified (firmware recipes) |
| `zip_expect_members`, `zip_extract` | check a zip's member list; extract one listed member, verified |
| `fat_image` | a deterministic FAT floppy image |
| `git_export` | the files of a pinned git commit |
| `source_tar` | a deterministic source tar |
| `crlf` | an LF text with CRLF line ends |
| `outputs_match`, `check_outputs`, `install_outputs` | the pin checks, and the only way into `out/` |
| `require_tools`, `sha256_of`, `check_sha256`, `stamp` | small utilities |

The comment at the top of [`lib/recipe.sh`](../lib/recipe.sh) documents
each one.

## Adding a recipe

1. Create `recipes/<recipe>/` with `build.sh` and a manifest. Pin every
   input by SHA-256 in `build.sh`, preferring the upstream's own checksums
   where it publishes them.
2. Run it: `nix develop --command bash recipes/<recipe>/build.sh`. It stops
   at the first output whose SHA-256 differs from its pin and names the
   actual one. Review each output before you pin it.
3. Remove `out/<recipe>/` and `cache/<recipe>/` and run it again: it must
   install the same bytes, which the pins check.
4. Check the manifest: `python3 -I lib/manifest.py check
   recipes/<recipe>/<manifest>`, and the refs across all of them (the
   [checks](../README.md#checks) do both).
5. Measure the digests: run the dry run with `--allow-unpinned <recipe>`.
   Each new set or package shows as `unpinned` with its digest; write that
   digest into the manifest.
6. Run the dry run again without `--allow-unpinned`: every row must read
   `dry run`.

See [publishing.md](publishing.md) for the pipeline and the pinned upcli.
