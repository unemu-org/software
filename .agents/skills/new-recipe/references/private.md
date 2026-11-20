# Private recipes (non-open titles)

A private recipe describes a title the repository may not distribute:
commercial releases, registered versions, anything without redistribution
permission. The user builds it from their own files into their own store
and registry. Output under `private/media/ibm-pc/<work>:<release>-rN` (or
`private/firmware/...`). Scope: bd memory `scope` (amended 2026-11-20).

Layout: `recipes/private/media/<work>/<work>-<release>.media.toml`, with
`<work>.work.toml`, our own bundled texts and the lock beside it; firmware
descriptions use `recipes/private/firmware/<catalogue id>.firmware.toml`.
This separates descriptions from the open recipes and prepared-input scripts.

## Hard limits

- **No bytes** of the title in the repository, the bead, the PR or the
  lock: no files, no extracted text, no manuals, no screenshots.
- **No URLs** in any input, and no link to any download of the title
  anywhere in the repository (AGENTS.md, "Only authorised sources").
  Information pages (Wikidata, MobyGames, the publisher's page) may be
  cited as metadata sources.
- **No `[license]`**: its absence makes the recipe user-supplied, and upcli
  names its output under `private/`. Never under `unemu/`.
- Inputs are sizes, SHA-256s and dump database ids (`ids = [{ db =
  "redump"|"tosec"|"no-intro"|"mame", set, name }]`). Files under 256
  bytes: their hash discloses their content; upcli's `disclosable` guard
  applies.
- Only bundled text of our own (a note, never the title's documents): a
  regular file directly beside the recipe, no link or file set, UTF-8
  without NUL, at most 1 MiB. Static checks enforce these limits;
  authorship must be reviewed.

## Writing it

1. Start from unemupc's examples (`crates/pc-image/data/recipes/` and the
   corpus in its `backlog/docs/design/recipe-corpus/`: `windows-3.0`,
   `azuramon`).
2. The maintainer (or the user) runs `upcli recipe scaffold` on their own
   copies to get sizes, hashes and member lists, and `upcli recipe pin
   FILE --library <their files>` to pin it. We never download the title.
3. Check what the lock holds before committing: manifest, config and
   metadata only - no payload byte, no document text of the title.
4. `python3 -I lib/plan.py --cli out/upcli/upcli FILE --library <files>`
   shows whether a library is complete (`ready`) or what is `missing`.
5. `python3 -I lib/recipes.py check --cli out/upcli/upcli FILE` checks the
   description and lock without the title's files: no licence statement,
   no input `urls` key (even empty), valid bundled text, no draft, finding
   or work-file drift, and an `ok` line for the recipe and its work file.
   upcli validates the lock, including the prohibition on payload bytes.

CI runs the same static check over all recipes. `lib/changed.py` includes
private recipes for static checking; its `--open-only` selection excludes
them from builds, fetching and conformance. `lib/publish.py` skips them by
default and refuses any explicit selection containing one, including a
mixed folder. Experimental and publication workflows never build or
publish their outputs. The repository holds and checks descriptions only.

## Users

Users build with `upcli recipe build FILE --library <their files> --store
DIR` and push to their own registry: `upcli image push <ref>
private/media/ibm-pc/<work>:<tag> --registry HOST --store DIR`.
