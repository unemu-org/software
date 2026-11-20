#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Offline self-test of lib/source.sh, the helpers of the scripts that make
# prepared inputs. Every input is made here, in a fresh temporary directory:
# a few small files and two zip archives with fixed DOS timestamps (python3
# zipfile), and a local git repository with fixed dates. They are read
# through file:// URLs and local paths only, so nothing touches the network.
# The outputs are pinned below: a mismatch means the helpers, the
# environment they clear or the toolchain changed the bytes.
#
# The caller is made hostile before the library is sourced, so the pins and
# checks also show that sourcing and the helpers keep it out:
#   - variables: the plain C locale, another time zone, a tight umask,
#     TAR_OPTIONS (a blocking factor, which changes the tar's bytes), UNZIP
#     and ZIPINFO options and a SOURCE_DATE_EPOCH;
#   - files under HOME and XDG_CONFIG_HOME: a curl configuration that asks
#     for the first byte only (curl -q must skip it; an option the helper
#     also gives on its command line, such as --max-filesize, would be
#     overridden there and show nothing); a git configuration with CRLF line
#     endings that also rewrites the upstream repository's path to one that
#     does not exist (the git isolation must skip it); and git attributes
#     that turn every exported text file into CRLF (git_export's archive
#     settings must skip them).
#
# Run it inside nix develop: bash tests/source-selftest.sh
set -euo pipefail

TESTS="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
LIB="$TESTS/../lib/source.sh"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/source-selftest.XXXXXX")"
TMP="$(CDPATH='' cd -- "$TMP" && pwd -P)"
trap 'chmod -R u+w "$TMP" 2>/dev/null; rm -rf -- "$TMP"' EXIT
UPSTREAM="$TMP/upstream"

count=0
ok() {
	count=$((count + 1))
	printf 'ok %d - %s\n' "$count" "$1"
}
fail() {
	printf 'not ok - %s\n' "$1" >&2
	exit 1
}
# "$@" must be refused with FRAGMENT in its message. It runs in a subshell,
# so the refusal's exit stops only the subshell.
refuses() {
	local what="$1" fragment="$2"
	shift 2
	if ("$@") > "$TMP/refusal.log" 2>&1; then
		fail "$what: not refused"
	fi
	if ! grep -qF -- "$fragment" "$TMP/refusal.log"; then
		cat "$TMP/refusal.log" >&2
		fail "$what: refused, but without '$fragment'"
	fi
	ok "$what: refused"
}
mtime_of() {
	python3 -I -c 'import os, sys; print(int(os.stat(sys.argv[1]).st_mtime))' "$1"
}

# --- a hostile caller ---------------------------------------------------------
mkdir -p "$TMP/home/.config/git"
printf 'range = 0-0\n' > "$TMP/home/.curlrc"
cp "$TMP/home/.curlrc" "$TMP/home/.config/curlrc"
printf '[core]\n\tautocrlf = true\n[url "%s"]\n\tinsteadOf = %s\n' \
	"$TMP/no-such-upstream" "$UPSTREAM" > "$TMP/home/.gitconfig"
cp "$TMP/home/.gitconfig" "$TMP/home/.config/git/config"
# XDG_CONFIG_HOME is HOME/.config here, so this is also ~/.config/git/attributes.
printf '* text eol=crlf\n' > "$TMP/home/.config/git/attributes"
unset GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_CONFIG_NOSYSTEM
export HOME="$TMP/home" XDG_CONFIG_HOME="$TMP/home/.config" CURL_HOME="$TMP/home"
export LC_ALL=C TZ=Pacific/Kiritimati SOURCE_DATE_EPOCH=1
export TAR_OPTIONS=--blocking-factor=1 UNZIP=-aa UNZIPOPT=-aa ZIPINFO=-v ZIPINFOOPT=-v
umask 077

refuses "running the library directly" "sourced by a script" bash "$LIB"
# shellcheck disable=SC2016 # "$1" expands in the child shell
refuses "sourcing without pipefail" "set -euo pipefail" bash -c 'set -eu; . "$1"' sh "$LIB"
# shellcheck disable=SC2016 # "$1" expands in the child shell
refuses "sourcing without errexit" "set -euo pipefail" bash -c 'set -uo pipefail; . "$1"' sh "$LIB"
# shellcheck disable=SC2016 # "$1" expands in the child shell
refuses "sourcing without nounset" "set -euo pipefail" bash -c 'set -eo pipefail; . "$1"' sh "$LIB"

# shellcheck source-path=SCRIPTDIR source=../lib/source.sh
. "$LIB"
work_dir "$TMP/build"
# work_dir's trap removes WORK; this one removes all of TMP, WORK included.
trap 'chmod -R u+w "$TMP" 2>/dev/null; rm -rf -- "$TMP"' EXIT

if [ -n "${SOURCE_DATE_EPOCH+set}" ]; then
	fail "SOURCE_DATE_EPOCH from the environment survived sourcing"
fi
for variable in TAR_OPTIONS UNZIP UNZIPOPT ZIPINFO ZIPINFOOPT; do
	if [ -n "${!variable+set}" ]; then
		fail "$variable survived sourcing"
	fi
done
if [ "$LC_ALL" != C.UTF-8 ] || [ "$TZ" != UTC0 ] || [ "$(umask)" != 0022 ]; then
	fail "sourcing did not fix the locale, time zone or umask"
fi
ok "sourcing clears the caller's environment"
export SOURCE_DATE_EPOCH=315532800

# --- fixtures -----------------------------------------------------------------
# Python runs from -c strings: the devShell's bash deadlocks on a
# here-document over 512 bytes (see lib/source.sh).
FIX="$TMP/fixtures"
MAKE_FIXTURES='
import pathlib
import sys
import zipfile

root = pathlib.Path(sys.argv[1])
root.mkdir()
MEMBERS = [
    ("install.bat", (1993, 1, 1, 14, 0, 0),
     b"@ECHO OFF\r\nECHO Example installer of the recipe self-test.\r\n"),
    ("EXAMPLE.DAT", (1993, 1, 1, 14, 0, 2), bytes(range(256)) * 16),
    ("readme.txt", (1993, 1, 2, 9, 30, 0),
     b"Example release, made by the recipe self-test.\r\n"),
    ("VENDOR.DOC", (1993, 1, 2, 9, 30, 4),
     b"Example terms of the recipe self-test.\r\n"),
    ("FILE_ID.DIZ", (1993, 1, 3, 23, 59, 58),
     b"Example 1.0, a recipe self-test fixture\r\n"),
]


def write_zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, when, data in members:
            info = zipfile.ZipInfo(name, when)
            info.create_system = 0  # MS-DOS, as the shareware archives
            info.external_attr = 0x20  # the DOS archive attribute
            archive.writestr(info, data)


write_zip(root / "example.zip", MEMBERS)
write_zip(root / "other.zip", MEMBERS[:2])
(root / "license.txt").write_bytes(
    b"Example licence index of the recipe self-test.\n"
    b"Every file of the example set is a test fixture.\n"
)
'
python3 -I -c "$MAKE_FIXTURES" "$FIX"
ZIP_URL="file://$FIX/example.zip"
ZIP_SHA256="$(sha256_of "$FIX/example.zip")"
OTHER_SHA256="$(sha256_of "$FIX/other.zip")"
# The example archive's complete member list in archive order, with pins.
MEMBERS=(
	"install.bat fe295287557adb93e9fe39d42ee617c02b2ca7574dd8a58e748540e7de8358b9"
	"EXAMPLE.DAT c8f5d0341d54d951a71b136e6e2afcb14d11ed8489a7ae126a8fee0df6ecf193"
	"readme.txt d034c37e1c1733b42098fe5a7d26d2bd3ee58465e872cd39526433ae60428112"
	"VENDOR.DOC 9112ecce990cf8b7a4f33f570d166d78b8c46d964da2643a369b791a88413001"
	"FILE_ID.DIZ d238e35c967e4a7490cf98b61daac13fa66c78e35bce7f4923322ad6b133a473"
)
NAMES=()
for line in "${MEMBERS[@]}"; do
	NAMES+=("${line%% *}")
done
pin_of() {
	local line
	for line in "${MEMBERS[@]}"; do
		if [ "${line%% *}" = "$1" ]; then
			echo "${line##* }"
		fi
	done
}

cp "$FIX/license.txt" "$WORK/back\\slash"
if [ "$(sha256_of "$WORK/back\\slash")" != "$(sha256_of "$FIX/license.txt")" ]; then
	fail "sha256_of depends on the file name"
fi
ok "sha256_of does not depend on the file name"

# --- fetch --------------------------------------------------------------------
CACHE="$TMP/cache"
fetch "$CACHE/example.zip" "$ZIP_SHA256" 1048576 "$ZIP_URL"
cmp -s "$CACHE/example.zip" "$FIX/example.zip" || fail "fetch: the cached file differs"
ok "fetch from a file:// URL with the right hash, past a hostile curl configuration"

no_network() {
	# shellcheck disable=SC2329 # shadows curl: a download fails the check
	curl() {
		echo "curl was called" >&2
		exit 99
	}
	fetch "$@"
}
(no_network "$CACHE/example.zip" "$ZIP_SHA256" 1048576 "file://$TMP/no-such-file") \
	|| fail "fetch: a verified cached file was not used"
ok "fetch uses a verified cached file without downloading"

refuses "fetch with a wrong hash" "could not fetch" \
	fetch "$TMP/cache-wrong/example.zip" "$OTHER_SHA256" 1048576 "$ZIP_URL"
if [ -e "$TMP/cache-wrong" ]; then
	fail "fetch: a refused download reached the cache"
fi
if [ -n "$(find "$WORK" -maxdepth 1 -name '*.part')" ]; then
	fail "fetch: a refused download left a .part file"
fi
ok "a refused download leaves the cache and the work directory empty"

refuses "fetch over the size limit" "could not fetch" \
	fetch "$TMP/cache-small/example.zip" "$ZIP_SHA256" 100 "$ZIP_URL"
if [ -e "$TMP/cache-small" ]; then
	fail "fetch: an oversized download reached the cache"
fi

mkdir "$TMP/cache-tampered"
cp "$FIX/other.zip" "$TMP/cache-tampered/example.zip"
refuses "a cached file with a wrong hash" "Remove the cached file" \
	fetch "$TMP/cache-tampered/example.zip" "$ZIP_SHA256" 1048576 "$ZIP_URL"

fetch "$TMP/cache-mirror/example.zip" "$ZIP_SHA256" 1048576 \
	"file://$FIX/other.zip" "file://$TMP/no-such-file" "$ZIP_URL" 2> "$TMP/mirror.log"
if ! grep -qF "wrong file from file://$FIX/other.zip" "$TMP/mirror.log" \
	|| ! grep -qF "download failed: file://$TMP/no-such-file" "$TMP/mirror.log" \
	|| ! cmp -s "$TMP/cache-mirror/example.zip" "$FIX/example.zip"; then
	fail "fetch: the mirrors were not tried in turn after a wrong file and a failure"
fi
ok "fetch tries the next mirror after a wrong hash and after a failure"

refuses "fetch with an invalid pin" "invalid SHA-256 pin" \
	fetch "$TMP/cache-x/example.zip" "ABC" 1048576 "$ZIP_URL"

# --- zip members ----------------------------------------------------------------
ZIP="$CACHE/example.zip"
zip_expect_members "$ZIP" "${NAMES[@]}"
ok "zip_expect_members accepts the exact member list"
refuses "a missing member" "unexpected member list" \
	zip_expect_members "$ZIP" "${NAMES[@]:0:4}"
refuses "an added member" "unexpected member list" \
	zip_expect_members "$FIX/other.zip" "${NAMES[@]:0:2}" readme.txt
refuses "another member order" "unexpected member list" \
	zip_expect_members "$ZIP" EXAMPLE.DAT install.bat "${NAMES[@]:2}"

FILES=()
for name in "${NAMES[@]}"; do
	zip_extract "$ZIP" "$name" "$WORK/files" "$(pin_of "$name")"
	FILES+=("$WORK/files/$name")
done
mtime="$(mtime_of "$WORK/files/install.bat")"
if [ "$mtime" != 725896800 ]; then
	fail "zip_extract: install.bat has mtime $mtime, not 1993-01-01 14:00 UTC (725896800)"
fi
ok "zip_extract gives every listed member, verified, with its DOS time in UTC"

zip_extract "$ZIP" readme.txt "$WORK/partial" "$(pin_of readme.txt)"
zip_extract "$ZIP" VENDOR.DOC "$WORK/partial" "$(pin_of VENDOR.DOC)"
listing="$(cd "$WORK/partial" && find . -mindepth 1 | LC_ALL=C sort | tr '\n' ' ')"
if [ "$listing" != "./VENDOR.DOC ./readme.txt " ]; then
	fail "zip_extract: the directory holds '$listing', not only the listed members"
fi
ok "zip_extract extracts only the listed members"

refuses "a member with a wrong hash" "SHA-256 mismatch" \
	zip_extract "$ZIP" readme.txt "$WORK/wrong" "$(pin_of VENDOR.DOC)"
refuses "a member the archive lacks" "cannot extract" \
	zip_extract "$ZIP" MISSING.TXT "$WORK/missing" "$(pin_of readme.txt)"
refuses "a wildcard member name" "refusing member name" \
	zip_extract "$ZIP" '*.DAT' "$WORK/wildcard" "$(pin_of EXAMPLE.DAT)"
refuses "a member name leaving the directory" "refusing member name" \
	zip_extract "$ZIP" ../readme.txt "$WORK/escape" "$(pin_of readme.txt)"
refuses "extraction outside the work directory" "not inside the work directory" \
	zip_extract "$ZIP" readme.txt "$TMP/elsewhere" "$(pin_of readme.txt)"
refuses "extraction over an existing file" "already exists" \
	zip_extract "$ZIP" readme.txt "$WORK/partial" "$(pin_of readme.txt)"

# --- CRLF text and the times of files the helpers write --------------------------
no_epoch() {
	unset SOURCE_DATE_EPOCH
	"$@"
}
crlf "$FIX/license.txt" "$WORK/out/LICENSE.TXT"
printf 'Example licence index of the recipe self-test.\r\nEvery file of the example set is a test fixture.\r\n' > "$WORK/expected.txt"
cmp -s "$WORK/out/LICENSE.TXT" "$WORK/expected.txt" || fail "crlf: unexpected bytes"
if [ "$(mtime_of "$WORK/out/LICENSE.TXT")" != "$SOURCE_DATE_EPOCH" ]; then
	fail "crlf: the text does not carry SOURCE_DATE_EPOCH"
fi
ok "crlf ends every line in CR LF and gives the text SOURCE_DATE_EPOCH"
refuses "crlf of a file that holds CR" "already holds carriage returns" \
	crlf "$WORK/files/readme.txt" "$WORK/text/x.txt"
refuses "crlf outside the work directory" "not inside the work directory" \
	crlf "$FIX/license.txt" "$TMP/x.txt"
refuses "crlf without SOURCE_DATE_EPOCH" "export SOURCE_DATE_EPOCH" \
	no_epoch crlf "$FIX/license.txt" "$WORK/text/x.txt"

# --- git export -----------------------------------------------------------------
# The upstream repository, with fixed names and dates and no user
# configuration, so its commit id is fixed as well.
fixed_git() {
	GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 \
		GIT_AUTHOR_NAME=Example GIT_AUTHOR_EMAIL=example@example.invalid \
		GIT_AUTHOR_DATE='1993-01-01T00:00:00+0000' \
		GIT_COMMITTER_NAME=Example GIT_COMMITTER_EMAIL=example@example.invalid \
		GIT_COMMITTER_DATE='1993-01-01T00:00:00+0000' \
		git "$@"
}
fixed_git init --quiet --template= -b main "$UPSTREAM"
mkdir "$UPSTREAM/src"
printf 'Example upstream of the recipe self-test.\n' > "$UPSTREAM/README"
printf 'int main(void) { return 0; }\n' > "$UPSTREAM/src/example.c"
# Only archived, never run: it carries an executable mode into the tar.
printf '#!/bin/sh\necho example\n' > "$UPSTREAM/build.sh"
chmod 0755 "$UPSTREAM/build.sh"
printf '*.o\n' > "$UPSTREAM/.gitignore"
fixed_git -C "$UPSTREAM" add -A
fixed_git -C "$UPSTREAM" commit --quiet -m 'Example 1.0'
COMMIT="$(fixed_git -C "$UPSTREAM" rev-parse HEAD)"

STAGE="$WORK/stage"
mkdir -p "$STAGE/example-src"
cp "$WORK/out/LICENSE.TXT" "$STAGE/example-src/LICENSE.TXT"
git_export "$UPSTREAM" "$COMMIT" "$STAGE/example-src/upstream"
listing="$(cd "$STAGE/example-src/upstream" && find . | LC_ALL=C sort | tr '\n' ' ')"
if [ "$listing" != ". ./.gitignore ./README ./build.sh ./src ./src/example.c " ]; then
	fail "git_export: the export holds '$listing'"
fi
cmp -s "$STAGE/example-src/upstream/README" "$UPSTREAM/README" || fail "git_export: README changed (line endings?)"
if [ -n "$(find "$WORK" -maxdepth 1 -name 'git*')" ]; then
	fail "git_export: left a repository or archive in the work directory"
fi
ok "git_export fetches the pinned commit into a fresh repository, past a hostile git configuration"
git_export "$UPSTREAM" "$COMMIT" "$WORK/export-again" README src
listing="$(cd "$WORK/export-again" && find . | LC_ALL=C sort | tr '\n' ' ')"
if [ "$listing" != ". ./README ./src ./src/example.c " ]; then
	fail "git_export: the export of two paths holds '$listing'"
fi
ok "git_export exports only the paths given"
refuses "an export from a missing repository after an earlier export (nothing is cached)" "cannot fetch" \
	git_export "$TMP/no-such-repository" "$COMMIT" "$WORK/export-x"
refuses "an abbreviated commit id" "is not a full commit id" \
	git_export "$UPSTREAM" "${COMMIT:0:12}" "$WORK/export-x"
refuses "a commit the repository lacks" "cannot fetch" \
	git_export "$UPSTREAM" 0123456789abcdef0123456789abcdef01234567 "$WORK/export-x"
# A commit without dates, which git's own checks refuse to fetch
# (transfer.fsckObjects); without them it would be exported.
TREE="$(fixed_git -C "$UPSTREAM" rev-parse 'HEAD^{tree}')"
MALFORMED="$(printf 'tree %s\nauthor Example\ncommitter Example\n\nmalformed\n' "$TREE" \
	| fixed_git -C "$UPSTREAM" hash-object -t commit --literally -w --stdin)"
fixed_git -C "$UPSTREAM" update-ref refs/heads/malformed "$MALFORMED"
refuses "a malformed upstream commit" "cannot fetch" \
	git_export "$UPSTREAM" "$MALFORMED" "$WORK/export-x"
refuses "an export outside the work directory" "not inside the work directory" \
	git_export "$UPSTREAM" "$COMMIT" "$TMP/elsewhere"
mkdir "$WORK/staged"
printf 'staged by the recipe\n' > "$WORK/staged/README"
refuses "an export over a staged file" "never replaced" \
	git_export "$UPSTREAM" "$COMMIT" "$WORK/staged"
grep -qx 'staged by the recipe' "$WORK/staged/README" || fail "git_export replaced a staged file"

# Old history with a zero-padded tree mode (040000): a warning-level fsck
# message, fatal under transfer.fsckObjects unless --fsck-warn relaxes it
# for one export; errors stay fatal.
ENTRIES='
import sys
sys.stdout.buffer.write(b"100644 README\0" + bytes.fromhex(sys.argv[1])
                        + b"040000 src\0" + bytes.fromhex(sys.argv[2]))
'
LEGACY_TREE="$(python3 -I -c "$ENTRIES" \
	"$(fixed_git -C "$UPSTREAM" rev-parse HEAD:README)" "$(fixed_git -C "$UPSTREAM" rev-parse HEAD:src)" \
	| fixed_git -C "$UPSTREAM" hash-object -t tree --literally -w --stdin)"
LEGACY="$(fixed_git -C "$UPSTREAM" commit-tree "$LEGACY_TREE" -m 'Example 0.9')"
fixed_git -C "$UPSTREAM" update-ref refs/heads/legacy "$LEGACY"
refuses "a zero-padded tree mode, by default" "cannot fetch" \
	git_export "$UPSTREAM" "$LEGACY" "$WORK/legacy-x"
git_export --fsck-warn zeroPaddedFilemode "$UPSTREAM" "$LEGACY" "$WORK/legacy" 2> "$TMP/legacy.log"
listing="$(cd "$WORK/legacy" && find . | LC_ALL=C sort | tr '\n' ' ')"
if [ "$listing" != ". ./README ./src ./src/example.c " ] || ! grep -q zeroPaddedFilemode "$TMP/legacy.log"; then
	fail "git_export --fsck-warn: the export holds '$listing'"
fi
ok "git_export --fsck-warn relaxes a warning-level message for one export"
refuses "relaxing an error-level message" "errors stay fatal" \
	git_export --fsck-warn missingEmail "$UPSTREAM" "$COMMIT" "$WORK/legacy-x"
refuses "relaxing a path warning" "errors stay fatal" \
	git_export --fsck-warn hasDotdot "$UPSTREAM" "$COMMIT" "$WORK/legacy-x"
refuses "a malformed commit with a warning relaxed" "cannot fetch" \
	git_export --fsck-warn zeroPaddedFilemode "$UPSTREAM" "$MALFORMED" "$WORK/legacy-x"

# Symbolic links, which could lead a later write out of WORK.
OUTSIDE="$TMP/outside"
mkdir "$OUTSIDE"
LINKED="$TMP/upstream-link"
fixed_git init --quiet --template= -b main "$LINKED"
printf 'Example upstream with a link.\n' > "$LINKED/README"
ln -s "$OUTSIDE" "$LINKED/doc"
fixed_git -C "$LINKED" add -A
fixed_git -C "$LINKED" commit --quiet -m 'Example with a link'
LINK_COMMIT="$(fixed_git -C "$LINKED" rev-parse HEAD)"
refuses "a symbolic link in the commit" "holds a symbolic link, doc" \
	git_export "$LINKED" "$LINK_COMMIT" "$WORK/linked"
git_export "$LINKED" "$LINK_COMMIT" "$WORK/linked-readme" README
if [ ! -f "$WORK/linked-readme/README" ] || [ -e "$WORK/linked-readme/doc" ] || [ -L "$WORK/linked-readme/doc" ]; then
	fail "git_export: the export of README holds the link"
fi
ok "git_export refuses a link only among the paths it exports"
mkdir "$WORK/dest-link"
ln -s "$OUTSIDE" "$WORK/dest-link/doc"
refuses "an export into a directory holding a link" "already holds a symbolic link" \
	git_export "$UPSTREAM" "$COMMIT" "$WORK/dest-link"
refuses "crlf through a link out of WORK" "leads outside the work directory" \
	crlf "$FIX/license.txt" "$WORK/dest-link/doc/LICENSES.TXT"
refuses "zip_extract through a link out of WORK" "leads outside the work directory" \
	zip_extract "$ZIP" readme.txt "$WORK/dest-link/doc" "$(pin_of readme.txt)"
refuses "a source tar through a link out of WORK" "leads outside the work directory" \
	source_tar "$WORK/dest-link/doc/x.tar" example-src 725846400 "$WORK/stage" upstream/.gitignore
if [ -n "$(ls -A "$OUTSIDE")" ]; then
	fail "a helper wrote outside the work directory: $(ls -A "$OUTSIDE")"
fi
ok "no helper wrote through a link out of the work directory"

# --- source tar ---------------------------------------------------------------
EPOCH=725846400 # 1993-01-01 00:00 UTC
refuses "a dot-file that is not on the allow list" "upstream/.gitignore is a dot entry" \
	source_tar "$WORK/x.tar" example-src "$EPOCH" "$STAGE"
source_tar "$WORK/src-a.tar" example-src "$EPOCH" "$STAGE" upstream/.gitignore
source_tar "$WORK/src-b.tar" example-src "$EPOCH" "$STAGE" upstream/.gitignore
cmp -s "$WORK/src-a.tar" "$WORK/src-b.tar" || fail "source_tar: two runs differ"
if [ "$(mtime_of "$WORK/src-a.tar")" != "$SOURCE_DATE_EPOCH" ]; then
	fail "source_tar: the archive file does not carry SOURCE_DATE_EPOCH"
fi
ok "source_tar gives identical bytes on two runs"

CHECK_TAR='
import sys
import tarfile

expected = [
    ("example-src", 0o755),
    ("example-src/LICENSE.TXT", 0o644),
    ("example-src/upstream", 0o755),
    ("example-src/upstream/.gitignore", 0o644),
    ("example-src/upstream/README", 0o644),
    ("example-src/upstream/build.sh", 0o755),
    ("example-src/upstream/src", 0o755),
    ("example-src/upstream/src/example.c", 0o644),
]
with tarfile.open(sys.argv[1]) as archive:
    members = archive.getmembers()
got = [(m.name, m.mode) for m in members]
headers = {(m.uid, m.gid, m.uname, m.gname, m.mtime) for m in members}
if got != expected or headers != {(0, 0, "", "", int(sys.argv[2]))}:
    print(got, headers, file=sys.stderr)
    sys.exit(1)
'
python3 -I -c "$CHECK_TAR" "$WORK/src-a.tar" "$EPOCH" || fail "source_tar: unexpected members or headers"
ok "source_tar fixes member order, modes, owners and timestamps"

mkdir "$STAGE/example-src/upstream/.beads"
printf 'sync.remote: example\n' > "$STAGE/example-src/upstream/.beads/config.yaml"
refuses "a planted .beads/config.yaml" "upstream/.beads is a dot entry" \
	source_tar "$WORK/x.tar" example-src "$EPOCH" "$STAGE" upstream/.gitignore
rm -r "$STAGE/example-src/upstream/.beads"
ln -s README "$STAGE/example-src/upstream/link"
refuses "a symbolic link in the stage" "is not a regular file or directory" \
	source_tar "$WORK/x.tar" example-src "$EPOCH" "$STAGE" upstream/.gitignore
rm "$STAGE/example-src/upstream/link"
refuses "a source tar outside the work directory" "not inside the work directory" \
	source_tar "$TMP/x.tar" example-src "$EPOCH" "$STAGE" upstream/.gitignore

# --- the pinned outputs and their install ---------------------------------------
# The bytes of a CRLF text and of a source tar, pinned: they depend on the
# helpers, the environment they clear and the toolchain (nix develop).
TEXT_PIN=62cd3ab8fbc7b434392a6ec193a38068424d59473cc2478f59328271578ca249
TAR_PIN=77ee30e50839eb339ccedf6b9a09da5f3c4f0f376a5314c65e5b0c3c1fecefc2
if ! matches "$WORK/out/LICENSE.TXT" "$TEXT_PIN" || ! matches "$WORK/src-a.tar" "$TAR_PIN"; then
	echo "the outputs differ from their pins; they are:" >&2
	printf '  LICENSE.TXT %s\n  example-src.tar %s\n' "$(sha256_of "$WORK/out/LICENSE.TXT")" \
		"$(sha256_of "$WORK/src-a.tar")" >&2
	fail "a pinned output changed"
fi
ok "the CRLF text and the source tar match their pins"

OUT="$TMP/out/example"
install_output "$WORK/src-b.tar" "$OUT"
cmp -s "$OUT/src-b.tar" "$WORK/src-a.tar" || fail "install_output: the file changed on the way"
if [ -e "$WORK/src-b.tar" ]; then
	fail "install_output: the file is still in the work directory"
fi
ok "install_output moves a checked file into out/"
refuses "installing a file from outside the work directory" "not inside the work directory" \
	install_output "$FIX/license.txt" "$OUT"
cp "$WORK/src-a.tar" "$WORK/src-c.tar"
mkdir -p "$TMP/linked-target"
ln -s "$TMP/linked-target" "$OUT/src-c.tar"
install_output "$WORK/src-c.tar" "$OUT"
if [ -L "$OUT/src-c.tar" ] || [ -n "$(ls -A "$TMP/linked-target")" ]; then
	fail "install_output followed a link at the file's path"
fi
ok "install_output replaces a link at the file's path instead of following it"

echo "all $count checks passed"
