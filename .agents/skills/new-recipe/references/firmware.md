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
- Title, description, vendor, version and labels are unemupc's words
  (`pc_image::template::FIRMWARE` in unemupc), word for word, so both
  repositories build one package. Changing them is coordinated with
  unemupc.

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

Tell the unemupc98 session the new digest: its template build pins the
package digests (`crates/pc-image/tests/template.rs`).
