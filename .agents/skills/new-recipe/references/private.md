# Private recipes (non-open titles)

A private recipe describes a title the repository may not distribute:
commercial releases, registered versions, anything without redistribution
permission. The user builds it from their own files into their own store
and registry. Output under `private/media/ibm-pc/<work>:<release>-rN` (or
`private/firmware/...`). Scope: bd memory `scope` (amended 2026-11-20).

Status: the CI path for these recipes (static check only, never built or
pushed) is bead um-7jx.24. Until it is merged, do not commit a private
recipe; drafts stay local.

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
- Only bundled text of our own (a licence-free note, never the title's
  documents).

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

## Users

Users build with `upcli recipe build FILE --library <their files> --store
DIR` and push to their own registry: `upcli image push <ref>
private/media/ibm-pc/<work>:<tag> --registry HOST --store DIR`.
