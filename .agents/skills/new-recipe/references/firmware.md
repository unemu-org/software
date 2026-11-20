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
