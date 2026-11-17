# Publishing

`lib/publish.py` builds the recipes, imports their sets and packages with
the pinned unemupc CLI, `upcli`, into a scratch store, checks them, and
pushes them to the registry only when asked. Without `--push` it is a dry
run: it needs no credentials and writes to no registry, and it reads one
only when `--skip-published` asks.

## The pinned upcli

### upcli.toml

[`upcli.toml`](../upcli.toml) pins one unemupc release. Its keys are exactly
these; `lib/upcli.py` refuses any other key, a missing or malformed one, and
(as TOML does) a key given twice.

| Key | What it holds |
| --- | --- |
| `commit` | the full commit the release's tag points to |
| `tag`, `version` | the release's tag (`v0.0.2`) and version (`0.0.2`) |
| `[archive.<triple>]` | one table per platform: `x86_64-unknown-linux-gnu`, `aarch64-apple-darwin` |
| `file` | the archive's name, `unemupc-<version>-<triple>.tar.gz` |
| `sha256`, `size` | the archive's SHA-256 and size in bytes |
| `upcli_sha256`, `upcli_size` | the SHA-256 and size of its member `unemupc-<version>-<triple>/upcli` |

Every hash is our own pin, never checked against the release's
`SHA256SUMS`.

### Getting upcli

The release binary, on a Linux x86_64 or macOS arm64 host:

```sh
nix develop --command python3 -I lib/upcli.py fetch out/upcli
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
UNEMUPC_SOURCE_COMMIT=$(git rev-parse HEAD) cargo build -p cli --bin upcli
```

Without `UNEMUPC_SOURCE_COMMIT` the build prints no commit line and the
pipeline refuses it.

### The version check

Before anything is built, the pipeline runs `upcli --version` once and
reads its stdout. Exactly one line may start with `commit:`, and it must be
`commit: ` followed by the 40 lower-case hex digits of the pinned commit.
Another commit, a malformed or repeated commit line, a failing `--version`,
or no commit line at all refuses the run, a dry run as well as a push.
There is no option to skip it. Nothing else in the output is read.

### Moving the pin

1. Take the commit from the new release's tag.
2. Download each archive and measure its SHA-256 and size; read its `upcli`
   member out of the checked archive, without running it, and measure that
   too. Never copy a hash from the release's `SHA256SUMS`.
3. Update `upcli.toml`, fetch the binary, and run the dry run: every digest
   pin must still hold, or change deliberately with the reason in the
   commit.

## The pipeline

```sh
python3 -I lib/publish.py --cli UPCLI [--store DIR] [--experimental RUN]
                          [--skip-published] [--no-build]
                          [--push | --allow-unpinned] [RECIPE|REF]...
```

| Option | Meaning |
| --- | --- |
| `--cli UPCLI` | the upcli binary; default `$UPCLI`; a name without a slash is looked up on `PATH` |
| `--store DIR` | import into `DIR`, a new directory that is kept (default: a fresh store under `build/`, removed afterwards) |
| `--experimental RUN` | push to the experimental targets (see below); `RUN` is the CI run id, from 1 |
| `--skip-published` | read each pinned set's and package's own tag first; skip what the registry already serves (see below) |
| `--no-build` | run no `build.sh`: the outputs must already be in `out/<recipe>/` and match their pins |
| `--push` | push every checked set and package |
| `--allow-unpinned` | let a dry run pass with sets or packages that have no digest pin yet; never with `--push` |
| `RECIPE` or `REF` | select a recipe (`freedos-1.4-floppy`) or one set or package by its ref (`unemu/media/freedos:1.4`, `unemu/firmware/xub-r638-xtp-ata1f0:1`); default: everything |

The registry host is `UNEMU_REGISTRY_HOST`, checked as upcli checks one (a
name with a dot or a port, or `localhost`; no scheme or path). A dry run
without it names `registry.unemu.dev`, which it contacts only for the
anonymous reads of `--skip-published`.

### Steps

A set or package that fails a step is not taken further.

1. `upcli.toml` is read, every recipe's manifest is checked, and no ref may
   appear twice across them. A refusal stops the run.
2. The version check (above).
3. With `--skip-published`, each selected set or package that has a digest
   pin is looked up under its own tag, `unemu/media/<work>:<tag>` or
   `unemu/firmware/<id>:<revision>`, with `--experimental` too: an
   anonymous read, as before a push. The pinned digest there makes it
   `published`: it is not built, imported or pushed, and a recipe whose
   selected sets and packages are all published is not built at all.
   Another digest there makes it `refused`: a tag never moves, so it needs a
   new tag (`-rN`) or revision. A missing tag goes on as usual, and so does
   one without a pin, which cannot be compared and ends `unpinned`. A failed
   read fails it.
4. Each selected recipe's `build.sh` runs (it stops at once when its outputs
   already match their pins), and every output is checked against its pin.
   A recipe never sees a variable whose name starts with `UNEMU`,
   `GITHUB_`, `RUNNER_` or `ACTIONS_`, is `BASH_ENV` or `ENV`, or contains
   `PASSWORD`, `TOKEN`, `SECRET` or `CREDENTIAL`. With `--no-build` no
   `build.sh` runs: the outputs must already be in `out/<recipe>/`, each
   with its pinned SHA-256, or the recipe's sets and packages fail with a
   message that says to build it first. This lets a CI job that holds
   credentials, and runs no recipe code, push what a job without them
   built.
5. Each set is imported with `upcli image media import` and checked with
   `upcli image media show`; each package is imported with `upcli image
   firmware import --for` and checked with `upcli image firmware show`,
   which must state the manifest's metadata, ROM, source archive and
   licences. Both shows must end by reporting it pushable to `unemu/`. Then
   `upcli image push --dry-run`'s guard must allow the target. upcli runs
   with an environment and a registries file of its own.
6. The digest is compared with the manifest's pin.
7. With `--push`, and only when everything selected passed and is pinned,
   each one that is not `published` is pushed (see below).

### Outcomes

The run prints a table of repository, tag, digest, outcome and licence on
stdout, and its progress on stderr.

| Outcome | Meaning |
| --- | --- |
| `dry run` | checked, and the digest equals the manifest's pin |
| `published` | `--skip-published`: its own tag already serves the pinned digest; not built, imported or pushed |
| `unpinned` | checked, but the manifest pins no digest; never pushed |
| `mismatch` | upcli gives another digest than the pin |
| `failed` | a build, check, import, guard, registry read or push failed |
| `held` | not pushed, because another one failed or is unpinned, or an earlier push failed |
| `pushed` | pushed, and read back with the same digest |
| `present` | the registry already serves this digest under the tag |
| `refused` | the registry holds another digest under the tag (the target before a push, or with `--skip-published` its own tag) |
| `unconfirmed` | pushed, but the read-back failed or named another digest |

The exit status is 0 when everything selected passed (and, with `--push`,
is on the registry), 1 otherwise, 2 for a usage error. An `unpinned` row
fails a dry run too, unless `--allow-unpinned` is given.

## Pushing

Pushing is the maintainer's step: by approving a run of the publish
workflow ([CI](#ci)), or by hand:

```sh
export UNEMU_REGISTRY_HOST=registry.unemu.dev UNEMU_REGISTRY_USER=ACCOUNT
read -rs UNEMU_REGISTRY_PASSWORD && export UNEMU_REGISTRY_PASSWORD
nix develop --command python3 -I lib/publish.py --cli out/upcli/upcli --push freedos-1.4-floppy
```

What keeps a push safe:

- **Credentials.** They come only from the environment
  (`UNEMU_REGISTRY_USER`, `UNEMU_REGISTRY_PASSWORD`), and a push also needs
  `UNEMU_REGISTRY_HOST` set explicitly. They reach the `upcli image push`
  call alone, through its environment: never a command line, a recipe or
  another upcli command.
- **All or nothing.** Nothing is pushed unless every selected set and
  package passed and is pinned; after the first failure, the rest are
  `held`.
- **An anonymous read first.** Before each push the target tag is read
  anonymously over HTTPS (OCI and Docker manifest types). A 404 is
  confirmed through the repository's tag list, and a tag the list names but
  the read cannot see is refused. A redirect, a timeout or any other answer
  stops the push.
- **Tags never move.** A tag that already serves the same digest is skipped
  (`present`, or `published` before anything is built with
  `--skip-published`); one that serves another digest is `refused`. upcli
  is never given `--move`, and the movable `latest` alias is not pushed.
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
| media set | `<host>/unemu/media/<work>:<media-tag>` | `<host>/unemu/experimental/media/<work>:<media-tag>.ci<RUN>` |
| firmware package | `<host>/unemu/firmware/<id>:<revision>` | `<host>/unemu/experimental/firmware/<id>:<revision>.ci<RUN>` |

The `.ci<RUN>` suffix is not part of the media tag or the package revision.
An experimental repository longer than 255 bytes, or a tag longer than 128
characters, is refused before anything is built.

## CI

Three GitHub Actions workflows in [`.github/workflows/`](../.github/workflows/)
run the checks and the publications. Every job that runs recipes runs its
steps in the pinned devShell (`nix develop`); the push jobs use the
runner's own Python and curl and no Nix. `upcli` is the release binary
`lib/upcli.py fetch` takes, on the runner's Linux x86_64. The digest pins
were measured with the macOS arm64 upcli of the pinned release; the check
workflow is the first to run the Linux one, and the pins must hold for it
too.

### The rule: only what changed, never what is published

A set or package that the registry already serves under its own tag, with
its pinned digest, is never built or pushed again by the workflows that
publish (experimental and publish): both pass `--skip-published`, so
FreeDOS and the firmware packages, once published, stay out of every later
publication. The check workflow does rebuild a recipe, published or not,
when the recipe or a shared input changes, and every week: that is a check,
and it pushes nothing. A tag that serves another digest is `refused` and
fails the run: a published tag never moves, so a corrected set needs a new
tag (`-rN`), and a package the next revision.

### Which recipes a run takes

`lib/changed.py BASE HEAD` prints the recipes a change touches (`--json`
for one JSON list, `--all` for every recipe):

- a recipe is a directory `recipes/<recipe>/` at `HEAD` with a manifest; a
  change to any file in it selects it, a file moved out of it too, and a
  deleted recipe is not selected;
- a change to a shared input selects every recipe: `lib/`, `flake.nix`,
  `flake.lock`, `upcli.toml` and `.github/workflows/` (the steps that build,
  compare and dry-run the recipes);
- anything else selects nothing: `docs/` and the other documentation,
  `tests/` (the check command runs them on every change; no recipe reads
  them) and the tooling configuration;
- a `BASE` of all zeros (a new branch) or one that is not a commit in the
  clone (a force push) selects every recipe.

It reads the two commits with git alone, never the working tree.

### check.yml: every pull request and push to main

On `pull_request`, on a push to `main`, weekly (Monday) and by hand. No
secret and no environment: fork pull requests run all of it. A pull
request's newest run replaces its older one; every other run has a
concurrency group of its own commit, so no push to `main` cancels another.

1. The README's check command.
2. The selection: `BASE` is the pull request's base, or the commit before
   the push; the weekly and manual runs take every recipe. A pull request's
   base may be older than `main`'s tip, which can select more than the pull
   request touches, never less.
3. For each selected recipe, a job of its own (a matrix, at most 256
   recipes in one run): `build.sh` from scratch (no `out/<recipe>/`, no
   `build/`), the outputs set aside, `build.sh` from scratch again, and both
   outputs compared byte for byte. Downloads come from an `actions/cache`
   keyed on the recipe's directory, `lib/` and `flake.lock`; the recipes
   check every cached file against its pin, so the cache never decides a
   byte. The weekly run goes without it, over every recipe, to catch
   vanished mirrors and host drift.
4. `lib/upcli.py fetch`, then `lib/publish.py RECIPE`, the dry run: every
   row must read `dry run`. A new recipe without a digest pin fails as
   `unpinned`, with the digest the pinned upcli gives in its row, ready to
   pin. The table goes to the job summary.
5. One job, `check result`, passes when everything above passed or nothing
   was selected: the status to require for `main`.

### experimental.yml: every push to main

On a push to `main` (and by hand, over every recipe), one run at a time: a
run waits for the one before it. Two jobs:

1. **build**, without an environment or a secret, in the devShell: the
   selection of the push, as above; `lib/upcli.py fetch`; then
   `lib/publish.py --skip-published --experimental RUN RECIPE...`, `RUN`
   being the run's id (`github.run_id`, unique in the repository; a rerun
   pushes the same digest under the same tag). This builds and checks only
   what is not published yet; an unpinned or failing set or package stops
   the run here, and the table goes to the job summary. What it built, the
   recipes' `out/<recipe>/` (on a fresh runner `out/` holds nothing else),
   goes to the push job as an artifact kept for one day. If nothing is
   selected, or everything selected is published, nothing is built and the
   run ends here, successfully.
2. **push**, on a fresh runner, in the environment `experimental`, with
   the credentials of `ci-experimental` (write on `unemu/experimental/**`
   only): a fresh checkout and a fresh `lib/upcli.py fetch` (never from the
   artifact), the artifact into `out/`, then the same selection with
   `--skip-published --no-build --push`. No recipe code runs in this job:
   `--no-build` checks every pinned output's SHA-256 before the import, and
   the sets and packages are pushed to
   `unemu/experimental/<kind>/<name>:<tag>.ci<RUN>`. The selection comes
   from the build job's outputs, which its recipe code could forge; the
   most that buys is another choice of pinned, unpublished sets and
   packages pushed to `unemu/experimental/`, each still with its pinned
   digest.

### publish.yml: publication, approved by the maintainer

By hand only (`workflow_dispatch`), from `main`. Its input `select` takes
recipes or refs separated by spaces; empty means every recipe; a newline,
carriage return or tab in it is refused. One run at a time, in a
concurrency group of its own (not the experimental one, so a push to `main`
never cancels a waiting publication). Two jobs, as above:

1. **build**, without an environment or a secret: `lib/publish.py
   --skip-published SELECT...`, the dry run, which builds only what is not
   on `unemu/media/` or `unemu/firmware/` yet. Its table is in the run's
   summary before anything is approved. What it built goes to the push job
   as an artifact kept for 7 days.
2. **push**, in the protected environment `media`, which waits for the
   maintainer's approval, with the credentials of `ci-promote` (write on
   `unemu/media/**` and `unemu/firmware/**`): the same with `--no-build
   --push`, on a fresh runner, from the artifact. It reads `select` from
   the run's input itself, with the same refusal, never from the build
   job's outputs, which its recipe code could forge (an empty selection
   would mean every recipe).

The maintainer approves the push job within 7 days, while the artifact is
kept; after that the push job fails for want of it, and the remedy is to
re-run the whole run (or start a new one), which builds again.

**Decision of the maintainer (2026-11-17): no promotion from
`unemu/experimental/`.** The publish workflow rebuilds only the sets and
packages that are not published yet and pushes them when they give their
pinned digest; nothing is copied from `unemu/experimental/`. The guarantee
is the digest pin: a set or package reaches `unemu/media/` or
`unemu/firmware/` only when the pinned upcli gives exactly the digest its
manifest pins, the digest an experimental push of the same recipe gave as
well. The publication therefore does not depend on an experimental tag that
retention may have removed.

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
  repository variable of that name. The jobs that run recipes have no
  environment and no secret.
- The boundary is the job split: the push jobs run no recipe code, on a
  runner of their own, so a recipe cannot reach the credentials through a
  file it leaves behind, a process that outlives its step, or a changed
  `lib/publish.py` or upcli. They take from the build job its outputs
  (each checked against its pin) and whether anything was built;
  experimental's push job also takes the selection, publish's reads it
  from the input. Hiding variables is only a backstop on top of that:
  `lib/publish.py` hides from every recipe the `UNEMU*` and secret-like
  variables and the runner's own (`GITHUB_*`, `RUNNER_*`, `ACTIONS_*`,
  `BASH_ENV`, `ENV`), so a recipe cannot reach a later step's environment,
  path or outputs through them, but code that runs in a job can always find
  that job's files.
- The artifacts of a public repository can be downloaded by any signed-in
  GitHub user while they are kept (one day for experimental, 7 days for
  publish): the built outputs are public for that time, as they are once
  pushed to `unemu/experimental/`. They hold the recipes' outputs only,
  open by the same rules as the sets and packages.
- `cachix/install-nix-action` runs in the jobs that run recipes, never in
  a push job. It writes `access-tokens = github.com=<the job's
  GITHUB_TOKEN>` into `/etc/nix/nix.conf`, which a recipe can read (the
  token can only read this public repository, and expires with the job),
  and adds the runner's user to Nix's `trusted-users`.
- Every third-party action is pinned by its full commit SHA, with the
  release in a comment: `actions/checkout`, `actions/cache`,
  `actions/upload-artifact`, `actions/download-artifact` and
  `cachix/install-nix-action` (upstream Nix from its official installer,
  a short shell script, no telemetry).

GitHub-hosted runners use public DNS.
