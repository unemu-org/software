# Publishing

`lib/publish.py` builds the recipes ([recipes.md](recipes.md)) with the
pinned unemupc CLI, `upcli`, into a store, checks every set and package
against its recipe's lock and digest, and pushes them to the registry only
when asked. Without `--push` it is a dry run: it needs no credentials and
writes to no registry, and it reads one only when `--skip-published` asks.

## The pinned upcli

### upcli.toml

[`upcli.toml`](../upcli.toml) pins one unemupc release. Its keys are exactly
these; `lib/upcli.py` refuses any other key, a missing or malformed one, and
(as TOML does) a key given twice.

| Key | What it holds |
| --- | --- |
| `commit` | the full commit the release's tag points to |
| `tag`, `version` | the release's tag (`v0.0.3`) and version (`0.0.3`) |
| `[archive.<triple>]` | one table per platform: `x86_64-unknown-linux-gnu`, `aarch64-apple-darwin` |
| `file` | the archive's name, `unemupc-<version>-<triple>.tar.gz` |
| `sha256`, `size` | the archive's SHA-256 and size in bytes |
| `upcli_sha256`, `upcli_size` | the SHA-256 and size of its member `unemupc-<version>-<triple>/upcli` |

Every hash is our own pin, never checked against the release's
`SHA256SUMS`.

### Getting upcli

The release binary, on a Linux x86_64 or macOS arm64 host:

```sh
python3 -I lib/upcli.py fetch out/upcli
```

`fetch` takes a new directory (it refuses one that exists) and:

1. reads the host's archive into memory once, from `cache/upcli/`, or else
   downloads it from the GitHub release over HTTPS (curl) into a temporary
   directory;
2. checks those bytes against the pinned size and SHA-256, and uses only
   those bytes from then on; a download is written into the cache only once
   checked, and a cached copy that differs is refused, never replaced;
3. takes out only the member `unemupc-<version>-<triple>/upcli`: the one
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

Or build it from source, in a clean unemupc checkout at the pinned commit
with no local changes, inside unemupc's devShell:

```sh
UNEMUPC_SOURCE_COMMIT=$(git rev-parse HEAD) cargo build -p upcli --bin upcli
```

Without `UNEMUPC_SOURCE_COMMIT` the build prints no commit line (unemupc's
`crates/platforms/src/lib.rs`, `commit_line`) and the pipeline refuses it.

### The version check

Before anything is checked or built, the pipeline runs `upcli --version`
once and reads its stdout. Exactly one line may start with `commit:`, and it
must be `commit: ` followed by the 40 lower-case hex digits of the pinned
commit. Another commit, a malformed or repeated commit line, a failing
`--version`, or no commit line at all refuses the run, a dry run as well as
a push. There is no option to skip it. Nothing else in the output is read.
`lib/recipes.py check` makes the same check.

### Moving the pin

1. Take the commit from the new release's tag.
2. Download each archive and measure its SHA-256 and size; read its `upcli`
   member out of the checked archive, without running it, and measure that
   too. Never copy a hash from the release's `SHA256SUMS`.
3. Update `upcli.toml`, fetch the binary, and run `lib/recipes.py check`,
   `upcli recipe conformance recipes --library DIR` and the dry run. A
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
release: the pipeline pushes it right after the revision, and upcli lets
an alias onto the newest revision the registry holds only. Moving an alias
to a newer revision takes `upcli image push --move`, which this pipeline
never passes and does not support yet, so an alias that names another
image is `refused` and the maintainer decides. No further revision is
published before unemupc's first public release (its decision-32, point
5): until then a corrected set or package replaces the published one under
the same tags, in the order of [Replacing a published set or
package](#replacing-a-published-set-or-package). Sets and
packages have no `latest`, and everything upcli 0.0.3 writes declares
`unemu.min-app-version` 0.0.3. `upcli recipe build` tags a set or package
under its own ref in the store (and a set's alias with it); every push
names its `unemu/...` target explicitly.

`upcli image push --dry-run` is local: it checks the guard, but no tag
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
python3 -I lib/publish.py --cli UPCLI [--store DIR] [--fetch] [--library DIR]...
                          [--no-build] [--experimental RUN] [--skip-published]
                          [--push] [--report FILE] [RECIPE|FOLDER|REF]...
```

| Option | Meaning |
| --- | --- |
| `--cli UPCLI` | the upcli binary; default `$UPCLI`; a name without a slash is looked up on `PATH` |
| `--store DIR` | build into `DIR`, a new directory that is kept (default: a fresh store under `build/`, removed afterwards); with `--no-build`, the existing store an earlier run built, which upcli never reads (see the steps) |
| `--fetch` | let `upcli recipe build` download a missing input from the URLs its recipe pins |
| `--library DIR` | a folder where `upcli recipe build` looks for inputs by size and SHA-256 (repeatable) |
| `--no-build` | build nothing: every selected set and package must already be in `--store` under its own ref, every byte of it checked against its pin |
| `--experimental RUN` | push to the experimental targets (see below); `RUN` is the CI run id, from 1 |
| `--skip-published` | read each pinned set's and package's own tag (and a set's alias) first; skip what the registry already serves (see below) |
| `--push` | push every checked set and package |
| `--report FILE` | also write the summary's rows as JSON (the CI reads whether anything is left to push) |
| `RECIPE`, `FOLDER` or `REF` | select a recipe file (`recipes/media/freedos/freedos-1.4.media.toml`), every recipe below a folder (`recipes/media/freedos`) or one set or package by its ref (`unemu/media/ibm-pc/freedos:1.4-r1`, `unemu/firmware/ibm-pc/xub-r638-xtp-ata1f0:r1`; not an alias); default: everything |

The registry host is `UNEMU_REGISTRY_HOST`, checked as upcli checks one (a
name with a dot or a port, or `localhost`; no scheme or path). A dry run
without it names `registry.unemu.dev`, which it contacts only for the
anonymous reads of `--skip-published`.

### Steps

A set or package that fails a step is not taken further.

1. Every recipe file is read (`lib/recipes.py`): its identity table names
   a repository under `unemu/media/` or `unemu/firmware/` and a tag of
   decision-38, and no ref, nor a set's release alias, appears twice. A
   refusal stops the run.
2. The version check (above), then `upcli recipe check` over every recipe:
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
4. Each selected set or package is built with `upcli recipe build FILE
   --store STORE` (with `--fetch` and `--library` as given). upcli finds
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
   fail it. upcli reads only that clean store, so every byte a push
   uploads is one the pin names. Then `upcli
   image media show` or `image firmware show` must name the ref with the
   digest and end by reporting it pushable to `unemu/`, and `upcli image
   push --dry-run`'s guard must allow the target, and a set's alias target
   too. upcli runs with an environment and a registries file of its own.
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
python3 -I lib/publish.py --cli out/upcli/upcli --fetch --push recipes/media/freedos
```

What keeps a push safe:

- **Credentials.** They come only from the environment
  (`UNEMU_REGISTRY_USER`, `UNEMU_REGISTRY_PASSWORD`), and a push also needs
  `UNEMU_REGISTRY_HOST` set explicitly. They reach the `upcli image push`
  call alone, through its environment: never a command line or another
  upcli command.
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
  refuses the set or package before anything of it is pushed. upcli is
  never given `--move`, and sets and packages have no `latest`.
- **The revision before its alias.** The revision tag is pushed and read
  back first, then the alias, which upcli moves onto the newest revision
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
system resolver, as the pipeline and upcli do; `dig` and `nslookup` may
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
run the checks and the publications. upcli builds the recipes, so no job
that builds a recipe needs Nix; the devShell runs the README's checks and
the script that makes the FreeDOS source archive. `upcli` is the release
binary `lib/upcli.py fetch` takes, on the runner's Linux x86_64. The pins
were made with the macOS arm64 upcli of the pinned release; the check
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
- a change to a shared input selects every recipe: `lib/`, `upcli.toml`
  and `.github/workflows/`;
- anything else selects nothing: `docs/`, `tests/` (the check command runs
  them on every change), the devShell (`flake.nix`, `flake.lock`: no
  recipe runs in it) and the tooling configuration;
- a `BASE` of all zeros (a new branch) or one that is not a commit in the
  clone (a force push) selects every recipe.

`--sources` prints instead the scripts that make prepared inputs
(`recipes/**/source/build.sh`) that the change touches: a change in the
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
   before the push; the weekly and manual runs take every recipe and
   script.
2. **recipes**, on the runner's own Python: `lib/upcli.py fetch`;
   `lib/recipes.py check`, over every recipe, which fails on a finding, on
   drift, on a draft and when upcli prints no `ok` line for a recipe or a
   work file a recipe names; then for the selected recipes the dry run with
   `--fetch` into a fresh store (each built against its lock, shown,
   guard-checked and compared with its pin; the table goes to the job
   summary), and `upcli recipe conformance` over them in a second scratch
   store from the downloaded inputs alone, which runs every step again.
   The inputs are cached between runs (`actions/cache`, keyed on the
   recipe files), each named by its SHA-256: a fresh download replaces a
   cached file, and a file whose bytes are not its name is dropped before
   the build and before the cache is saved. upcli checks each input
   against its pin, so the cache never decides a byte. The weekly run goes
   without it, to catch vanished mirrors.
3. **prepared inputs**, in the devShell, when the selection names a script
   and every week: each script builds its input from scratch and stops
   unless it has its pin; then the recipes beside it are built with that
   output as a library. The script's downloads are cached, except weekly.
4. **check result** passes when the three jobs passed (the third may be
   skipped): the status to require for `main`.

The FreeDOS recipes download their source archive from this repository's
release `freedos-1.4-floppy` (created by the maintainer on 2026-11-20). If
it ever went missing, their builds in the recipes job would fail at that
download, and the prepared-inputs job would still build them from the
archive it makes.

### experimental.yml: every push to main

On a push to `main` (and by hand, over every recipe), one run at a time: a
run waits for the one before it. No Nix. Two jobs:

1. **build**, without an environment or a secret: the selection of the
   push, as above; `lib/upcli.py fetch`; then `lib/publish.py
   --skip-published --experimental RUN --fetch --store STORE --report
   FILE RECIPE...`, `RUN` being the run's id (`github.run_id`). This builds
   and checks only what is not published yet; a draft or a failure stops
   the run here, and the table goes to the job summary. An official tag
   that still serves another digest is a note, not a refusal. When the
   report names anything left to push, the built store (its `store.toml`
   and its OCI layout `images/`, not its download cache) and the table go
   to the push job as an artifact kept for one day.
2. **push**, on a fresh runner, in the environment `experimental`: a fresh
   checkout, a fresh `lib/upcli.py fetch` (never from the artifact), the
   artifact, then one step with the credentials of `ci-experimental`
   (write on `unemu/experimental/**` only): `--skip-published --no-build
   --store handover/store --push --experimental RUN`. It builds and
   downloads nothing: every byte it pushes is checked against the recipe's
   pin first and copied into a clean store that upcli reads (see the
   steps), so a forged artifact can only fail the push. It pushes to
   `unemu/experimental/<kind>/ibm-pc/<name>:<release>-ci<RUN>-rN` (a
   package's `ci<RUN>-rN`), each set with its alias `<release>-ci<RUN>`.
   The selection comes from the build job's outputs; the most a forgery of
   them buys is another choice of pinned, unpublished sets and packages
   pushed to `unemu/experimental/`, each still with its pinned digest.

### publish.yml: publication, approved by the maintainer

By hand only (`workflow_dispatch`), from `main`. Its input `select` takes
recipe files, folders and refs separated by spaces; empty means every
recipe; a newline, carriage return or tab in it is refused. One run at a
time, in a concurrency group of its own. Two jobs, as above:

1. **build**, without an environment or a secret: `lib/publish.py
   --skip-published --fetch SELECT...`, the dry run, which builds only what
   is not on `unemu/media/` or `unemu/firmware/` yet. Its table is in the
   run's summary before anything is approved. The built store goes to the
   push job as an artifact kept for 7 days.
2. **push**, in the protected environment `media`, which waits for the
   maintainer's approval: on a fresh runner, as above, one step with the
   credentials of `ci-promote` (write on `unemu/media/**` and
   `unemu/firmware/**`) that checks every byte of the store against the
   pins and pushes it, `--skip-published --no-build --store handover/store
   --push`. It reads `select` from the run's input itself, with the same
   refusal, never from the build job's outputs (an empty selection would
   mean every recipe).

The maintainer approves the push job within 7 days, while the artifact is
kept; after that the push job fails for want of it, and the remedy is to
re-run the whole run (or start a new one), which builds again.

**Decision of the maintainer (2026-11-17): no promotion from
`unemu/experimental/`.** The publish workflow rebuilds only the sets and
packages that are not published yet and pushes them when they give their
pinned digest; nothing is copied from `unemu/experimental/`. The guarantee
is the lock and the digest pin: a set or package reaches `unemu/media/` or
`unemu/firmware/` only when the pinned upcli builds exactly the digest its
recipe pins, the digest an experimental push of the same recipe gave as
well.

### When a change is missed

A push to `main` can leave its recipes unchecked or unpushed: GitHub keeps
only the newest waiting run of a concurrency group and cancels an older
waiting one (in the `experimental` group), a commit message with `[skip
ci]` starts no run, and a failed run stays failed until someone runs it
again. The remedy is to re-run the failed or cancelled run, or to start a
workflow by hand: check.yml and experimental.yml by hand take every recipe,
and the experimental run still builds and pushes only what is not published.
Nothing is ever published to `unemu/media/` or `unemu/firmware/` without
publish.yml, which takes every recipe by default.

### Credentials

- Every workflow sets `permissions: contents: read`; none uses
  `pull_request_target`, and actions/checkout keeps no credentials in the
  clone.
- The environment `experimental` (deployment branch `main`) holds the
  secret `UNEMU_EXPERIMENTAL_PASSWORD` of the account `ci-experimental`;
  the protected environment `media` (deployment branch `main`, the
  maintainer as required reviewer) holds `UNEMU_PROMOTE_PASSWORD` of the
  account `ci-promote`. The account names are written in the workflows.
- Only the push jobs name an environment, and only their push step maps the
  account and secret into `UNEMU_REGISTRY_USER` and
  `UNEMU_REGISTRY_PASSWORD`, next to `UNEMU_REGISTRY_HOST` from the
  repository variable of that name. GitHub hands a job's secrets to its
  runner when the job starts, so a step's environment is no boundary
  within a job: the boundary is the job. No job that refers to a secret
  builds anything or downloads anything but the pinned upcli (checked
  against `upcli.toml`) and the build job's artifact; the build jobs, which
  download and build, have no environment and no secret.
- No job runs code of a recipe, which holds none: the recipes are data
  that only the pinned upcli reads, and every input is checked by
  SHA-256. The push jobs run no Nix and take from the build job only its
  store, whether anything is left to push and, for experimental, the
  selection (publish's reads it from the input). The store is untrusted
  there: `lib/publish.py` checks every byte a push would upload against
  the recipe's pin before upcli reads any of it, and upcli reads only the
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

`upcli recipe push` publishes a pinned recipe itself (to
`unemu/recipes/<kind>/ibm-pc/<name>:<tag>`); upcli 0.0.3 accepts no other
place, an experimental one neither. No recipe is published before
unemupc's recipe format is frozen (its decision-35, point 8), so the
workflows push sets and packages only. Publishing recipes later needs an
account that may write `unemu/recipes/**`: the maintainer's registry step.

## Gaps of upcli 0.0.3 that shape this pipeline

- It cannot read a remote tag's digest (`image remote ls` lists tags
  only), so `lib/publish.py` keeps its own anonymous reads.
- `recipe check` exits 0 with drift, and `recipe conformance` with drafts
  beside pinned recipes: `lib/recipes.py check` refuses both.
- A recipe has no key for a package's provenance or for the licence of an
  extra other than its source archive (`--extra-license`,
  `--source-license`); none of ours needs one yet.
