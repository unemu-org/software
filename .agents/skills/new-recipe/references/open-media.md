# Open media sets

Published to `unemu/media/ibm-pc/<work>:<release>-rN` (alias `<release>`).
Examples: `recipes/media/freedos/` (disk images as released, copyleft,
source archive), `recipes/media/kenslab/` (archive without disks, freeware
terms).

## Shapes

| Upstream ships | Recipe | Example |
| --- | --- | --- |
| Disk images (`.img`, `.ima`) inside an archive | `unpack` step listing the image members with size and SHA-256; each `[[medium]]` takes one image unmodified | freedos-1.4*.media.toml |
| Files in an archive, no disks | `unpack` with `complete = true` and every member's path, size, SHA-256 and time; a `fat` step with `order = "listed"`, `format`, `label`, `serial`, `time` (the recipe's own, cited) | kenslab-2.01.media.toml |
| Several disks | one `[[medium]]` per disk, in installer order, labelled "Disk N of M - ..." | freedos |
| A CD image | an ISO input taken unmodified as the medium (`iso` format part in the release when not the release's own) | - |

Files placed on a built disk are unmodified upstream bytes. Never patch,
re-encode, rename or reorder upstream content beyond what the `fat` step
does.

## Licences

- **Open source**: an SPDX expression naming every licence actually present
  (`GPL-2.0-only AND MIT AND ...`), one entry per program in the licence
  index. Packages whose licence cannot be verified keep the whole set
  unpublished.
- **Freeware/shareware under its own terms**: `LicenseRef-<Name>` with
  `terms` pointing at the extra carrying the vendor's terms text,
  unmodified (`README.TXT`, `VENDOR.DOC`, `ORDER.FRM`, ...). The terms must
  allow redistribution of exactly what the set holds (a shareware episode,
  not the registered data). Note distribution conditions (not for profit,
  all files together) in the description.
- **Copyleft** content needs its corresponding source archive as an extra,
  `[license] source = "<extra id>"`. If no step can make it (git, ISO
  reading, tar), it is a **prepared input**: a script in
  `recipes/media/<work>/source/` builds it reproducibly in `nix develop`
  and checks its pin; the maintainer hosts it as a release asset of this
  repository; the recipe names that URL. See the FreeDOS source script and
  `tests/recipes_test.py`.

## Labels and description

Labels: kind (`game`, `operating-system`, `application`, `utility`,
`development`, `driver`), licence class (`open-source`, `freeware`,
`shareware`, `public-domain`), plus a few genre words. The description is
factual: what the title is, what the disks hold, how to install it, the
distribution terms in one sentence.
