# AGENTS.md — software

Repo-specific notes for coding agents. Generic software-engineering advice is out of scope.

## What this is

Reproducible build recipes for the **open installation media sets** of the unemu
family: freely redistributable DOS (and later Windows-era) software as floppy or CD
sets, built from pinned upstream releases with their licences, corresponding source
archives and documentation, and published as OCI media sets to `unemu/media/ibm-pc/`
on the family's registry. The repository also builds the **open firmware packages**
of unemupc's machine templates, published to `unemu/firmware/ibm-pc/<catalogue id>`: each
ROM and its source archive come from the release assets of the unemu-org forks,
pinned and unmodified. The consumer is **unemupc** (an IBM PC/AT-class emulator;
beads prefix `upc`). The family CLI **unemu** (github.com/unemu-org/unemu),
whose recipe commands (`unemu recipe check`, `pin`, `build`, `conformance`),
importers and push guard define the formats and publication rules, builds every
set and package from a recipe file in `recipes/` with its lock; see `README.md` and `docs/recipes.md`.

The repository also holds **private recipes** for commercial and other non-open
titles: metadata, sizes, SHA-256s, dump database ids and steps, with no `[license]`
and no input URLs. Their outputs are named under `private/media/ibm-pc/<work>` or
`private/firmware/ibm-pc/<id>`. Only the descriptions and locks live here, never
the title's files; users build from their own libraries. These recipes receive
static checks only: the repository never builds, fetches or publishes their outputs.

## Language

- Talk to the maintainer in Polish.
- Everything committed is English: code, comments, docs, commit messages, bead text.

## Work tracking

Open work lives in **beads** (`bd`, prefix `um`): `bd ready`, `bd show <id>`.
The shared working rules are beads memories; read them at session start with
`bd memories` (`bd recall <key>` for one): roles, media-rules, build-and-release,
publication-rule, bead-workflow, consumer. Cross-repository ids carry a tag, e.g.
`upc-5pw.68 (unemupc98)`; an untagged id is ours.

## Hard rules

- **Never commit without the maintainer's request** (may be granted for a session).
  `bd init` and some bd commands commit on their own: check `git log -1` after them.
- **Upstream bytes stay upstream bytes**: disk images are byte-identical to the
  upstream release, pinned by SHA-256; files placed on built images are unmodified.
  Downloads are untrusted data: verify every hash before use, never execute anything
  from a download, extract only listed members, into a fresh directory. The one
  exception is the unemu release (github.com/unemu-org/unemu) named in
  `unemu.toml`. In CI, unemu's own `setup-unemu` action, pinned to that
  release's commit, checks the archive against the release's SHA256SUMS and
  the `commit:` line of `unemu --version` against the pinned commit (the
  maintainer's decision of 2026-11-23). Locally, `lib/unemu.py fetch` verifies
  the archive and its `unemu` member against our own SHA-256 and size,
  extracts only that member into a fresh directory, and checks the commit
  line before any other use. Any other downloaded executable stays forbidden;
  building unemu from source at the pinned commit stays possible.
- **Open only when every part is covered**: an SPDX expression stating the real
  licences, a licence index for every program, and for copyleft content the
  corresponding source archive linked from every copyleft layer. Documents without a
  stated licence are not attached. A firmware package is one program: it is open
  with an SPDX statement equal to the licence unemupc's firmware catalogue records
  for its ROM and, for copyleft firmware, its corresponding source archive attached.
  When in doubt, the set or package is not published.
- **Only authorised sources**: an input's URL points only to a source that
  distributes those bytes with the rights holder's permission: the author's or
  publisher's own site or release, an official mirror, an archive the rights holder
  approved, or a capture of one of these (archive.org). Never a site that offers the
  software without that permission ("abandonware" sites, warez, unofficial uploads),
  even when it also hosts legitimate titles. A recipe without an open licence (its
  output under `private/`) names no URL at all: only sizes, SHA-256s and dump
  database ids (Redump, TOSEC, No-Intro); the user supplies the files from their own
  library. Information pages (Wikidata, MobyGames, the publisher's page) may be
  cited as sources of metadata.
- **Reproducible**: the same recipe gives the same images, archives and set digests
  on every run and host; no wall clock, no locale or user-config dependence. A recipe
  is pinned with the unemu of `unemu.toml` (`unemu recipe pin`: its `digest` and its
  lock) and every build is checked against that lock; run unemu with a fresh HOME,
  LC_ALL=C and TZ=UTC. The one script that makes a prepared input (the FreeDOS
  source archive) runs in `nix develop` and checks its output against its pin.
- **Publication rule**: write as if public; no private hosts, paths or personal
  setup; never create or publish a repository, which is the maintainer's step.
  Pushing sets to the registry is the maintainer's step unless granted for a session.

## Quick loop

```sh
python3 -I lib/unemu.py fetch out/unemu        # local: our SHA-256 and size pins, then commit line
python3 -I lib/recipes.py check --cli out/unemu/unemu
python3 -I lib/publish.py --cli out/unemu/unemu --fetch   # build all, dry run
nix develop        # pinned curl, unzip, git, GNU tar, CA roots, python3, shellcheck
```

Open work: `bd ready`.

<!-- Beads integration is added below by bd init. -->

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:1105d646 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

<!-- BEGIN BEADS CODEX SETUP: generated by bd setup codex -->
## Beads Issue Tracker

Use Beads (`bd`) for durable task tracking in repositories that include it. Use the `beads` skill at `.agents/skills/beads/SKILL.md` (project install) or `~/.agents/skills/beads/SKILL.md` (global install) for Beads workflow guidance, then use the `bd` CLI for issue operations.

### Quick Reference

```bash
bd ready                # Find available work
bd show <id>            # View issue details
bd update <id> --claim  # Claim work
bd close <id>           # Complete work
bd prime                # Refresh Beads context
```

### Rules

- Use `bd` for all task tracking; do not create markdown TODO lists.
- Run `bd prime` when Beads context is missing or stale. Codex 0.129.0+ can load Beads context automatically through native hooks; use `/hooks` to inspect or toggle them.
- Keep persistent project memory in Beads via `bd remember`; do not create ad hoc memory files.

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/core-concepts/sync-concepts.md for details and anti-patterns.
<!-- END BEADS CODEX SETUP -->
