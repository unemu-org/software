# SPDX-License-Identifier: AGPL-3.0-or-later
# shellcheck shell=bash
#
# The shared steps of the scripts that make a prepared input: a file a
# recipe takes by size and SHA-256 that upcli's recipe steps cannot make
# (docs/recipes.md). Today that is one script,
# recipes/media/freedos/source/build.sh, which makes the FreeDOS source
# archive. A script pins its inputs, sources this file and calls the helpers
# below; this file is never run on its own. Each step follows the strictest
# version of it in unemupc98's vendor scripts (vendor/shareware/common.sh,
# vendor/freedos/fetch-floppy-1.4.sh, vendor/vgabios/fetch.sh).
#
# Nothing downloaded is ever executed or trusted unverified: every input is
# checked against its pinned SHA-256 before use, a cached copy included; an
# archive gives up only the members the script lists, into the run's fresh
# work directory; the output is built in that directory and reaches out/
# only through install_output, once the script has checked it against its
# pin. The same script gives the same bytes on every run and host: settings
# that change what the tools write are cleared, the locale, time zone and
# umask are fixed, and every timestamp comes from the release or the script,
# never from the clock.
#
# A script runs with `set -euo pipefail` before sourcing this file (it is
# refused otherwise), and after sourcing it:
#   - calls work_dir once, before any other helper that writes;
#   - exports SOURCE_DATE_EPOCH, the time of every file the helpers write
#     themselves (source tars, CRLF texts). A value from the caller's
#     environment is cleared on sourcing, so the script's own is the only one
#     (nix develop exports one of its own).
#
# Helpers; each refusal prints its reason on stderr and exits 1:
#   require_tools TOOL...                       every TOOL is on PATH
#   sha256_of FILE                              print FILE's SHA-256
#   matches FILE SHA256                         FILE is a regular file (not a
#                                               link) with that hash
#   check_sha256 FILE SHA256 ROLE               refuse a mismatch; ROLE is
#                                               cached, member or output
#   file_size FILE                              print FILE's size in bytes
#   work_dir [PARENT]                           fresh $WORK under PARENT
#                                               (default build/), removed on
#                                               exit
#   fetch DEST SHA256 MAX_BYTES URL...          a verified download, cached
#                                               at DEST
#   zip_expect_members ZIP MEMBER...            ZIP holds exactly these, in
#                                               this order
#   zip_extract ZIP MEMBER DIR SHA256           one listed member into DIR
#                                               (in $WORK), verified
#   git_export [--fsck-warn ID]... REPO COMMIT DEST [PATH...]
#                                               a pinned commit's files into
#                                               DEST (in $WORK)
#   source_tar OUT NAME EPOCH STAGE [ALLOWED...]  deterministic tar of
#                                               STAGE/NAME at OUT (in $WORK)
#   crlf IN OUT                                 LF text to CRLF at OUT (in
#                                               $WORK)
#   install_output FILE DIR                     move FILE (in $WORK, checked
#                                               by the script) into DIR
#   stamp FILE...                               give FILE... the time
#                                               SOURCE_DATE_EPOCH
#
# Requires Bash 4 or later, GNU coreutils, findutils and tar, awk, the
# C.UTF-8 locale and, for the helpers that use them, Info-ZIP UnZip, curl,
# git and python3 (3.11 or later). Run the scripts inside `nix develop`: the
# flake pins all of them, and the CA roots curl verifies downloads against.
#
# Scripts use no here-document or here-string longer than 512 bytes: the
# devShell's bash 5.3 on macOS hangs on them (measured on aarch64-darwin).
# Pass long text through a file, a variable or process substitution.

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
	echo "lib/source.sh is sourced by a script, not run" >&2
	exit 1
fi
if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
	echo "bash $BASH_VERSION is too old for lib/source.sh: run the script inside nix develop" >&2
	exit 1
fi
# The helpers refuse explicitly, but a failure they do not foresee must stop
# the script too, also inside a pipeline.
if [[ ! -o errexit || ! -o nounset || ! -o pipefail ]]; then
	echo "run the script with set -euo pipefail before sourcing lib/source.sh" >&2
	exit 1
fi

refuse() {
	printf '%s\n' "$*" >&2
	exit 1
}

# The repository root, from this file's own location. An empty CDPATH keeps
# cd from printing a directory into the result.
ROOT="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# Set by work_dir only, never taken from the environment.
WORK=""
# The time one download may take. The size limit is per call (fetch).
FETCH_MAX_TIME=1800

# Both GNU/Linux and macOS provide one of these SHA-256 tools.
if command -v sha256sum >/dev/null 2>&1; then
	SHA256=(sha256sum)
elif command -v shasum >/dev/null 2>&1; then
	SHA256=(shasum -a 256)
else
	refuse "missing SHA-256 tool: install sha256sum or shasum (or use nix develop)"
fi

require_tools() {
	local tool hint
	for tool in "$@"; do
		if ! command -v "$tool" >/dev/null 2>&1; then
			case "$tool" in
				unzip | curl | git | python3 | tar) hint="nix develop provides it" ;;
				*) hint="install it with the host's package manager" ;;
			esac
			refuse "missing $tool on PATH: $hint"
		fi
	done
}

# Keep the caller's environment out of the bytes.
# - GNU tar takes extra options from TAR_OPTIONS. Info-ZIP's UNZIP, UNZIPOPT,
#   ZIPINFO and ZIPINFOOPT add default options that change names, bytes or
#   timestamps.
# - The locale is C.UTF-8, the one the pinned archive was made under; the
#   steps that compare or map bytes (find, tar's name order) run under
#   LC_ALL=C.
# - TZ=UTC0, a POSIX zone that needs no zoneinfo, makes unzip and mcopy
#   convert the archives' DOS dates and times identically on every host.
# - SOURCE_DATE_EPOCH is cleared: the script sets its own.
# - The umask fixes the modes of extracted and staged files.
# - git never waits for a password prompt.
unset TAR_OPTIONS UNZIP UNZIPOPT ZIPINFO ZIPINFOOPT SOURCE_DATE_EPOCH
export LC_ALL=C.UTF-8 TZ=UTC0 GIT_TERMINAL_PROMPT=0
if [ "$(locale charmap 2>/dev/null)" != "UTF-8" ]; then
	refuse "the C.UTF-8 locale is not available on this host"
fi
umask 022
# BSD tar (the macOS default) cannot fix member order, owners and modes.
case "$(tar --version 2>/dev/null)" in
	*"GNU tar"*) ;;
	*) refuse "missing GNU tar: run the script inside nix develop" ;;
esac

# Read from standard input, so no file name can change the tool's output
# (GNU sha256sum escapes a name that holds a backslash or a newline).
sha256_of() {
	"${SHA256[@]}" < "$1" | awk '{print $1}'
}

# Whether a file is a regular file, not a link, holding the pinned bytes.
matches() {
	[ -f "$1" ] && [ ! -L "$1" ] && [ "$(sha256_of "$1")" = "$2" ]
}

is_sha256() {
	[[ "$1" =~ ^[0-9a-f]{64}$ ]]
}

file_size() {
	wc -c < "$1" | tr -d '[:space:]'
}

# $3 says what the file is, so the hint names the right remedy.
check_sha256() {
	local file="$1" expected="$2" role="$3" actual
	actual="$(sha256_of "$file")"
	if [ "$actual" = "$expected" ]; then
		return 0
	fi
	echo "SHA-256 mismatch for $file: expected $expected, got $actual" >&2
	case "$role" in
		cached) echo "Remove the cached file and run again; do not change its expected hash." >&2 ;;
		member) echo "The archive is not the pinned release; do not change the expected hash." >&2 ;;
		output) echo "Check the pinned inputs and the toolchain (nix develop, Info-ZIP UnZip 6.00, GNU tar); review the output deliberately before changing its pin, and the recipes' pins with it." >&2 ;;
	esac
	exit 1
}

# A relative path made only of characters DOS and Unix names share: no
# empty, "." or ".." component and no leading dot, so it cannot leave the
# directory it is joined to; none of unzip's pattern characters (* ? [ ]) or
# its escape (\), so unzip matches exactly one member.
safe_relative_path() {
	local lead="[A-Za-z0-9_~!#\$%&'(){}@^+-]" char="[A-Za-z0-9._~!#\$%&'(){}@^+-]"
	[[ "$1" =~ ^${lead}${char}*(/${lead}${char}*)*$ ]]
}

need_work() {
	if [ -z "$WORK" ] || [ ! -d "$WORK" ]; then
		refuse "$1: call work_dir first"
	fi
}

# PATH lies inside the run's work directory, so a helper writes nothing a
# later step could mistake for a checked output. Both the spelling and the
# resolved path count: a symbolic link on the way (one a script staged, say)
# must not lead the write elsewhere. WORK itself is a physical path
# (work_dir), and realpath -m resolves every link in PATH, existing or not.
inside_work() {
	local resolved
	need_work "$2"
	case "$1" in
		"$WORK"/?*) ;;
		*) refuse "$2: $1 is not inside the work directory $WORK" ;;
	esac
	case "/$1/" in
		*/../* | */./*) refuse "$2: $1 has a '.' or '..' component" ;;
	esac
	resolved="$(realpath -m -- "$1")" \
		|| refuse "$2: cannot resolve $1 (GNU realpath is needed; nix develop provides it)"
	case "$resolved" in
		"$WORK"/?*) ;;
		*) refuse "$2: $1 leads outside the work directory through a symbolic link (to $resolved)" ;;
	esac
}

need_epoch() {
	if [[ ! "${SOURCE_DATE_EPOCH:-}" =~ ^[0-9]+$ ]]; then
		refuse "$1: export SOURCE_DATE_EPOCH in the script (the time of the files the helpers write)"
	fi
}

# Every file a helper writes itself carries SOURCE_DATE_EPOCH, never the
# clock, so it gives the same bytes wherever a later step records its time
# (an image entry, for one).
stamp() {
	touch -d "@$SOURCE_DATE_EPOCH" -- "$@" \
		|| refuse "cannot set the time of $*: GNU touch is needed (nix develop provides it)"
}

# The run's own directory for downloads in progress, extracted members,
# staging trees, git fetches and outputs before their check. mktemp makes a
# new one for every run, so nothing an earlier run left is reused, and the
# EXIT trap removes it whatever stops the script (read-only copies are made
# writable first). Call it directly, not in $(...), so WORK reaches the
# script; it owns the EXIT trap, so other temporary files belong inside WORK.
work_dir() {
	local parent="${1:-$ROOT/build}"
	if [ -n "$WORK" ]; then
		refuse "work_dir: the work directory is already $WORK"
	fi
	mkdir -p -- "$parent" || refuse "work_dir: cannot create $parent"
	parent="$(CDPATH='' cd -- "$parent" && pwd -P)" || refuse "work_dir: cannot enter $parent"
	WORK="$(mktemp -d "$parent/work.XXXXXX")" || refuse "work_dir: cannot create a work directory in $parent"
	# shellcheck disable=SC2064 # the path is fixed now, quoted for the trap
	trap "chmod -R u+w $(printf '%q' "$WORK") 2>/dev/null; rm -rf -- $(printf '%q' "$WORK")" EXIT
}

# Fetch one pinned file into the cache at DEST. A cached copy is re-checked
# byte for byte and refused, never replaced, when it differs. Otherwise each
# URL is tried in turn until one gives the pinned bytes: the download lands
# in WORK, so a partial or wrong answer never reaches the cache name, and a
# failure or a wrong hash moves on to the next mirror. curl -q comes first so
# no ~/.curlrc applies; the limits let a stalled or oversized answer give way;
# only HTTPS is followed (file:// serves local copies and the self-test). The
# file is only ever read, never executed.
fetch() {
	if [ "$#" -lt 4 ]; then
		refuse "usage: fetch DEST SHA256 MAX_BYTES URL..."
	fi
	local dest="$1" expected="$2" max_bytes="$3" url part got
	shift 3
	is_sha256 "$expected" || refuse "fetch $dest: invalid SHA-256 pin '$expected'"
	[[ "$max_bytes" =~ ^[1-9][0-9]*$ ]] || refuse "fetch $dest: invalid size limit '$max_bytes'"
	if [ -e "$dest" ] || [ -L "$dest" ]; then
		if [ -L "$dest" ] || [ ! -f "$dest" ]; then
			refuse "cached $dest is not a regular file; remove it and run again"
		fi
		check_sha256 "$dest" "$expected" cached
		return 0
	fi
	need_work fetch
	require_tools curl
	part="$WORK/fetch.$(basename -- "$dest").part"
	for url in "$@"; do
		rm -f -- "$part" || refuse "fetch: cannot remove $part"
		if ! curl -q --fail --silent --show-error --location --retry 2 \
			--connect-timeout 20 --max-time "$FETCH_MAX_TIME" --max-filesize "$max_bytes" \
			--proto '=https,file' --proto-redir '=https' -o "$part" "$url"; then
			echo "download failed: $url" >&2
			continue
		fi
		got="$(sha256_of "$part")"
		if [ "$got" != "$expected" ]; then
			echo "wrong file from $url: $(file_size "$part") bytes, SHA-256 $got; trying the next source" >&2
			continue
		fi
		# Only a verified file enters the cache.
		mkdir -p -- "$(dirname -- "$dest")" || refuse "fetch: cannot create the cache directory of $dest"
		mv -fT -- "$part" "$dest" || refuse "fetch: cannot move the verified download to $dest"
		return 0
	done
	rm -f -- "$part"
	echo "could not fetch $(basename -- "$dest") with SHA-256 $expected from:" >&2
	printf '  %s\n' "$@" >&2
	exit 1
}

# A repacked archive with an added, missing or reordered member is not the
# release, even if the members that are present match.
zip_expect_members() {
	if [ "$#" -lt 2 ]; then
		refuse "usage: zip_expect_members ZIP MEMBER..."
	fi
	local zip="$1" expected actual
	shift
	require_tools unzip
	expected="$(printf '%s\n' "$@")"
	actual="$(unzip -Z1 "$zip")" || refuse "cannot list the members of $zip"
	if [ "$actual" != "$expected" ]; then
		echo "unexpected member list in $zip:" >&2
		printf '%s\n' "$actual" >&2
		exit 1
	fi
}

# One listed member, extracted by its exact name into DIR inside the run's
# fresh work directory, then checked. The name must be a safe relative path,
# so unzip matches no other member and writes nowhere else; nothing is
# overwritten (-n), and the result must be a regular file, not a link. unzip
# restores the member's DOS date and time (under TZ=UTC0). Nothing
# extracted is ever run; unzip reads past a self-extractor's
# program stub without running it.
zip_extract() {
	if [ "$#" -ne 4 ]; then
		refuse "usage: zip_extract ZIP MEMBER DIR SHA256"
	fi
	local zip="$1" member="$2" dir="$3" expected="$4"
	is_sha256 "$expected" || refuse "zip_extract $member: invalid SHA-256 pin '$expected'"
	inside_work "$dir" zip_extract
	require_tools unzip
	safe_relative_path "$member" || refuse "zip_extract: refusing member name '$member'"
	if [ -e "$dir/$member" ] || [ -L "$dir/$member" ]; then
		refuse "zip_extract: $dir/$member already exists"
	fi
	mkdir -p -- "$dir" || refuse "zip_extract: cannot create $dir"
	unzip -q -n "$zip" "$member" -d "$dir" || refuse "cannot extract $member from $zip"
	if [ -L "$dir/$member" ] || [ ! -f "$dir/$member" ]; then
		refuse "zip_extract: $member in $zip is not a regular file"
	fi
	check_sha256 "$dir/$member" "$expected" member
}

# git with nothing from the caller or from a repository's own files: no
# repository variables (GIT_DIR and the others git lists as local), no
# global or system configuration (a user's url.insteadOf, tar.umask or
# signing settings), no hooks, HTTPS or local repositories only, and every
# fetched object checked (transfer.fsckObjects). The pinned commit id names
# the content; the source archive's pin is the final guard.
run_git() {
	(
		local_vars="$(git rev-parse --local-env-vars)" || exit 1
		# shellcheck disable=SC2086 # one variable name per word
		unset $local_vars
		export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_ALLOW_PROTOCOL=https:file
		exec git -c core.hooksPath=/dev/null -c transfer.fsckObjects=true "$@"
	)
}

# The fsck messages a script may relax to warnings for one export
# (--fsck-warn): of the messages git 2.54 rates WARN (git-fsck(1), FSCK
# MESSAGES), those found in old but sound history that cannot change which
# paths an export writes. Path warnings (hasDot, hasDotdot, hasDotgit,
# fullPathname, emptyName, largePathname) and every error stay fatal.
FSCK_WARN_ALLOWED=(zeroPaddedFilemode nulInCommit)

# The files of a pinned commit, or only PATH..., into DEST inside WORK. Every
# run fetches the commit with depth 1, by URL and with no named remote, into
# a fresh empty repository under WORK (git init with no template), so no
# earlier clone, its hooks, its configuration or its objects are ever
# trusted. Every fetched object passes git's checks (transfer.fsckObjects);
# each --fsck-warn ID before REPO relaxes one message of FSCK_WARN_ALLOWED to
# a warning for this export. The commit's files are listed first: a symbolic
# link or a submodule among the exported paths is refused (a link could lead
# a later write out of WORK; a submodule's files would be missing from the
# source), and so is any link already in DEST. git archive runs with fixed
# line-ending and attribute settings, so no host configuration changes the
# bytes; the tar goes through WORK and GNU tar extracts it with -k, so a file
# already staged in DEST is never replaced. Exported files keep the commit's
# time (git archive).
git_export() {
	local warn=() id allowed found repo commit dest clone archive list entry
	while [ "${1:-}" = --fsck-warn ]; do
		id="${2:-}"
		found=0
		for allowed in "${FSCK_WARN_ALLOWED[@]}"; do
			if [ "$id" = "$allowed" ]; then
				found=1
			fi
		done
		if [ "$found" -eq 0 ]; then
			refuse "git_export: --fsck-warn '$id': only ${FSCK_WARN_ALLOWED[*]} can be relaxed; errors stay fatal"
		fi
		warn+=(-c "fetch.fsck.$id=warn")
		shift 2
	done
	if [ "$#" -lt 3 ]; then
		refuse "usage: git_export [--fsck-warn ID]... REPO COMMIT DEST [PATH...]"
	fi
	repo="$1" commit="$2" dest="$3"
	shift 3
	[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || refuse "git_export: '$commit' is not a full commit id"
	inside_work "$dest" git_export
	require_tools git
	if [ "$#" -eq 0 ]; then
		set -- .
	fi
	list="$(mktemp "$WORK/git-list.XXXXXX")" || refuse "git_export: cannot create a list file"
	if [ -e "$dest" ]; then
		find_entries "$list" "$dest" -type l
		if [ -s "$list" ]; then
			refuse "git_export: $dest already holds a symbolic link; export into a directory without links"
		fi
	fi
	clone="$(mktemp -d "$WORK/git.XXXXXX")" || refuse "git_export: cannot create a repository directory"
	run_git init --quiet --template= "$clone" || refuse "git_export: cannot create a repository in $clone"
	run_git -C "$clone" "${warn[@]}" fetch --quiet --depth 1 "$repo" "$commit" \
		|| refuse "git_export: cannot fetch $commit from $repo"
	run_git -C "$clone" cat-file -e "$commit^{commit}" \
		|| refuse "git_export: $repo did not give commit $commit"
	run_git -C "$clone" ls-tree -r -z --full-tree "$commit" -- "$@" > "$list" \
		|| refuse "git_export: cannot list the files of $commit"
	while IFS= read -r -d '' entry; do
		case "${entry%% *}" in
			120000) refuse "git_export: $commit holds a symbolic link, ${entry#*$'\t'}" ;;
			160000) refuse "git_export: $commit holds a submodule, ${entry#*$'\t'}, whose files the export would miss" ;;
		esac
	done < "$list"
	rm -f -- "$list"
	archive="$(mktemp "$WORK/git-export.XXXXXX")" || refuse "git_export: cannot create a temporary archive"
	run_git -C "$clone" -c core.autocrlf=false -c core.eol=lf -c core.attributesFile=/dev/null \
		archive --format=tar --output="$archive" "$commit" -- "$@" \
		|| refuse "git_export: cannot export $commit"
	mkdir -p -- "$dest" || refuse "git_export: cannot create $dest"
	tar -x -k -f "$archive" -C "$dest" \
		|| refuse "git_export: cannot extract $commit into $dest (a file already there is never replaced)"
	rm -rf -- "$clone" "$archive" || refuse "git_export: cannot remove $clone"
}

# Every entry under DIR that FIND_ARGS select, NUL-separated, into a file in
# WORK; byte-wise (LC_ALL=C), and a failing find stops the script.
find_entries() {
	local list="$1" dir="$2"
	shift 2
	LC_ALL=C find "$dir" "$@" -print0 > "$list" || refuse "cannot list $dir"
}

# A deterministic tar of STAGE/NAME at OUT, both inside WORK: member order,
# timestamps (EPOCH, seconds since 1970), owners and modes are fixed so the
# archive has the same bytes on every host; ustar stores no atime or ctime.
# The archive file itself carries SOURCE_DATE_EPOCH. The stage holds only
# files the script copied or exported from explicit lists, and is checked
# before packing: anything but regular files and directories is refused, and
# so is every dot entry (any name starting with ".", .beads/ included) that is
# not on the ALLOWED list, given as paths relative to STAGE/NAME. An allowed
# dot directory admits what it holds, except further dot entries. A
# developer's local files never reach a published archive (vgabios once
# shipped a .beads/config.yaml naming a private host).
source_tar() {
	if [ "$#" -lt 4 ]; then
		refuse "usage: source_tar OUT NAME EPOCH STAGE [ALLOWED...]"
	fi
	local out="$1" name="$2" epoch="$3" stage="$4" entry rel allowed found archive list
	shift 4
	[[ "$name" =~ ^[A-Za-z0-9][A-Za-z0-9._+-]*$ ]] || refuse "source_tar: invalid archive name '$name'"
	[[ "$epoch" =~ ^[0-9]+$ ]] || refuse "source_tar: invalid epoch '$epoch'"
	need_epoch source_tar
	inside_work "$out" source_tar
	inside_work "$stage" source_tar
	if [ -L "$stage/$name" ] || [ ! -d "$stage/$name" ]; then
		refuse "source_tar: $stage/$name is not a directory"
	fi
	list="$(mktemp "$WORK/source-list.XXXXXX")" || refuse "source_tar: cannot create a list file"
	find_entries "$list" "$stage/$name" ! -type f ! -type d
	while IFS= read -r -d '' entry; do
		refuse "source_tar: ${entry#"$stage/"} is not a regular file or directory"
	done < "$list"
	find_entries "$list" "$stage/$name" -mindepth 1 -name '.*'
	while IFS= read -r -d '' entry; do
		rel="${entry#"$stage/$name/"}"
		found=0
		for allowed in "$@"; do
			if [ "$rel" = "$allowed" ]; then
				found=1
			fi
		done
		if [ "$found" -eq 0 ]; then
			refuse "source_tar: $name/$rel is a dot entry that is not on the script's allow list; stage files from explicit lists only"
		fi
	done < "$list"
	rm -f -- "$list"
	archive="$(mktemp "$WORK/source-tar.XXXXXX")" || refuse "source_tar: cannot create a temporary archive"
	LC_ALL=C tar --create --file="$archive" --directory="$stage" \
		--format=ustar --sort=name --hard-dereference --mtime="@$epoch" \
		--owner=0 --group=0 --numeric-owner --mode='a=rX,u+w,ug-s' "$name" \
		|| refuse "source_tar: tar failed for $name"
	chmod 0644 "$archive" || refuse "source_tar: cannot set the mode of $archive"
	stamp "$archive"
	mkdir -p -- "$(dirname -- "$out")" || refuse "source_tar: cannot create the directory of $out"
	mv -fT -- "$archive" "$out" || refuse "source_tar: cannot move the archive to $out"
}

# DOS readers expect CRLF. Every line of IN, the last one included, ends in
# CR LF at OUT, inside WORK; awk works byte-wise under LC_ALL=C. IN must hold
# no CR already, so a CRLF file is never turned into CR CR LF. OUT carries
# SOURCE_DATE_EPOCH, so an image holding it does not depend on the clock.
crlf() {
	if [ "$#" -ne 2 ]; then
		refuse "usage: crlf IN OUT"
	fi
	local in="$1" out="$2" part status=0
	if [ -L "$in" ] || [ ! -f "$in" ]; then
		refuse "crlf: $in is not a regular file"
	fi
	need_epoch crlf
	inside_work "$out" crlf
	LC_ALL=C grep -q $'\r' "$in" || status=$?
	case "$status" in
		0) refuse "crlf: $in already holds carriage returns" ;;
		1) ;;
		*) refuse "crlf: cannot read $in" ;;
	esac
	part="$(mktemp "$WORK/crlf.XXXXXX")" || refuse "crlf: cannot create a temporary file"
	LC_ALL=C awk '{ printf "%s\r\n", $0 }' "$in" > "$part" || refuse "crlf: cannot convert $in"
	chmod 0644 "$part" || refuse "crlf: cannot set the mode of $part"
	stamp "$part"
	mkdir -p -- "$(dirname -- "$out")" || refuse "crlf: cannot create the directory of $out"
	mv -fT -- "$part" "$out" || refuse "crlf: cannot move the text to $out"
}

# The only way into out/: FILE, built and checked by the script inside WORK,
# moves into DIR under its own name. DIR itself may be a symbolic link (an
# out/ kept elsewhere); a link at the file's own path is replaced, not
# followed (mv -T).
install_output() {
	if [ "$#" -ne 2 ]; then
		refuse "usage: install_output FILE DIR"
	fi
	local file="$1" dir="$2" name
	inside_work "$file" install_output
	if [ -L "$file" ] || [ ! -f "$file" ]; then
		refuse "install_output: $file is not a regular file"
	fi
	name="$(basename -- "$file")"
	if [ -d "$dir/$name" ] && [ ! -L "$dir/$name" ]; then
		refuse "install_output: $dir/$name is a directory"
	fi
	mkdir -p -- "$dir" || refuse "install_output: cannot create $dir"
	mv -fT -- "$file" "$dir/$name" || refuse "install_output: cannot move $name into $dir"
}
