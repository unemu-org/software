# SPDX-License-Identifier: AGPL-3.0-or-later
# shellcheck shell=bash
#
# The shared steps of every recipe. A recipe (recipes/<recipe>/build.sh) pins
# its inputs, sources this file and calls the helpers below; this file is
# never run on its own. Each step follows the strictest version of it in
# unemupc98's vendor scripts (vendor/shareware/common.sh,
# vendor/freedos/fetch-floppy-1.4.sh, vendor/vgabios/fetch.sh).
#
# Nothing downloaded is ever executed or trusted unverified: every input is
# checked against its pinned SHA-256 before use, a cached copy included; an
# archive gives up only the members the recipe lists, into the run's fresh
# work directory; every output is built in that directory and reaches out/
# only through install_outputs, once every pin in the recipe's manifest
# (media.toml or firmware.toml, read by lib/manifest.py) matches. The same
# recipe gives the same bytes on every run and host: settings that change
# what the tools write are cleared, the locale, time zone and umask are
# fixed, and every timestamp comes from the release or the recipe, never
# from the clock.
#
# A recipe runs with `set -euo pipefail` before sourcing this file (it is
# refused otherwise), and after sourcing it:
#   - calls work_dir once, before any other helper that writes;
#   - exports SOURCE_DATE_EPOCH, the time of every file the helpers write
#     themselves (images, source tars, CRLF texts) and the date of a FAT
#     volume label. A value from the caller's environment is cleared on
#     sourcing, so the recipe's own is the only one (nix develop exports one
#     of its own).
#
# Helpers; each refusal prints its reason on stderr and exits 1:
#   require_tools TOOL...                       every TOOL is on PATH
#   sha256_of FILE                              print FILE's SHA-256
#   matches FILE SHA256                         FILE is a regular file (not a
#                                               link) with that hash
#   check_sha256 FILE SHA256 ROLE               refuse a mismatch; ROLE is
#                                               cached, member or output
#   work_dir [PARENT]                           fresh $WORK under PARENT
#                                               (default build/), removed on
#                                               exit
#   fetch DEST SHA256 MAX_BYTES URL...          a verified download, cached
#                                               at DEST
#   fetch_release_assets MANIFEST CACHE STAGE BASE_URL ASSETS
#                                               every output MANIFEST pins,
#                                               a release asset fetched from
#                                               BASE_URL and staged in STAGE
#                                               (in $WORK) unmodified
#   zip_expect_members ZIP MEMBER...            ZIP holds exactly these, in
#                                               this order
#   zip_extract ZIP MEMBER DIR SHA256           one listed member into DIR
#                                               (in $WORK), verified
#   fat_image OUT FORMAT SERIAL LABEL FILE...   deterministic FAT floppy at
#                                               OUT (in $WORK)
#   git_export [--fsck-warn ID]... REPO COMMIT DEST [PATH...]
#                                               a pinned commit's files into
#                                               DEST (in $WORK)
#   source_tar OUT NAME EPOCH STAGE [ALLOWED...]  deterministic tar of
#                                               STAGE/NAME at OUT (in $WORK)
#   crlf IN OUT                                 LF text to CRLF at OUT (in
#                                               $WORK)
#   outputs_match MANIFEST DIR                  the fast path: every pinned
#                                               output in DIR matches
#   check_outputs MANIFEST DIR                  refuse any missing or wrong
#                                               output in DIR
#   install_outputs MANIFEST STAGE OUT          check every pinned output in
#                                               STAGE (in $WORK), then move
#                                               them into OUT
#   stamp FILE...                               give FILE... the time
#                                               SOURCE_DATE_EPOCH
#
# Requires Bash 4 or later, GNU coreutils, findutils and tar, awk, the
# C.UTF-8 locale and, for the helpers that use them, mtools, Info-ZIP UnZip,
# curl, git and python3 (3.11 or later). Run recipes inside `nix develop`:
# the flake pins all of them on the nixpkgs revision unemupc98 uses, mtools
# 4.0.49 above all, whose output the pinned images depend on, and the CA
# roots curl verifies downloads against.
#
# Recipes use no here-document or here-string longer than 512 bytes: the
# devShell's bash 5.3 on macOS hangs on them (measured on aarch64-darwin).
# Pass long text through a file, a variable or process substitution.

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
	echo "lib/recipe.sh is sourced by a recipe, not run" >&2
	exit 1
fi
if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
	echo "bash $BASH_VERSION is too old for lib/recipe.sh: run the recipe inside nix develop" >&2
	exit 1
fi
# The helpers refuse explicitly, but a failure they do not foresee must stop
# the recipe too, also inside a pipeline.
if [[ ! -o errexit || ! -o nounset || ! -o pipefail ]]; then
	echo "run the recipe with set -euo pipefail before sourcing lib/recipe.sh" >&2
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
				mformat | mcopy | mdir) hint="nix develop provides mtools" ;;
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
#   timestamps. MTOOLSRC and the MTOOLS_* variables change mtools' layout
#   (MTOOLS_NFATS, MTOOLS_DIR_LEN) or dates (MTOOLS_DATE_STRING), and MCWD
#   names a state file; MTOOLS_SKIP_CHECK is set again, as the pinned images
#   of unemupc98 were made with it.
# - The locale is C.UTF-8: under the plain C locale mtools 4.0.49 adds a VFAT
#   long-name entry to every file and to the volume label, while any UTF-8
#   locale gives the plain 8.3 entries of the pinned images. The steps that
#   compare or map bytes (case mapping, find, tar's name order) run under
#   LC_ALL=C.
# - TZ=UTC0, a POSIX zone that needs no zoneinfo, makes unzip and mcopy
#   convert the archives' DOS dates and times identically on every host.
# - SOURCE_DATE_EPOCH is cleared: the recipe sets its own.
# - The umask fixes the modes of extracted and staged files.
# - git never waits for a password prompt.
unset TAR_OPTIONS UNZIP UNZIPOPT ZIPINFO ZIPINFOOPT MTOOLSRC MCWD SOURCE_DATE_EPOCH
# ${!MTOOLS_@} names every variable starting with MTOOLS_ (compgen is missing
# from non-interactive bash builds such as the devShell's).
for _recipe_variable in "${!MTOOLS_@}"; do
	unset "$_recipe_variable"
done
unset _recipe_variable
export MTOOLS_SKIP_CHECK=1
export LC_ALL=C.UTF-8 TZ=UTC0 GIT_TERMINAL_PROMPT=0
if [ "$(locale charmap 2>/dev/null)" != "UTF-8" ]; then
	refuse "the C.UTF-8 locale is not available on this host; mtools would write long-name entries"
fi
umask 022
# BSD tar (the macOS default) cannot fix member order, owners and modes.
case "$(tar --version 2>/dev/null)" in
	*"GNU tar"*) ;;
	*) refuse "missing GNU tar: run the recipe inside nix develop" ;;
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
		output) echo "Check the pinned inputs and the toolchain (nix develop, mtools 4.0.49, Info-ZIP UnZip 6.00, GNU tar); review the output deliberately before changing its pin in the manifest." >&2 ;;
	esac
	exit 1
}

# Byte-wise, so no locale can map a letter outside A-Z.
upper() {
	printf '%s' "$1" | LC_ALL=C tr '[:lower:]' '[:upper:]'
}

# A canonical DOS 8.3 name, as unemupc98 checks a guest name
# (machine-file pc_names::valid_dos_name): upper case, DOS characters only,
# no reserved device name.
dos_name_ok() {
	local chars="[A-Z0-9!#\$%&'()@^_\`{}~-]" stem="${1%%.*}"
	[[ "$1" =~ ^${chars}{1,8}(\.${chars}{1,3})?$ ]] || return 1
	case "$stem" in
		CON | NUL | AUX | PRN | 'CLOCK$' | COM[1-9] | LPT[1-9]) return 1 ;;
	esac
	return 0
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
# resolved path count: a symbolic link on the way (one a recipe staged, say)
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
		refuse "$1: export SOURCE_DATE_EPOCH in the recipe (the time of the files the helpers write)"
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
# EXIT trap removes it whatever stops the recipe (read-only copies are made
# writable first). Call it directly, not in $(...), so WORK reaches the
# recipe; it owns the EXIT trap, so other temporary files belong inside WORK.
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

# For a recipe whose outputs are a release's assets, unmodified (a firmware
# package's ROM and source archive): every output MANIFEST pins is fetched
# from BASE_URL/<name> with the manifest's pin into CACHE/<name> (fetch: a
# verified download, or a cached copy checked again) and copied into STAGE,
# inside WORK, with mode 0644. ASSETS lists the release's assets the recipe
# takes, one "NAME BYTES" per line: NAME a plain file name, BYTES the asset's
# size, the most its download may bring. The manifest must pin exactly the
# listed assets, each under its asset name, so its pins are the assets' only
# pins. Nothing fetched is ever run.
fetch_release_assets() {
	if [ "$#" -ne 5 ]; then
		refuse "usage: fetch_release_assets MANIFEST CACHE STAGE BASE_URL ASSETS"
	fi
	local manifest="$1" cache="$2" stage="$3" base="$4" assets="$5" name bytes pins expected staged=0
	local -A sizes=()
	inside_work "$stage" fetch_release_assets
	while read -r name bytes; do
		[[ "$name" =~ ^[A-Za-z0-9_][A-Za-z0-9._+-]*$ ]] \
			|| refuse "fetch_release_assets: '$name' is not a plain file name"
		[[ "$bytes" =~ ^[1-9][0-9]*$ ]] || refuse "fetch_release_assets: invalid size '$bytes' for $name"
		if [ -n "${sizes[$name]:-}" ]; then
			refuse "fetch_release_assets: $name is listed twice"
		fi
		sizes["$name"]="$bytes"
	done < <(printf '%s\n' "$assets")
	pins="$(manifest_pins "$manifest")" || exit 1
	mkdir -p -- "$stage" || refuse "fetch_release_assets: cannot create $stage"
	while read -r name expected; do
		if [ -z "${sizes[$name]:-}" ]; then
			refuse "$manifest pins $name, which is not an asset of the release this recipe downloads"
		fi
		fetch "$cache/$name" "$expected" "${sizes[$name]}" "$base/$name"
		cp -- "$cache/$name" "$stage/$name" || refuse "cannot stage $name"
		chmod 0644 -- "$stage/$name" || refuse "cannot set the mode of $stage/$name"
		staged=$((staged + 1))
	done < <(printf '%s\n' "$pins")
	if [ "$staged" -ne "${#sizes[@]}" ]; then
		refuse "$manifest pins $staged of the release's ${#sizes[@]} assets"
	fi
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
# restores the member's DOS date and time (under TZ=UTC0), which fat_image
# keeps. Nothing extracted is ever run; unzip reads past a self-extractor's
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

# mtools reads ~/.mtoolsrc through HOME even when MTOOLSRC is set (that only
# adds a further file), so mtools runs with an empty home directory. The
# host-wide files mtools 4.0.49 reads (the list below, from its binary) are
# refused in fat_image; the one in its own store path is part of the pinned
# package.
run_mtools() {
	local home="$1"
	shift
	HOME="$home" SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" "$@"
}
HOST_MTOOLS_CONFIG=(/etc/mtools.conf /etc/mtools /etc/default/mtools.conf /etc/default/mtools)

# Microsoft FAT32 File System Specification, Boot Sector and BPB, BS_jmpBoot:
# a valid jump lets DOS recognize the BPB. The project's own volume sector
# keeps mtools' boot code out of the image; its OEM name stays "UNEMUPC " so
# the images reproduce the ones unemupc98 pinned. IBM Personal Computer AT
# Technical Reference, BIOS listing, INT 18h: no bootable disk; halt if it
# returns. Drive number 00h (the first floppy) and the extended-BPB signature
# 29h, which enables the serial and label. mformat writes the BPB itself.
fat_boot_sector() {
	local boot="$1" failed="fat_image: cannot write the boot sector"
	dd if=/dev/zero of="$boot" bs=512 count=1 2>/dev/null || refuse "$failed"
	printf '\353\074\220' | dd of="$boot" bs=1 seek=0 conv=notrunc 2>/dev/null || refuse "$failed"
	printf 'UNEMUPC ' | dd of="$boot" bs=1 seek=3 conv=notrunc 2>/dev/null || refuse "$failed"
	printf '\000\000\051' | dd of="$boot" bs=1 seek=36 conv=notrunc 2>/dev/null || refuse "$failed"
	printf '\315\030\364\353\375' | dd of="$boot" bs=1 seek=62 conv=notrunc 2>/dev/null || refuse "$failed"
	printf '\125\252' | dd of="$boot" bs=1 seek=510 conv=notrunc 2>/dev/null || refuse "$failed"
}

# A fresh FAT floppy at OUT (inside WORK) holding FILE... in the root
# directory, in the order given, each under the upper case of its file name,
# which must be a DOS 8.3 name. FORMAT is the set's format: 1.44M, 1.2M,
# 720K or 360K. SERIAL is 0x and eight hex digits, LABEL the volume label.
# The steps are those of unemupc98's vendor/shareware/common.sh, so the same
# files give the same image: a zeroed image of the exact size, mformat with
# the fixed serial, label and boot sector, then mcopy -m, which keeps each
# file's modification time. The label's date comes from SOURCE_DATE_EPOCH;
# every file entry's comes from the file, as given: a member extracted from
# an archive carries the release's DOS date and time, and a file the helpers
# wrote carries SOURCE_DATE_EPOCH. A file the recipe makes another way must
# get its time from the recipe (stamp), never from the clock.
fat_image() {
	if [ "$#" -lt 5 ]; then
		refuse "usage: fat_image OUT FORMAT SERIAL LABEL FILE..."
	fi
	local out="$1" format="$2" serial="$3" label="$4" flag sectors file name names="" image home size conf
	local label_re="^[A-Z0-9!#\$%&'()@^_\`{}~-][A-Z0-9 !#\$%&'()@^_\`{}~-]{0,10}$"
	shift 4
	case "$format" in
		1.44M) flag=1440 sectors=2880 ;;
		1.2M) flag=1200 sectors=2400 ;;
		720K) flag=720 sectors=1440 ;;
		360K) flag=360 sectors=720 ;;
		*) refuse "fat_image: unknown format '$format' (expected 1.44M, 1.2M, 720K or 360K)" ;;
	esac
	[[ "$serial" =~ ^0x[0-9A-Fa-f]{8}$ ]] || refuse "fat_image: serial '$serial' is not 0x and eight hex digits"
	[[ "$label" =~ $label_re ]] || refuse "fat_image: invalid volume label '$label' (1 to 11 upper-case DOS characters)"
	need_epoch fat_image
	inside_work "$out" fat_image
	require_tools mformat mcopy dd
	for conf in "${HOST_MTOOLS_CONFIG[@]}"; do
		if [ -e "$conf" ] || [ -L "$conf" ]; then
			refuse "fat_image: the host has $conf, which mtools reads and which can change the image; build on a host without it"
		fi
	done
	for file in "$@"; do
		if [ -L "$file" ] || [ ! -f "$file" ]; then
			refuse "fat_image: $file is not a regular file"
		fi
		name="$(upper "$(basename -- "$file")")"
		dos_name_ok "$name" || refuse "fat_image: $name is not a DOS 8.3 name"
		case "$names" in
			*"/$name/"*) refuse "fat_image: two files are named $name" ;;
		esac
		names="$names/$name/"
	done

	fat_boot_sector "$WORK/fat-boot.bin"
	image="$(mktemp "$WORK/fat-image.XXXXXX")" || refuse "fat_image: cannot create a temporary image"
	home="$(mktemp -d "$WORK/home.XXXXXX")" || refuse "fat_image: cannot create an empty home"
	dd if=/dev/zero of="$image" bs=512 count="$sectors" 2>/dev/null || refuse "fat_image: cannot write $image"
	run_mtools "$home" mformat -i "$image" -f "$flag" -N "$serial" -v "$label" -B "$WORK/fat-boot.bin" :: \
		|| refuse "fat_image: mformat failed for $out"
	for file in "$@"; do
		run_mtools "$home" mcopy -m -i "$image" "$file" "::$(upper "$(basename -- "$file")")" \
			|| refuse "fat_image: mcopy failed for $file"
	done
	size="$(file_size "$image")"
	if [ "$size" -ne $((sectors * 512)) ]; then
		refuse "fat_image: unexpected image size $size bytes (expected $((sectors * 512)))"
	fi
	chmod 0644 "$image" || refuse "fat_image: cannot set the mode of $image"
	stamp "$image"
	mkdir -p -- "$(dirname -- "$out")" || refuse "fat_image: cannot create the directory of $out"
	mv -fT -- "$image" "$out" || refuse "fat_image: cannot move the image to $out"
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

# The fsck messages a recipe may relax to warnings for one export
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
# WORK; byte-wise (LC_ALL=C), and a failing find stops the recipe.
find_entries() {
	local list="$1" dir="$2"
	shift 2
	LC_ALL=C find "$dir" "$@" -print0 > "$list" || refuse "cannot list $dir"
}

# A deterministic tar of STAGE/NAME at OUT, both inside WORK: member order,
# timestamps (EPOCH, seconds since 1970), owners and modes are fixed so the
# archive has the same bytes on every host; ustar stores no atime or ctime.
# The archive file itself carries SOURCE_DATE_EPOCH. The stage holds only
# files the recipe copied or exported from explicit lists, and is checked
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
			refuse "source_tar: $name/$rel is a dot entry that is not on the recipe's allow list; stage files from explicit lists only"
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

# The pinned outputs of a manifest, "PATH SHA256" per line, or a refusal of
# an invalid manifest (which stops the recipe, never counts as a mismatch).
manifest_pins() {
	require_tools python3
	python3 -I "$ROOT/lib/manifest.py" pins "$1" || refuse "cannot read the pins of $1"
}

# The fast path. True when every output the manifest pins exists in DIR with
# its hash; a recipe then stops without touching the network. A cached output
# is re-checked byte for byte, never trusted for being there.
outputs_match() {
	if [ "$#" -ne 2 ]; then
		refuse "usage: outputs_match MANIFEST DIR"
	fi
	local manifest="$1" dir="$2" pins path expected
	pins="$(manifest_pins "$manifest")" || exit 1
	while read -r path expected; do
		matches "$dir/$path" "$expected" || return 1
	done < <(printf '%s\n' "$pins")
	return 0
}

# Every output the manifest pins must exist in DIR with its hash, or the
# recipe stops naming the first that does not.
check_outputs() {
	if [ "$#" -ne 2 ]; then
		refuse "usage: check_outputs MANIFEST DIR"
	fi
	local manifest="$1" dir="$2" pins path expected
	pins="$(manifest_pins "$manifest")" || exit 1
	while read -r path expected; do
		if [ -L "$dir/$path" ] || [ ! -f "$dir/$path" ]; then
			refuse "missing output $dir/$path (pinned in $manifest)"
		fi
		check_sha256 "$dir/$path" "$expected" output
	done < <(printf '%s\n' "$pins")
}

# The only way into out/: the recipe builds every output in STAGE, inside
# WORK, with the layout of OUT. Every pinned output is checked there first,
# and every destination too: no directory on the way from OUT to an output
# may be a symbolic link (mkdir -p and mv would follow it out of OUT), while
# a link at an output's own path is replaced, not followed (mv -T). Nothing
# reaches OUT unless all of them pass; then each moves into OUT, and OUT is
# checked again.
install_outputs() {
	if [ "$#" -ne 3 ]; then
		refuse "usage: install_outputs MANIFEST STAGE OUT"
	fi
	local manifest="$1" stage="$2" out="$3" pins path expected root parent directory
	inside_work "$stage" install_outputs
	pins="$(manifest_pins "$manifest")" || exit 1
	check_outputs "$manifest" "$stage"
	root="$(realpath -m -- "$out")" || refuse "install_outputs: cannot resolve $out (GNU realpath is needed)"
	while read -r path expected; do
		directory="$(dirname -- "$path")"
		parent="$(realpath -m -- "$(dirname -- "$out/$path")")" || refuse "install_outputs: cannot resolve $out/$path"
		if [ "$directory" = . ]; then
			directory="$root"
		else
			directory="$root/$directory"
		fi
		if [ "$parent" != "$directory" ]; then
			refuse "install_outputs: the way to $out/$path passes a symbolic link (to $parent)"
		fi
		if [ -d "$out/$path" ] && [ ! -L "$out/$path" ]; then
			refuse "install_outputs: $out/$path is a directory"
		fi
	done < <(printf '%s\n' "$pins")
	while read -r path expected; do
		mkdir -p -- "$(dirname -- "$out/$path")" || refuse "install_outputs: cannot create the directory of $out/$path"
		mv -fT -- "$stage/$path" "$out/$path" || refuse "install_outputs: cannot move $path into $out"
	done < <(printf '%s\n' "$pins")
	check_outputs "$manifest" "$out"
}
