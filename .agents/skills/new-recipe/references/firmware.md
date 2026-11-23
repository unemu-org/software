# Firmware packages

Published to `unemu/firmware/ibm-pc/<catalogue id>:rN` (no alias).
Examples: `recipes/firmware/*.firmware.toml`. Rules: bd memory
`firmware-rules`.

## Before writing

- The ROM must already be in unemupc's firmware catalogue
  (`crates/assembly/src/firmware.rs` in unemupc): its catalogue id, SHA-256,
  size, licence and whether it is user-supplied. If it is not, the
  catalogue entry comes first, in unemupc (ask its supervisor).
- The ROM and its source archive are release assets of the project that
  builds them (the unemu-org forks attach them from their own release
  workflows). We never build firmware from source in a recipe.
- Our recipe is the source of truth for title, description, vendor,
  version and labels. unemupc's template build now builds its three
  firmware packages from our recipes, copied into
  `crates/pc-image/data/recipes/`; do not take metadata from
  `pc_image::template::FIRMWARE`.

## The recipe

- `[firmware]`: `name = "unemu/firmware/ibm-pc/<catalogue id>"`,
  `platform`, `revision`, `digest` once pinned.
- `[package]`: title, vendor, version, `date` (the date the ROM itself
  carries: its BIOS date or version string, quoted in a comment), labels,
  description; `[package.fits]` what the catalogue records.
- `[license]`: the catalogue's licence; copyleft needs `source = "<extra>"`.
- one `[[rom]]` with `role`, `catalogue_id`, `from` the ROM input,
  unmodified; the source archive as the one `[[extra]]`.
- Inputs: the release asset URLs, pinned by size and SHA-256.

## After pinning

Before publication, send the unemupc session every changed recipe and its
new lock files and digest, including changes to metadata alone, so its
copies in `crates/pc-image/data/recipes/` and template digest pins agree.
The same coordination applies to the FreeDOS recipes (open-media.md).

## Recipe artifact publication

Publish through the `publish` workflow with its default `mode=both`:
outputs first, then their recipe artifacts, in one approved run. Every
output under `unemu/firmware/**` must come from a pinned recipe of this
repository and have its recipe artifact under
`unemu/recipes/firmware/ibm-pc/<catalogue id>:rN` at the same revision
tag. An output without its published recipe is incomplete. Explicit
`mode=outputs` leaves it incomplete until the recipe follows;
`mode=recipes` supports recipe-only updates after the output's revision
tag serves its pinned digest. The recipe artifact names the output
digest; lookup is by name, with no reverse link from the output because
the recipe's lock contains that output digest. The unemu project pushes
nothing to the family registry; publication belongs to this repository.
The maintainer runs and approves the push job, including the first
push of each recipe; `ci-promote` may write `unemu/recipes/**` (2026-11-23).
The pipeline enforces the strict static check, exact guard plan and anonymous
output gate, then anonymously pulls every pushed recipe and compares all
files and bytes with the repository. No experimental recipe publication or
alias is allowed. See SKILL.md §5 and `docs/publishing.md`.
