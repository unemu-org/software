# AGENTS.md — unemu-media

Repo-specific notes for coding agents. Generic software-engineering advice is out of scope.

## What this is

Reproducible build recipes for the **open installation media sets** of the unemu
family: freely redistributable DOS (and later Windows-era) software as floppy or CD
sets, built from pinned upstream releases with their licences, corresponding source
archives and documentation, and published as OCI media sets to `unemu/media/` on
the family's registry. The consumer is **unemupc98** (an IBM PC/AT-class emulator,
beads prefix `upc`), whose `image media import` / template build and push guard
define the set format and the publication rules. See `README.md`.

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
  from a download, extract only listed members, into a fresh directory.
- **Open only when every part is covered**: an SPDX expression stating the real
  licences, a licence index for every program, and for copyleft content the
  corresponding source archive linked from every copyleft layer. Documents without a
  stated licence are not attached. When in doubt, the set is not published.
- **Reproducible**: the same recipe gives the same images, archives and set digests
  on every run and host; no wall clock, no locale or user-config dependence (run
  mtools with an empty HOME, LC_ALL pinned, TZ=UTC).
- **Publication rule**: write as if public; no private hosts, paths or personal
  setup; never create or publish a repository, which is the maintainer's step.
  Pushing sets to the registry is the maintainer's step unless granted for a session.

## Quick loop

```sh
nix develop        # pinned curl, unzip, mtools, CA roots, python3
```

The recipes, the set builder and the checks are being set up; see `bd ready`.

<!-- Beads integration is added below by bd init. -->
