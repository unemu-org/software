# Publishing

`lib/publish.py` builds the recipes ([recipes.md](recipes.md)) with the
pinned unemu CLI, `unemu`, into a store, checks every set and package
against its recipe's lock and digest, and pushes them to the registry only
when asked. Without `--push` it is a dry run: it needs no credentials and
writes to no registry. Output mode reads one only when `--skip-published`
asks; recipe mode also reads the open output's revision tag.

Private recipes in `recipes/private/` describe user-supplied titles only.
Their outputs under `private/media/` or `private/firmware/` are never built,
fetched, conformed or published by this pipeline, including experimental
runs. Output mode's default selection skips them; any explicit file, ref or folder
selection containing one is refused before unemu or a registry is called.
`lib/recipes.py check` checks them statically, alongside open recipes.
Recipe mode can publish their public descriptions (see below).

## Recipe artifacts

Everything under `unemu/media/**` and `unemu/firmware/**` on the family
registry is the output of a pinned recipe of this repository. Each output
must have its recipe artifact under `unemu/recipes/...` with the same revision
tag. An output without its published recipe is incomplete. Publication
belongs to this repository; the unemu project pushes nothing to the family
registry.

The link is one-way: the recipe artifact names the output digest. The output
cannot name its recipe because the recipe's lock contains the output digest.
Lookup is by name: `unemu/media/X:tag` maps to `unemu/recipes/media/X:tag`,
and firmware follows the same rule.

The maintainer's decision of 2026-11-23 grants `ci-promote` write access to
`unemu/recipes/**`. Recipe format 1.0 is frozen. Recipe artifacts publish
only through the approved `publish` workflow; a recipe's first push needs
the maintainer's approval of that run. The artifact contains the recipe,
its work file, bundled texts and lock files, with no payload bytes.

The REF is `unemu/recipes/` followed by the output's path after its first
component, with the output's revision tag:

| Output | Recipe artifact |
| --- | --- |
| `unemu/media/ibm-pc/kenslab:2.01-r1` | `unemu/recipes/media/ibm-pc/kenslab:2.01-r1` |
| `unemu/firmware/ibm-pc/xub-r638-xtp-ata1f0:r1` | `unemu/recipes/firmware/ibm-pc/xub-r638-xtp-ata1f0:r1` |
| `private/media/ibm-pc/windows:3.1-r1` | `unemu/recipes/media/ibm-pc/windows:3.1-r1` |

There is no experimental recipe publication: unemu's guard refuses every
other place, including `unemu/experimental/recipes/`, release aliases and
CI tags. Recipes of private outputs are public text under `unemu/recipes/`;
their outputs under `private/` never leave the user's registries.

```sh
python3 -I lib/publish.py --cli UNEMU --recipes --report recipes.json
python3 -I lib/publish.py --cli UNEMU --recipes --guard-only  # offline CI check
```

Recipe mode defaults to every open AND private recipe; file, folder and
output REF selectors work as in output mode. The shared recipe loader rejects
duplicate derived recipe REFs across all recipes under `recipes/`, before
selection, including `--guard-only` and CI's static recipe check. It rejects output options
`--fetch`, `--library`, `--no-build`, `--experimental` and `--skip-published`.
`--store DIR` must name a fresh directory (otherwise a temporary store is
used), initialized with `unemu library scan` of an empty folder. The CLI
runs with a fresh HOME, `LC_ALL=C`, `TZ=UTC` and the run's public registry
configuration; an explicit CA bundle can be supplied with `SSL_CERT_FILE`
(also accepted from `NIX_SSL_CERT_FILE` or `CURL_CA_BUNDLE`). Recipe dry run
constructs an HTTPS client and needs trusted CA roots, but sends no request.

For each selection, `lib/recipes.py`'s static check must pass with no draft,
finding, drift or missing `ok` line for the recipe and its work file.
`unemu recipe push FILE REF --dry-run --store DIR` must report exactly the
derived target on the configured host, the pinned output REF and digest,
the recipe artifact digest and `guard:allowed`. For an open recipe, an
anonymous registry read must then find exactly the pinned output digest at
its revision tag; an absent output, another digest or a failed read refuses
publication. Private recipes have no output gate: the static check and
guard suffice, without looking for or fetching the title's files.

`--guard-only` defers the open output gate so PR checks and the build job
can check recipes before their outputs exist. Its outcome is `guard only`,
and it cannot be combined with `--push`. A normal dry run includes the
registry gate. Both print REF, recipe artifact digest, output digest, gate
state and outcome, also available with `--report FILE` as JSON. The JSON
retains the output `ref`, and adds `target`, `recipe_digest`, `output_digest`
and `gate`. Nothing is pushed if any selected recipe fails.

In the approved push step, `--recipes --push` repeats these checks and
runs `unemu recipe push FILE REF --store DIR`. Its last line must be
`pushed\tTARGET\tIMAGE_DIGEST`, `unchanged\tTARGET\tIMAGE_DIGEST`, or
`moved\tTARGET\tFROM -> IMAGE_DIGEST (the same output OUTPUT_DIGEST)`
(with literal tabs). `TARGET` is the host-qualified reference from the dry
run's `target:` line. The `moved` form must explicitly name the same pinned
output digest and expected recipe artifact digest. A recipe tag can move
only between recipes of that same output digest; another output digest is
refused by unemu. The pipeline checks the reported target and digests too.
Credentials come only from the environment and reach only the push call.
The shared CLI runner replaces non-empty registry password and user values
with `***` in captured stdout and stderr before either push path can relay
them to logs, errors or summaries.

After every successful push, including unchanged and moved outcomes,
`unemu recipe pull REF --to DIR` runs anonymously into a fresh folder. Its
reported digest must match the dry-run artifact. Every file must have the
same bytes as its repository counterpart relative to the recipe's folder:
recipe, work file, bundled texts and lock files. A missing, differing or
extra file, or a symlink, fails confirmation (`unconfirmed`) and stops later
pushes. Recipe artifacts are rebuilt from the push job's checkout; no
recipe store or recipe bytes from the build hand-over are trusted.

## The pinned unemu

### unemu.toml

[`unemu.toml`](../unemu.toml) pins one unemu release. Its keys are exactly
these; `lib/unemu.py` refuses any other key, a missing or malformed one, and
(as TOML does) a key given twice.

| Key | What it holds |
| --- | --- |
| `commit` | the full commit the release's tag points to |
| `tag`, `version` | the release's tag (`v0.0.4`) and version (`0.0.4`) |
| `[archive.<triple>]` | one table per platform: `x86_64-unknown-linux-gnu`, `aarch64-apple-darwin` |
| `file` | the archive's name, `unemu-<version>-<triple>.tar.gz` |
| `sha256`, `size` | the archive's SHA-256 and size in bytes |
| `unemu_sha256`, `unemu_size` | the SHA-256 and size of its member `unemu-<version>-<triple>/unemu` |

These hashes are our own pins for local use by `lib/unemu.py fetch`,
which never checks against the release's `SHA256SUMS`. CI uses the
separate action route below and does not check our archive or member pins.

### Getting unemu

Locally, on a Linux x86_64 or macOS arm64 host:

```sh
python3 -I lib/unemu.py fetch out/unemu
```

`fetch` takes a new directory (it refuses one that exists) and:

1. reads the host's archive into memory once, from `cache/unemu/`, or else
   downloads it from the GitHub release over HTTPS (curl) into a temporary
   directory;
2. checks those bytes against the pinned size and SHA-256, and uses only
   those bytes from then on; a download is written into the cache only once
   checked, and a cached copy that differs is refused, never replaced;
3. takes out only the member `unemu-<version>-<triple>/unemu`: the one
   member of that name, a regular file, with the pinned size and SHA-256 (an
   archive that names any member by an absolute path or with `..` is
   refused whole), and writes it into the directory with mode 0755;
4. runs its `--version` (only `PATH`, a fresh `HOME` and `TMPDIR`,
   `LC_ALL=C`, `TZ=UTC`, in a scratch directory) and checks the commit line
   against the pin; on any failure the directory is removed again;
5. prints the binary's path.

This is the one downloaded program the repository runs (see the hard rules
in [AGENTS.md](../AGENTS.md)). Any other host is refused. Under an x86_64
Python on Apple silicon (Rosetta) the host reads as macOS x86_64, so pass
`--triple aarch64-apple-darwin`. `--cache DIR` keeps the archive elsewhere.

In CI, every job that needs unemu installs it with upstream
`unemu-org/unemu/.github/actions/setup-unemu`, pinned by full SHA to the
release commit in `unemu.toml`. Its `version` input equals `tag` (`v0.0.4`),
and its `commit` input equals `commit`. The action downloads the release
archive and its `SHA256SUMS`, checks the archive against that list, then
runs `unemu --version` and checks its `commit:` line against the input.
This checks the download against the release's own sums; it is not a check
against our archive or member pins. The maintainer chose the action alone
on 2026-11-23, with no extra hash check in CI. Later steps receive its
absolute `path` output through `env`, then use the quoted shell variable.
The push job installs unemu afresh, never from the build artifact.

At this release the action supports Linux X64 and documents glibc 2.35+
and the ALSA runtime (`libasound2`) as requirements. The workflows use
`ubuntu-latest` without an apt step; availability of that runtime remains
to be confirmed by the first CI run.

Or build it from source, in a clean unemu checkout at the pinned commit
with no local changes, inside unemu's devShell:

```sh
UNEMUPC_SOURCE_COMMIT=$(git rev-parse HEAD) cargo build -p window --bin unemu
```

Without `UNEMUPC_SOURCE_COMMIT` the build prints no commit line (unemu's
`crates/platforms/src/lib.rs`, `commit_line`) and the pipeline refuses it.

### The version check

Before anything is checked or built, the pipeline runs `unemu --version`
once and reads its stdout. Exactly one line may start with `commit:`, and it
must be `commit: ` followed by the 40 lower-case hex digits of the pinned
commit. Another commit, a malformed or repeated commit line, a failing
`--version`, or no commit line at all refuses the run, a dry run as well as
a push. There is no option to skip it. Nothing else in the output is read.
`lib/recipes.py check` makes the same check.

### Moving the pin

1. Take the commit from the new release's tag.
2. Download each archive and measure its SHA-256 and size; read its `unemu`
   member out of the checked archive, without running it, and measure that
   too. Never copy a hash from the release's `SHA256SUMS`.
3. Update `unemu.toml` and every `setup-unemu` use in `check.yml`,
   `experimental.yml` and `publish.yml` together: the action SHA and
   `commit` input equal the TOML `commit`, and `version` equals its `tag`.
   Run the README check command; `tests/changed_test.py` checks agreement,
   full SHA pinning of every action, and the absence of `lib/unemu.py fetch`
   in workflows.
4. Fetch the binary locally and run `lib/recipes.py check`,
   conformance over the open recipe folders with `--library DIR`, and the dry run. A
   later release builds every recipe from its lock, so every digest must
   stay as pinned; a release that cannot build one of them is not taken.

## Names and tags

Every repository names the machine platform, `ibm-pc`, after its kind
(unemupc's decision-35), and tags follow its decision-38:

| Kind | Repository | Revision tag | Alias |
| --- | --- | --- | --- |
| media set | `unemu/media/ibm-pc/<work>` | `<release>-rN` (`freedos:1.4-r1`, `:1.4-720k-r1`) | `<release>` (`freedos:1.4`) |
| firmware package | `unemu/firmware/ibm-pc/<catalogue id>` | `rN` (`r1`) | none |

A tag that ends in `rN` names one set or package for ever and never moves.
A set's release alone is an alias that names the newest revision of that
release: the pipeline pushes it right after the revision, and unemu lets
an alias onto the newest revision the registry holds only. Moving an alias
to a newer revision takes `unemu image push --move`, which this pipeline
never passes and does not support yet, so an alias that names another
image is `refused` and the maintainer decides. No further revision is
published before unemupc's first public release (its decision-32, point
5): until then a corrected set or package replaces the published one under
the same tags, in the order of [Replacing a published set or
package](#replacing-a-published-set-or-package). Sets and
packages have no `latest`. Our locked artifacts retain their original
`unemu.min-app-version` 0.0.3 annotation under unemu 0.0.4. `unemu recipe build` tags a set or package
under its own ref in the store (and a set's alias with it); every push
names its `unemu/...` target explicitly.

`unemu image push --dry-run` is local: it checks the guard, but no tag
rule, which only a push checks on the registry. The pipeline derives every
tag itself from the recipe.

### Replacing a published set or package

No further revision is published before unemupc's first public release
(its decision-32, point 5): until then a corrected set or package replaces
the published one under the same tags. A revision tag never moves and the
pipeline never moves an alias, so the order is exactly this:

1. **The prepared inputs.** Create the release asset a changed recipe
   downloads (for FreeDOS the source archive, release
   `freedos-1.4-floppy`), or confirm it is there:
   `recipes/media/freedos/source/build.sh` rebuilds the archive and stops
   unless it has the recipes' pin, and the recipes build from it.
2. **Merge.** The check workflow builds every changed recipe against its
   lock; the experimental workflow pushes it to `unemu/experimental/`. The
   old official tags are still published then: an experimental run only
   notes them and pushes its experimental tags, so the replacement is
   tested there first.
3. **Delete the old tags.** The maintainer deletes on the registry, for
   every changed set or package, its revision tag and, for a set, its
   release alias too (`freedos:1.4-r1` and `freedos:1.4`): either one left
   in place would refuse the publication.
4. **Publish.** Run publish.yml and approve its push job.

## The pipeline

```sh
python3 -I lib/publish.py --cli UNEMU [--store DIR] [--fetch] [--library DIR]...
                          [--no-build] [--experimental RUN] [--skip-published]
                          [--push] [--report FILE] [RECIPE|FOLDER|REF]...
```

| Option | Meaning |
| --- | --- |
| `--cli UNEMU` | the unemu binary; default `$UNEMU`; a name without a slash is looked up on `PATH` |
| `--store DIR` | build into `DIR`, a new directory that is kept (default: a fresh store under `build/`, removed afterwards); with `--no-build`, the existing store an earlier run built, which unemu never reads (see the steps) |
| `--fetch` | let `unemu recipe build` download a missing input from the URLs its recipe pins |
| `--library DIR` | a folder where `unemu recipe build` looks for inputs by size and SHA-256 (repeatable) |
| `--no-build` | build nothing: every selected set and package must already be in `--store` under its own ref, every byte of it checked against its pin |
| `--experimental RUN` | push to the experimental targets (see below); `RUN` is the CI run id, from 1 |
| `--skip-published` | read each pinned set's and package's own tag (and a set's alias) first; skip what the registry already serves (see below) |
| `--push` | push every checked set and package |
| `--report FILE` | also write the summary's rows as JSON (the CI reads whether anything is left to push) |
| `RECIPE`, `FOLDER` or `REF` | select an open recipe file (`recipes/media/freedos/freedos-1.4.media.toml`), recipes below a folder (`recipes/media/freedos`) or one set or package by its ref (`unemu/media/ibm-pc/freedos:1.4-r1`, `unemu/firmware/ibm-pc/xub-r638-xtp-ata1f0:r1`; not an alias); default: every open recipe; an explicit selection including a private recipe is refused |

The registry host is `UNEMU_REGISTRY_HOST`, checked as unemu checks one (a
name with a dot or a port, or `localhost`; no scheme or path). A dry run
without it names `registry.unemu.dev`, which it contacts only for the
anonymous reads of `--skip-published`.

### Steps

A set or package that fails a step is not taken further.

1. Every recipe file is read (`lib/recipes.py`); private recipes are
   excluded from subsequent steps. An open recipe's identity table names
   a repository under `unemu/media/` or `unemu/firmware/` and a tag of
   decision-38, and no ref, nor a set's release alias, appears twice. A
   refusal stops the run.
2. The version check (above), then `unemu recipe check` over every open recipe:
   a finding, or a recipe without its own `ok` line, stops the run. Drift
   (a work file that differs from a lock)
   is reported; a build writes the lock's record all the same, and CI
   refuses drift before anything is published (`lib/recipes.py check`).
3. With `--skip-published`, each selected set or package that has a digest
   pin is looked up under its own tag,
   `unemu/media/ibm-pc/<work>:<release>-rN` or
   `unemu/firmware/ibm-pc/<id>:rN`, with `--experimental` too, and a set
   then under its own alias, `<release>`: anonymous reads, as before a
   push. The pinned digest under both makes it `published`: it is not
   built or pushed. Another digest under its tag or under the alias makes
   it `refused`: a revision tag never moves and the pipeline never moves
   an alias, so the refusal says to stop and ask the maintainer. With
   `--experimental` it refuses nothing: the set or package goes on to its
   experimental tags, and its row's detail notes what the official tag or
   alias still serves (`note: ... still serves ...; replacing it needs the
   maintainer`), so a replacement can be tested before the old tags are
   deleted. A missing
   tag goes on as usual, and so does a missing alias of a tag that serves
   the pin (the set is built again and only the alias is pushed), and a
   draft, which cannot be compared and ends `unpinned`. A failed read fails
   it.
4. Each selected set or package is built with `unemu recipe build FILE
   --store STORE` (with `--fetch` and `--library` as given). unemu finds
   every input by size and SHA-256, runs the steps, checks every byte it
   makes against the recipe's lock and tags only the locked artifact; a
   draft is built as a draft. With `--no-build` nothing is built, a draft
   is `unpinned` at once and nothing of it is read, and the given store
   (an OCI layout another job built, untrusted) is read by
   `lib/publish.py` alone: its index (a regular file inside the store, at
   most 4 MiB) must name the ref once, with the
   recipe's pin (else `mismatch`), and the manifest and every blob it names
   are copied into a clean store of the run's own as they are checked
   against their digests and sizes; a link, a missing blob or other bytes
   fail it. unemu reads only that clean store, so every byte a push
   uploads is one the pin names. Then `unemu
   image media show` or `image firmware show` must name the ref with the
   digest and end by reporting it pushable to `unemu/`, and `unemu image
   push --dry-run`'s guard must allow the target, and a set's alias target
   too. unemu runs with an environment and a registries file of its own.
5. The digest is compared with the recipe's `digest`: another digest is a
   `mismatch`, a draft is `unpinned`.
6. With `--push`, and only when everything selected passed and is pinned,
   each one that is not `published` is pushed (see below).

A recipe is data: no step of it starts a process, and the build reads
nothing but the inputs it pins. So the pipeline runs no code of any recipe,
and hides nothing from one.

### Outcomes

The run prints a table of repository, tag, alias (`-` for a package),
digest, outcome and licence on stdout, and its progress on stderr.

| Outcome | Meaning |
| --- | --- |
| `dry run` | checked, and the digest equals the recipe's pin |
| `published` | `--skip-published`: its own tag, and a set's alias, already serve the pinned digest; not built or pushed |
| `unpinned` | a draft: the recipe has no `digest`; never pushed |
| `mismatch` | the store holds another digest than the pin |
| (a `note:` detail) | `--experimental`: the official tag or alias still serves another digest; the row's outcome is unchanged |
| `failed` | a build, check, guard, registry read or push failed |
| `held` | not pushed, because another one failed or is unpinned, or an earlier push failed |
| `pushed` | the tag or the alias pushed, and each read back with the same digest |
| `present` | the registry already serves this digest under the tag and the alias |
| `refused` | the registry holds another digest under the tag or the alias (the target before a push, or with `--skip-published` its own); stop and ask the maintainer |
| `unconfirmed` | pushed, but a read-back failed or named another digest |
| `partial` | the revision tag is on the registry with the digest (read back), but its alias push or the alias's read-back failed; the detail names the revision tag |

The exit status is 0 when everything selected passed (and, with `--push`,
is on the registry), 1 otherwise, 2 for a usage error.

## Pushing

Pushing is the maintainer's step: by approving a run of the publish
workflow ([CI](#ci)), or by hand:

```sh
export UNEMU_REGISTRY_HOST=registry.unemu.dev UNEMU_REGISTRY_USER=ACCOUNT
read -rs UNEMU_REGISTRY_PASSWORD && export UNEMU_REGISTRY_PASSWORD
python3 -I lib/publish.py --cli out/unemu/unemu --fetch --push recipes/media/freedos
```

This command publishes outputs only; publication remains incomplete until
their recipe artifacts follow through the approved workflow. The workflow's
default `mode=both` handles both in one approved run.

What keeps a push safe:

- **Credentials.** They come only from the environment
  (`UNEMU_REGISTRY_USER`, `UNEMU_REGISTRY_PASSWORD`), and a push also needs
  `UNEMU_REGISTRY_HOST` set explicitly. They reach the `unemu image push`
  call alone, through its environment: never a command line or another
  unemu command.
- **All or nothing.** Nothing is pushed unless every selected set and
  package passed and is pinned; after the first failure, the rest are
  `held`.
- **An anonymous read first.** Before a set or package is pushed, its
  target tag and a set's target alias are read anonymously over HTTPS (OCI
  and Docker manifest types). A 404 is confirmed through the repository's
  tag list, and a tag the list names but the read cannot see is refused. A
  redirect, a timeout or any other answer stops the push.
- **Revision tags never move.** A tag or alias that already serves the
  same digest is skipped (`present`, or `published` before anything is
  built with `--skip-published`); either one serving another digest
  refuses the set or package before anything of it is pushed. unemu is
  never given `--move`, and sets and packages have no `latest`.
- **The revision before its alias.** The revision tag is pushed and read
  back first, then the alias, which unemu moves onto the newest revision
  the registry has only.
- **A read-back after.** Each push is read back anonymously and must serve
  the pushed digest.

### Before a push: check the registry host

A resolver that still answers with an older address (a split-horizon DNS
entry, say) would send the anonymous read and the push somewhere else.
Before pushing, check that this machine resolves the host as public DNS
does, and that the host answers as the public registry:

```sh
host=registry.unemu.dev
python3 -c 'import socket, sys; print(sorted({a[4][0] for a in socket.getaddrinfo(sys.argv[1], 443)}))' "$host"
nslookup "$host" 1.1.1.1                                         # a public resolver
curl -sS -o /dev/null -w '%{http_code}\n' "https://$host/v2/"   # 200: anonymous reads work
```

The first two must name the same addresses. The Python line asks the
system resolver, as the pipeline and unemu do; `dig` and `nslookup` may
bypass per-domain resolver settings.

## Experimental targets

| Kind | Target | With `--experimental RUN` |
| --- | --- | --- |
| media set | `<host>/unemu/media/ibm-pc/<work>:<release>-rN`, alias `<release>` | `<host>/unemu/experimental/media/ibm-pc/<work>:<release>-ci<RUN>-rN`, alias `<release>-ci<RUN>` |
| firmware package | `<host>/unemu/firmware/ibm-pc/<id>:rN` | `<host>/unemu/experimental/firmware/ibm-pc/<id>:ci<RUN>-rN` |

`ci<RUN>` becomes the last part of the tag's release, so every run pushes
revision tags of its own, which never move, and a set's experimental alias
is pushed after its revision as the official one is. Each run pushes fresh
tags only: an experimental run is a first publication every time, so it
never meets an alias that would have to move, as a later publication to
`unemu/media/` can, and it meets an existing revision tag only when the
same run is re-run, which finds its own tags. `ci<RUN>` lives in the tag
only: the set's metadata keeps its own version and edition, and the package
its revision. An experimental repository longer than 255 bytes, or a tag
longer than 128 characters, is refused before anything is built.

## CI

Three GitHub Actions workflows in [`.github/workflows/`](../.github/workflows/)
run the checks and the publications. unemu builds the recipes, so no job
that builds a recipe needs Nix; the devShell runs the README's checks and
the script that makes the FreeDOS source archive. On the runner's Linux
x86_64, unemu's own `setup-unemu` action installs the release binary,
checking the release's `SHA256SUMS` and the commit line as described above,
without checking our archive pins. The recipes
retain their original digests and locks, checked with unemu 0.0.4; the check
workflow builds every recipe on Linux against the same locks.

### The rule: only what changed, never what is published

A set or package that the registry already serves under its own tag (and,
for a set, its alias), with its pinned digest, is never built or pushed
again by the workflows that publish (experimental and publish): both pass
`--skip-published`. The check workflow does build a recipe, published or
not, when the recipe or one of its inputs changes, and every week: that is
a check, and it reads and pushes nothing. In the publish workflow a tag
or alias that serves another digest is `refused` and fails the run: a
published revision tag never moves, and the pipeline never moves an alias.
The experimental workflow only notes it and pushes its experimental tags.
Before unemupc's first public release a corrected set or package replaces
the published one under its tags, in the order of [Replacing a published
set or package](#replacing-a-published-set-or-package).

### Which recipes a run takes

`lib/changed.py BASE HEAD` prints the recipe files a change touches
(`--json` for one JSON list, `--all` for every recipe):

- a recipe is a file `recipes/**/NAME.media.toml` or `NAME.firmware.toml`
  at `HEAD`, outside hidden and lock folders;
- a change to the recipe, to its lock folder, to one of its bundled inputs
  or to the work file it names selects it; a deleted recipe is not
  selected;
- a change to a shared input selects every recipe: `lib/`, `unemu.toml`
  and `.github/workflows/`;
- anything else selects nothing: `docs/`, `tests/` (the check command runs
  them on every change), the devShell (`flake.nix`, `flake.lock`: no
  recipe runs in it) and the tooling configuration;
- a `BASE` of all zeros (a new branch) or one that is not a commit in the
  clone (a force push) selects every recipe.

This default selection includes private recipes for static checking.
`--open-only` restricts it to parsed open recipes, keeping the same JSON
list format; check.yml and experimental.yml use that list for every build,
fetch, conformance and publication step. An invalid recipe stays in the
static selection and refuses `--open-only`, so an invalid open recipe
cannot be silently skipped.

`--sources` prints instead the scripts that make prepared inputs
(`recipes/**/source/build.sh`, excluding `recipes/private/`) that the change touches: a change in the
script's folder, to a plain file of the work folder above it (the licence
index the archive carries), or to `lib/source.sh`, `flake.nix`,
`flake.lock` or `.github/workflows/`. It reads the two commits with git
alone, never the working tree.

### check.yml: every pull request and push to main

On `pull_request`, on a push to `main`, weekly (Monday) and by hand. No
secret, no environment and no registry access: fork pull requests run all
of it. A pull request's newest run replaces its older one; every other run
has a concurrency group of its own commit.

1. **tests and selection**, in the devShell: the README's check command,
   then the selection: `BASE` is the pull request's base, or the commit
   before the push; the weekly and manual runs take every open recipe and
   script.
2. **recipes**, on the runner's own Python: `setup-unemu`;
   `lib/recipes.py check`, over every recipe including private ones, which fails on a finding, on
   drift, on a draft and when unemu prints no `ok` line for a recipe or a
   work file a recipe names; `--recipes --guard-only` over every open and
   private recipe (all guards must allow the derived REF, no registry read);
   then for the selected open recipes the output dry run with
   `--fetch` into a fresh store (each built against its lock, shown,
   guard-checked and compared with its pin; the table goes to the job
   summary), and `unemu recipe conformance` over them in a second scratch
   store from the downloaded inputs alone, which runs every step again.
   The inputs are cached between runs (`actions/cache`, keyed on the
   recipe files), each named by its SHA-256: a fresh download replaces a
   cached file, and a file whose bytes are not its name is dropped before
   the build and before the cache is saved. unemu checks each input
   against its pin, so the cache never decides a byte. The weekly run goes
   without it, to catch vanished mirrors.
3. **prepared inputs**, in the devShell, when the selection names a script
   and every week: each script builds its input from scratch and stops
   unless it has its pin; then `setup-unemu` installs the CLI and the
   recipes beside the script are built with that output as a library.
   The script's downloads are cached, except weekly.
4. **check result** passes when the three jobs passed (the third may be
   skipped): the status to require for `main`.

The FreeDOS recipes download their source archive from this repository's
release `freedos-1.4-floppy` (created by the maintainer on 2026-11-20). If
it ever went missing, their builds in the recipes job would fail at that
download, and the prepared-inputs job would still build them from the
archive it makes.

### experimental.yml: every push to main

On a push to `main` (and by hand, over every open recipe), one run at a time: a
run waits for the one before it. No Nix. Two jobs:

1. **build**, without an environment or a secret: the selection of the
   push, as above; `setup-unemu`; then `lib/publish.py
   --skip-published --experimental RUN --fetch --store STORE --report
   FILE RECIPE...`, `RUN` being the run's id (`github.run_id`). This builds
   and checks only what is not published yet; a draft or a failure stops
   the run here, and the table goes to the job summary. An official tag
   that still serves another digest is a note, not a refusal. When the
   report names anything left to push, the built store (its `store.toml`
   and its OCI layout `images/`, not its download cache) and the table go
   to the push job as an artifact kept for one day.
2. **push**, on a fresh runner, in the environment `experimental`: a fresh
   checkout, a fresh `setup-unemu` installation (never from the artifact), the
   artifact, then one step with the credentials of `ci-experimental`
   (write on `unemu/experimental/**` only): `--skip-published --no-build
   --store handover/store --push --experimental RUN`. It builds and
   downloads nothing: every byte it pushes is checked against the recipe's
   pin first and copied into a clean store that unemu reads (see the
   steps), so a forged artifact can only fail the push. It pushes to
   `unemu/experimental/<kind>/ibm-pc/<name>:<release>-ci<RUN>-rN` (a
   package's `ci<RUN>-rN`), each set with its alias `<release>-ci<RUN>`.
   The selection comes from the build job's outputs; the most a forgery of
   them buys is another choice of pinned, unpublished sets and packages
   pushed to `unemu/experimental/`, each still with its pinned digest.

### publish.yml: publication, approved by the maintainer

By hand only (`workflow_dispatch`), from `main`, one run at a time. The
`mode` input defaults to `both`: outputs first, then their recipes, in one
approved run. Explicit `outputs` mode is an exception that leaves publication
incomplete until the recipes follow. `recipes` mode supports recipe-only
updates and private recipes. `select`
takes recipe files, folders and output refs separated by spaces; a newline,
carriage return or tab is refused. Empty selection means every open recipe
for `outputs` or `both`, and every open and private recipe for `recipes`.
Private paths are allowed only in `recipes` mode: in `both`, the output
pipeline refuses them before any build. This keeps one approval and one
selection for a combined publication, with no way to build a private output.

1. **build**, without an environment or a secret: `setup-unemu`, then for
   outputs the existing `--skip-published --fetch` dry run and checked-store
   hand-over. For recipes it runs `--recipes --guard-only` with a fresh
   store: outputs can still await this run's push, so this checks all static
   rules and guards and clearly defers the registry gate. Both tables go to
   the run summary before approval. Only the output OCI store and reports
   are handed over, kept for 7 days. A recipe-only run hands over reports
   alone; a combined run still proceeds when all outputs are published.
   Separate `any` and `outputs` flags distinguish recipe work from pending
   outputs; no store files are fabricated when no outputs need pushing.
2. **push**, in the protected environment `media`, waits for the
   maintainer's approval. A fresh runner checks out the same commit and
   installs unemu afresh. It re-reads `select` and `mode` from the run inputs,
   never from build outputs. The handed-over store is downloaded only when
   the build's `outputs` flag is true; only then does the output push run.
   Outputs go first, with the existing
   `--skip-published --no-build --store handover/store --push` checks.
   Then recipes use `--recipes --push` in a fresh store, enforcing the
   anonymous open-output gate and exact anonymous pull read-back described
   above. Each push step alone receives `ci-promote` credentials through
   its environment (write on `unemu/media/**`, `unemu/firmware/**` and
   `unemu/recipes/**`); build and read-back commands receive none.

When outputs need pushing, the maintainer approves the push job within
7 days, while the store artifact is kept; after that its download fails,
and the remedy is to re-run the whole run (or start a new one), which
builds again. Recipe pushes use the checkout and require no artifact download.

**Decision of the maintainer (2026-11-17): no promotion from
`unemu/experimental/`.** The publish workflow rebuilds only the sets and
packages that are not published yet and pushes them when they give their
pinned digest; nothing is copied from `unemu/experimental/`. The guarantee
is the lock and the digest pin: a set or package reaches `unemu/media/` or
`unemu/firmware/` only when the pinned unemu builds exactly the digest its
recipe pins, the digest an experimental push of the same recipe gave as
well.

### When a change is missed

A push to `main` can leave its recipes unchecked or unpushed: GitHub keeps
only the newest waiting run of a concurrency group and cancels an older
waiting one (in the `experimental` group), a commit message with `[skip
ci]` starts no run, and a failed run stays failed until someone runs it
again. The remedy is to re-run the failed or cancelled run, or to start a
workflow by hand: check.yml statically checks every recipe and builds every
open recipe; experimental.yml by hand takes every open recipe,
and the experimental run still builds and pushes only what is not published.
Nothing is ever published to `unemu/media/` or `unemu/firmware/` without
publish.yml, which takes every open recipe by default.

### Credentials

- Every workflow sets `permissions: contents: read`; none uses
  `pull_request_target`, and actions/checkout keeps no credentials in the
  clone.
- The environment `experimental` (deployment branch `main`) holds the
  secret `UNEMU_EXPERIMENTAL_PASSWORD` of the account `ci-experimental`;
  the protected environment `media` (deployment branch `main`, the
  maintainer as required reviewer) holds `UNEMU_PROMOTE_PASSWORD` of the
  account `ci-promote`, with write access to `unemu/media/**`,
  `unemu/firmware/**` and `unemu/recipes/**`. The account names are written
  in the workflows.
- Only the push jobs name an environment, and only their push steps map the
  account and secret into `UNEMU_REGISTRY_USER` and
  `UNEMU_REGISTRY_PASSWORD`, next to `UNEMU_REGISTRY_HOST` from the
  repository variable of that name. GitHub hands a job's secrets to its
  runner when the job starts, so a step's environment is no boundary
  within a job: the boundary is the job. No job that refers to a secret
  builds an output or downloads its inputs. It downloads unemu, its release's
  `SHA256SUMS`, the build job's artifact and, for recipe read-back, public
  recipe text artifacts. `setup-unemu` checks the archive
  against those sums and the commit line against the pinned commit; the
  build jobs, which download and build, have no environment and no secret.
- No job runs code of a recipe, which holds none: the recipes are data
  that only the pinned unemu reads, and every input is checked by
  SHA-256. The push jobs run no Nix and take from the build job only its
  store, whether anything is left to push and, for experimental, the
  selection (publish's reads it from the input). The store is untrusted
  there: `lib/publish.py` checks every byte a push would upload against
  the recipe's pin before unemu reads any of it, and unemu reads only the
  clean store those checked bytes are copied into. What a forged artifact
  can still do is fail the push, or offer a parser of `lib/publish.py`
  (Python's `json` on the index and manifests, at most 4 MiB each) hostile
  input.
- The artifacts of a public repository can be downloaded by any signed-in
  GitHub user while they are kept (one day for experimental, 7 days for
  publish): they hold the built, open sets and packages, as they are once
  pushed.
- `cachix/install-nix-action` runs only in the jobs that run the README's
  checks and the prepared-input scripts, never in a job with credentials.
  It writes `access-tokens = github.com=<the job's GITHUB_TOKEN>` into
  `/etc/nix/nix.conf` (the token can only read this public repository, and
  expires with the job).
- Every third-party action is pinned by its full commit SHA, with the
  release in a comment: `actions/checkout`, `actions/cache`,
  `actions/upload-artifact`, `actions/download-artifact` and
  `cachix/install-nix-action`.

GitHub-hosted runners use public DNS.

## Recipes on the registry

`unemu recipe push` publishes a pinned recipe itself (to
`unemu/recipes/<kind>/ibm-pc/<name>:<tag>`); unemu 0.0.4 accepts no other
place, an experimental one neither. Recipe format **1.0 is frozen**
(unemupc decision-43); its schema is the
[recipe-schema-1.0.json release asset](https://github.com/unemu-org/unemu/releases/download/v0.0.4/recipe-schema-1.0.json).
Recipes publish through the maintainer-approved `publish` workflow after
their open outputs; private recipes publish public text without an output
gate. Each recipe's first push needs approval of that run. `ci-promote`
may write `unemu/recipes/**` (2026-11-23). See [Recipe artifacts](#recipe-artifacts)
for the mapping, gates and anonymous byte-for-byte read-back.

## Gaps of unemu 0.0.4 that shape this pipeline

- Until ufe-uym.9.10 (unemu), `--fetch` with an external input without
  URLs first downloads missing URL inputs over HTTPS into the first
  `--library` (or a temporary library). Size and SHA-256 are verified
  before an atomic rename to the SHA-256 filename, matching `check.yml`'s
  download cache. That recipe then builds without `--fetch`. Bundled
  inputs need no download; recipes whose external inputs all have URLs
  keep the normal unemu fetch path.
- It cannot read a remote tag's digest (`image remote ls` lists tags
  only), so `lib/publish.py` keeps its own anonymous reads.
- `recipe check` exits 0 with drift, and `recipe conformance` with drafts
  beside pinned recipes: `lib/recipes.py check` refuses both.
- A recipe has no key for a package's provenance or for the licence of an
  extra other than its source archive (`--extra-license`,
  `--source-license`); none of ours needs one yet.
