# Publishing

`lib/publish.py` builds the recipes, imports their sets and packages with
the pinned unemupc CLI, `upcli`, into a scratch store, checks them, and
pushes them to the registry only when asked. Without `--push` it is a dry
run: it needs no credentials and never contacts a registry.

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
                          [--push | --allow-unpinned] [RECIPE|REF]...
```

| Option | Meaning |
| --- | --- |
| `--cli UPCLI` | the upcli binary; default `$UPCLI`; a name without a slash is looked up on `PATH` |
| `--store DIR` | import into `DIR`, a new directory that is kept (default: a fresh store under `build/`, removed afterwards) |
| `--experimental RUN` | push to the experimental targets (see below); `RUN` is the CI run number, from 1 |
| `--push` | push every checked set and package |
| `--allow-unpinned` | let a dry run pass with sets or packages that have no digest pin yet; never with `--push` |
| `RECIPE` or `REF` | select a recipe (`freedos-1.4-floppy`) or one set or package by its ref (`unemu/media/freedos:1.4`, `unemu/firmware/xub-r638-xtp-ata1f0:1`); default: everything |

The registry host is `UNEMU_REGISTRY_HOST`, checked as upcli checks one (a
name with a dot or a port, or `localhost`; no scheme or path). A dry run
without it names `registry.unemu.dev`, which it never contacts.

### Steps

A set or package that fails a step is not taken further.

1. `upcli.toml` is read, every recipe's manifest is checked, and no ref may
   appear twice across them. A refusal stops the run.
2. The version check (above).
3. Each selected recipe's `build.sh` runs (it stops at once when its outputs
   already match their pins), and every output is checked against its pin.
   A recipe never sees a variable whose name starts with `UNEMU` or contains
   `PASSWORD`, `TOKEN`, `SECRET` or `CREDENTIAL`.
4. Each set is imported with `upcli image media import` and checked with
   `upcli image media show`; each package is imported with `upcli image
   firmware import --for` and checked with `upcli image firmware show`,
   which must state the manifest's metadata, ROM, source archive and
   licences. Both shows must end by reporting it pushable to `unemu/`. Then
   `upcli image push --dry-run`'s guard must allow the target. upcli runs
   with an environment and a registries file of its own.
5. The digest is compared with the manifest's pin.
6. With `--push`, and only when everything selected passed and is pinned,
   each is pushed (see below).

### Outcomes

The run prints a table of repository, tag, digest, outcome and licence on
stdout, and its progress on stderr.

| Outcome | Meaning |
| --- | --- |
| `dry run` | checked, and the digest equals the manifest's pin |
| `unpinned` | checked, but the manifest pins no digest; never pushed |
| `mismatch` | upcli gives another digest than the pin |
| `failed` | a build, check, import, guard, registry read or push failed |
| `held` | not pushed, because another one failed or is unpinned, or an earlier push failed |
| `pushed` | pushed, and read back with the same digest |
| `present` | the registry already serves this digest under the tag |
| `refused` | the registry holds another digest under the tag |
| `unconfirmed` | pushed, but the read-back failed or named another digest |

The exit status is 0 when everything selected passed (and, with `--push`,
is on the registry), 1 otherwise, 2 for a usage error. An `unpinned` row
fails a dry run too, unless `--allow-unpinned` is given.

## Pushing

Pushing is the maintainer's step:

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
  (`present`); one that serves another digest is `refused`. upcli is never
  given `--move`, and the movable `latest` alias is not pushed.
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

## CI (intended, not yet wired)

The workflows are open work (beads um-7jx.6 and um-7jx.9). Until they run,
the digest pins are measured with the macOS arm64 upcli of the pinned
release; the Linux x86_64 one is first checked in CI. The intended setup,
already prepared on GitHub:

- every pull request runs the checks and the dry run, which needs no
  secrets; fork pull requests get none;
- on `main`, the environment `experimental` (deployment branch `main`)
  holds the secret `UNEMU_EXPERIMENTAL_PASSWORD` of the account
  `ci-experimental`, which may write only `unemu/experimental/**`; CI pushes
  there with `--experimental`;
- promotion runs in the protected environment `media` (deployment branch
  `main`, the maintainer as required reviewer), whose secret
  `UNEMU_PROMOTE_PASSWORD` belongs to the account `ci-promote`, which may
  write `unemu/media/**` and `unemu/firmware/**`; it moves a set or package
  by digest, unchanged;
- the repository variable `UNEMU_REGISTRY_HOST` names the registry;
- each step that writes maps its account and secret into
  `UNEMU_REGISTRY_USER` and `UNEMU_REGISTRY_PASSWORD`, for that step only.

GitHub-hosted runners use public DNS.
