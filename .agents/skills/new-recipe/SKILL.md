---
name: new-recipe
description: Use when adding or changing a recipe in this repository (unemu-org/software) - a new media set (DOS/Windows-era software, games, operating systems on floppy or CD), a firmware package, a new release or revision of an existing one, or a private recipe for a commercial title. Trigger on "add a title", "new package", "new recipe", "package this game", "add the shareware/freeware version of", "add firmware", "replace/re-pin a published set". Covers choosing the kind, licence research, authorised sources, metadata, writing the TOML, pinning, verification and publication.
---

# Adding a recipe

A recipe is a upcli recipe file (TOML 1.0, closed steps, inputs by size and
SHA-256, a lock) that builds one media set or one firmware package. Read
`AGENTS.md` (hard rules) and `docs/recipes.md` (format) before writing one;
this skill is the procedure, they are the rules.

## 1. Decide whether it may exist at all

Answer these before writing a byte. If one answer is "no" or "unsure", stop
and ask the maintainer; record the question in the bead.

1. **Who holds the rights, and what did they allow?** Find the rights
   holder's own statement: for open inputs, a licence file in the release;
   otherwise the author's page, a press release, a registry of
   freeware/shareware terms. For private titles use public sources only.
   Quote it
   in the bead notes with its URL and date. "Abandonware", "everyone has
   it" and third-party claims are not permission.
2. **Which kind is it?** (pick the reference file)
   - open source (SPDX licences), freeware or shareware under its own
     terms that allow redistribution: an **open media set**, published to
     `unemu/media/ibm-pc/<work>` - [references/open-media.md](references/open-media.md)
   - a ROM for unemupc's machine templates: a **firmware package** -
     [references/firmware.md](references/firmware.md)
   - anything else (commercial, registered versions, no redistribution
     permission): a **private recipe** - its outputs are never built by CI
     or published here - [references/private.md](references/private.md)
3. **Where do the bytes come from?** Only from a source that distributes
   them with the rights holder's permission (AGENTS.md, "Only authorised
   sources"): the author's or publisher's own site or release, an official
   mirror, an approved archive, or an archive.org `id_` capture of one of
   these. Never an abandonware or warez site, even for a legitimate title.
   A private recipe names no URL at all.
4. **Does it fit the open rule?** Every program in the set is covered by a
   stated licence; copyleft content brings its corresponding source;
   documents without a stated licence are left out.

## 2. Bead and branch

- `bd create --parent um-7jx --type task` with the title, the rights
  statement, the sources and the kind; `bd update <id> --claim`.
- One bead, one branch (`feat/<bead>`), one signed commit. Bead text is
  public: no private paths, hosts or personal setup.
- Implementation goes to a worker (Codex by default); a fresh reviewer
  reads licence claims first; the supervisor verifies and commits only on
  the maintainer's word.

## 3. Write it

1. **Get the pinned upcli**: `python3 -I lib/upcli.py fetch out/upcli`.
   Run upcli with a fresh `HOME`, `LC_ALL=C`, `TZ=UTC`.
2. **Obtain the inputs**: download authorised open inputs into a fresh
   directory; private inputs stay in the maintainer's library, read-only.
   Treat all inputs as untrusted: never run them or extract into the tree.
3. **Scaffold first, always**: run `upcli recipe scaffold FILE|FOLDER...`
   on the inputs for every kind, including firmware and private recipes.
   Start from its `[input]` tables and member lists (sizes, hashes and
   archive times); ids may be renamed. Hand-measured inputs or a hand-rolled
   reader of a title's files are a review failure. For private recipes the
   maintainer runs it, or a worker reads the maintainer's library read-only.
4. **Name it** (`docs/recipes.md`, "Where recipes live"): `recipes/media/<work>/`
   with `<work>.work.toml`, or `recipes/firmware/<catalogue id>.firmware.toml`.
   Tag = `<release>-r<revision>`; the release is version, then edition,
   then format when not the release's own (`1.2m`, `720k`, `360k`, `iso`).
5. **Metadata** - cited values only, the source in a comment beside each:
   - work file: `[title]` (title, publishers, developers, genres, labels,
     synopsis in our own words), `[ids]` (Wikidata first;
     MobyGames and IGDB as Wikidata records them, with the property and
     retrieval date), `[needs]` only sourced values in unemupc's vocabulary
     (`cpu`, `fpu`, `video`, `sound`, `memory`, `ems`, `xms`, `input`);
   - open recipe `[title]`: version as the software shows it, edition,
     `requirements` quoted word for word (say which text, which section);
     `[needs]` only what the release's documents state;
   - private recipes take no text or metadata from the title's files:
     requirements and descriptive facts come only from public sources,
     cited with URL and retrieval date, or are omitted. See
     [references/private.md](references/private.md) for filename hints
     and scaffold's disk labels and order;
   - nothing guessed: a value without a source is left out.
6. **Licence index (open media only)**: `licenses.txt` beside the recipe (our own text,
   CC0-1.0, bundled, a `text` step with `eol = "crlf"` to `LICENSES.TXT`),
   naming every program, its licence and where its source is.

## 4. Pin and verify

The commands below are for open outputs; private recipes follow
[references/private.md](references/private.md) and are never built by CI
or `lib/publish.py`. All store-using commands must pass `--store`
explicitly. `check`, `show` and `scaffold` have no store option.
upcli 0.0.3 has no `recipe build --create`: create each fresh store first
with `upcli library scan "$EMPTY" --store "$S1"`, where `$EMPTY` is an
empty scratch folder (repeat for `$S2` and `$S3`). `library scan` creates
the store without importing an image. Both this gap and `recipe plan`
writing its library index in the default store when `--store` is absent
are filed as `upc-5pw.113.15` in unemupc.

```sh
upcli recipe check FILE && upcli recipe show FILE
upcli recipe build FILE --fetch --store "$S1"      # review: upcli image media show <tag> --store "$S1"
upcli recipe pin FILE --fetch --store "$S2"        # writes digest and FILE.lock/
# pin a copy of the draft again from $S2/cache/downloads only, new store: recipe and lock byte-identical (diff -r)
python3 -I lib/recipes.py check --cli out/upcli/upcli
python3 -I lib/publish.py --cli out/upcli/upcli --fetch FILE        # every row: dry run
upcli recipe conformance FILE --library "$S2/cache/downloads" --store "$S3"
python3 -I lib/plan.py --cli out/upcli/upcli FILE --store "$S3"    # ready / needs --fetch
upcli recipe push FILE unemu/recipes/media/<platform>/<work>:<tag> --dry-run --store "$S3"
```

For firmware use `unemu/recipes/firmware/<platform>/<catalogue id>:<tag>`.
The dry run sends nothing and must report `guard:allowed`: the bundle is
text only (recipe, work file, our bundled texts, lock), about texts need
sources, and private file inputs, file-set entries and archive members
under 256 bytes require `disclosable = true` before publication.

Then the README check command (`nix develop`), a fresh review, the
supervisor's own rerun, the commit (`-S`, `Refs: <bead>`), the PR. CI
rebuilds on Linux; its digests must equal the macOS pins.

## 5. Publish

**No recipe is pushed to any registry before unemupc's recipe format 1.0
freeze (`upc-5pw.113.17`).** Verification still includes the recipe push
dry run above. After the freeze, recipes of private outputs are public
text under `unemu/recipes/...`; their outputs under `private/` never leave
the user's registries. Publishing outputs and publishing recipes are
separate actions; neither is authorised by a successful dry run.

unemupc's template build builds its three firmware packages and FreeDOS
sets from our recipes, copied into `crates/pc-image/data/recipes/` in
unemupc. Our recipes are the source of truth. Any change to those recipes,
metadata included, must be sent to the unemupc session with the new files,
locks and digests before publication.

For open outputs, merge (fast-forward main to the signed commit after green checks);
`experimental` pushes `unemu/experimental/...:<release>-ci<RUN>-r1`; check
it anonymously; the maintainer runs and approves `publish`; verify the
revision tag and the alias anonymously; update the bd memory `published`;
coordinate the firmware and FreeDOS files and digests as described above.

## Pitfalls met so far

- For open media a version from a web page can differ from the one the
  software shows: use the software's (Ken's Labyrinth 2.01, page says v2.1).
  Private recipes use public sources or explicitly stated filename hints.
- "Word for word" means word for word: no joining of two sections.
- A licence text inside the game ("not shareware") can be superseded by a
  later release statement: say so in the licence index.
- Files under 256 bytes: their hash discloses them; mind upcli's
  `disclosable` guard in private recipes.
- Changing metadata changes the digest: before unemupc's first public
  release a published item is replaced (maintainer deletes its tag and
  alias), after it a new revision is needed.
- Report upcli misbehaviour to the unemupc session with a reproducer so it
  gets a bead. Exit 0 on drift is `upc-5pw.113.20`; `lib/recipes.py check`
  stays the CI guard meanwhile.
