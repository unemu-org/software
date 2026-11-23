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
  lock: no files, no extracted text, no manuals, no screenshots. This
  includes metadata: take no text or descriptive facts from the title's
  own files. Requirements and descriptive facts come only from public
  sources (publisher page, Wikidata, MobyGames), cited with URL and
  retrieval date, or are left out. Do not decode or quote a title's files
  to fill metadata. Disk order and labels may use scaffold's volume
  labels and member names, without quoting or describing file contents;
  when scaffold does not provide them, use neutral labels or omit them.
- **No URLs** in any input, and no link to any download of the title
  anywhere in the repository (AGENTS.md, "Only authorised sources").
  Information pages (Wikidata, MobyGames, the publisher's page) may be
  cited as metadata sources.
- **No `[license]`**: its absence makes the recipe user-supplied, and unemu
  names its output under `private/`. Never under `unemu/`.
- Inputs are sizes, SHA-256s and dump database ids (`ids = [{ db =
  "redump"|"tosec"|"no-intro"|"mame", set, name }]`). Files under 256
  bytes: their hash discloses their content; unemu's `disclosable` guard
  applies.
- Only bundled text of our own (a note, never the title's documents): a
  regular file directly beside the recipe, no link or file set, UTF-8
  without NUL, at most 1 MiB. Static checks enforce these limits;
  authorship must be reviewed.

## Writing it

1. Consult unemupc's private corpus in
   `backlog/docs/design/recipe-corpus/`: `windows-3.0`, `azuramon`.
   `crates/pc-image/data/recipes/` holds open examples only; never copy
   their `[license]` into a private recipe.
2. **Scaffold first, always**: the maintainer runs
   `unemu recipe scaffold FILE|FOLDER...` on their own copies, or a worker
   does so with read-only access to the maintainer's library. Start from
   its `[input]` tables and member lists; renaming ids is fine. Hand-measured
   inputs or a hand-rolled reader of a title's files are a review failure.
   We never download the title or copy it into the worktree. TOSEC-style
   names are hints, not verified provenance or dump database ids: version,
   edition and `(Disk N)` order may follow an explicitly stated name hint,
   with public sources cited where available. Scaffold treats raw images
   as plain files; it does not supply their volume labels or file contents.
3. Remove any old digest and lock, then pin with
   `unemu recipe pin FILE --library "$LIBRARY" --store "$S1"`. Pin a copy
   of the same draft (with its work file and our own bundled texts) again
   with a fresh store `$S2`; recipe and lock must be byte-identical.
   In unemu 0.0.4 `recipe build` has no `--create`: create each store first
   with `unemu library scan "$EMPTY" --store "$S1"` (repeat for `$S2`),
   where `$EMPTY` is an empty scratch folder. Run with a fresh `HOME`,
   `LC_ALL=C`, `TZ=UTC`. Always pass `--store` on store-using commands:
   `recipe plan` without it writes its library index in the user's default
   store. Both gaps are filed as `upc-5pw.113.15` in unemupc.
4. Decode and check what the lock holds before committing: manifest,
   config and metadata only - no payload byte, no document text of the title.
5. `python3 -I lib/plan.py --cli out/unemu/unemu FILE --library "$LIBRARY" --store "$S3"`
   shows whether a library is complete (`ready`). Repeat without the
   library in another fresh store: the title's inputs must be `missing`.
6. `python3 -I lib/recipes.py check --cli out/unemu/unemu FILE` checks the
   description and lock without the title's files: no licence statement,
   no input `urls` key (even empty), valid bundled text, no draft, finding
   or work-file drift, and an `ok` line for the recipe and its work file.
   unemu validates the lock, including the prohibition on payload bytes.
7. `unemu recipe push FILE unemu/recipes/media/<platform>/<work>:<tag> --dry-run --store "$S3"`
   must report `guard:allowed` and sends nothing. Firmware uses
   `unemu/recipes/firmware/<platform>/<catalogue id>:<tag>`. The recipe
   guard allows only the text bundle (recipe, work file, our own bundled
   texts, lock); about texts need sources, and every file input, file-set
   entry and archive member under 256 bytes needs `disclosable = true`.
   `check` and `scaffold` do not accept `--store`; create every fresh store
   used above with `library scan` first.

Recipe format 1.0 is frozen (unemupc decision-43). Publishing recipe
artifacts still requires the maintainer's later decision; this migration
adds no recipe push step. Recipes for private outputs are public text under
`unemu/recipes/...`; outputs under `private/` never leave the user's own
registries. A successful dry run does not authorise a push.

CI runs the same static check over all recipes. `lib/changed.py` includes
private recipes for static checking; its `--open-only` selection excludes
them from builds, fetching and conformance. `lib/publish.py` skips them by
default and refuses any explicit selection containing one, including a
mixed folder. Experimental and publication workflows never build or
publish their outputs. The repository holds and checks descriptions only.

## Users

Users build with `unemu recipe build FILE --library <their files> --store
DIR` after creating a fresh store as above, and push to their own registry.
The usage is `unemu image push NAME:TAG [REFERENCE] --store DIR [--registry HOST]`:
`unemu image push private/media/ibm-pc/<work>:<tag> <their-registry-ref> --registry HOST --store DIR`.
