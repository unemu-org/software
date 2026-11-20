#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The corresponding source archive of the FreeDOS 1.4 Floppy Edition sets,
# freedos-1.4-floppy-src.tar: the official source packages of every program
# on the edition's disks, the installer and its translations, the release
# build environment and LICENSES.TXT. The three recipes beside this folder
# (freedos-1.4*.media.toml) take it as a prepared input, by size and SHA-256
# ([input.source]), from its release asset
# https://github.com/unemu-org/unemu-media/releases/download/freedos-1.4-floppy/freedos-1.4-floppy-src.tar;
# upcli's recipe steps cannot make it (it needs an ISO reader, git and a
# tar writer). This script is the one way to make it again and to check that
# the asset is what it should be: it builds the archive from pinned upstream
# inputs and stops unless its SHA-256 and size are the pins below, the
# recipes' pins (tests/recipes_test.py keeps them equal). Ported from
# unemupc98's vendor/freedos/fetch-floppy-1.4.sh (upc-5pw.68 (unemupc98)).
#
# Output: out/freedos-1.4-floppy-src/freedos-1.4-floppy-src.tar
#
# Inputs, every one pinned by SHA-256 and cached in
# cache/freedos-1.4-floppy-src/:
#   - The release's packages, with their SOURCE/ trees, exactly as the
#     LiveCD carries them (the online repository has moved on since the
#     release): FD14-LiveCD.zip (SHA-256 listed in the release's verify.txt)
#     is fetched only while a package is missing from the cache, its
#     FD14LIVE.iso is read by iso-extract.py, a small ISO 9660 reader that
#     copies only the listed packages, and the LiveCD zip leaves the cache
#     once every package is cached. FDINST is part of fdnpkg; FDNET's
#     DHCP.EXE is mTCP's, so mTCP's package comes too.
#   - util/fdbanner.zip from the FreeDOS 1.4 repository: FDBANNER, on the
#     boot disk only, is not on the LiveCD; this package holds the same
#     FDBANNER.COM and its source.
#   - The installer (FDI-x86, FDI), its translations and fonts (fd-nls) and
#     the release build environment (builder), at the FreeDOS-v1.4 commits
#     their projects tagged for this release, exported afresh on every run
#     that builds (git_export).
#   - ../licenses.txt, the sets' licence index, with CRLF line ends.
#
# Nothing downloaded is ever executed. When the output already matches its
# pin, the script stops at once, without the network.
#
# Run it inside nix develop: bash recipes/media/freedos/source/build.sh
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source-path=SCRIPTDIR source=../../../../lib/source.sh
. "$HERE/../../../../lib/source.sh"

SOURCE_NAME=freedos-1.4-floppy-src
# The archive's pins: equal to [input.source] of the three recipes.
SOURCE_SHA256="dfa7006e742cb9676a0a6401ce64214089923f055f4a7ba8f36960115b5362b6"
SOURCE_BYTES=22835200
CACHE="$ROOT/cache/$SOURCE_NAME"
OUT="$ROOT/out/$SOURCE_NAME"
# The release date, 2025-04-02 00:00 UTC: the time of every member of the
# archive and of every file the helpers write.
RELEASE_EPOCH=1743552000

if matches "$OUT/$SOURCE_NAME.tar" "$SOURCE_SHA256"; then
	echo "$OUT/$SOURCE_NAME.tar matches its pin"
	exit 0
fi

require_tools curl unzip git python3
export SOURCE_DATE_EPOCH="$RELEASE_EPOCH"
work_dir
STAGE="$WORK/out"
SOURCE_STAGE="$WORK/source"
SOURCE="$SOURCE_STAGE/$SOURCE_NAME"

# The release directory and its mirror on ibiblio, where FreeDOS keeps every
# distribution. Both serve the same bytes; the hashes decide.
PRIMARY="https://download.freedos.org/1.4"
MIRROR="https://www.ibiblio.org/pub/micro/pc-stuff/freedos/files/distributions/1.4"
REPOSITORY="https://www.ibiblio.org/pub/micro/pc-stuff/freedos/files/repositories/1.4"

# One file of the release directory into the cache: NAME SHA256 BYTES, BYTES
# being the pinned file's exact size.
release_file() {
	fetch "$CACHE/$1" "$2" "$3" "$PRIMARY/$1" "$MIRROR/$1"
}

# --- the release's packages ----------------------------------------------------

LIVECD_SHA256="2020ff6bb681967fd6eff8f51ad2e5cd5ab4421165948cef4246e4f7fcaf6339"
LIVECD_BYTES=293950337
LIVECD_MEMBERS=(FD14BOOT.img FD14LIVE.iso readme.txt)
LIVECD_ISO_SHA256="c48a9dcf4b8e22f44e268a9879745f0bd88c061195ac584e6ef2deb0477f81fb"
# Every package on the disks, by its group directory on the LiveCD, with the
# SHA-256 of its zip.
PACKAGES="apps/fdimples 3259e011cad448a20863c1ebdab2fb1d509b829765efe0d1ad5bc2cac3857fcf
archiver/gzip a21e7ffa045ee5c19be7e49757bfe03828295a1a3ab67bf54f2bf7a2182de8c5
archiver/slicer 48598beb09e72548710b68e4699b51c16694a90801764328f12b5cb1cb2962a2
archiver/unzip ce22cde8ee2185f7e9312a61413ce2fe7879df9aebbf1cf04f0c4181456e4c17
archiver/zip e139f09786aa77678d06c6a266e324bd74bd0a82ee4ebd95da85c5c2576dc4dc
base/append 5bbd29b12309cc6966796f90a2580714ea902eb19e744a7d2905bb5b2a9144c6
base/assign 6e79e0fc10d18d5153f7c42b5cbbbfe0c64575c0b79f398dd66b6383b07beb49
base/attrib b5a77bd0c20a821bf0085dfed7cff408a8dd72603506c0abda1e2c54c6303964
base/chkdsk 42a7120307e6604147ee663b3cba2b346bdb1305961eed0357ec6130cd23ba4e
base/choice fae8902974b165dbaaa47c87ad171d0a073f94f0610a71ebec0c5f808a68079b
base/comp 3687e25eb78f1164a66e4f7328bc37952c402fba3ba888df64ad59f323114d96
base/cpidos 3a3daed5de2daf7ed39d0f3f8bb8b96ff23a79e2cda5622a1d8796d197261e7c
base/ctmouse dd378ddfe5b6948d3073c12acaf0ca31bf60d0898f1cd708b6858083a14a41c7
base/debug 0ceabfacdad7066d159419bedd2a5299d583fb04ba0540d8a89df3e5563a09f7
base/defrag a2af8b47652616da9a33aee6e61193fdff729616120f594067a4c6faf95763de
base/deltree 6f603150a3d9004314dcfaba6c14f6c80e21dbfd2ef88aef602162bb2a4def3d
base/devload 8ed0fcd7ac0d66d9f6b351c5b5cf4c69026f60033bcb8b2552c3ae23c95c99d9
base/diskcomp e4f266f1e4e1d4dd8b90530ed9f911fcddb131d6d6008a4b524402561a56ac78
base/diskcopy 23eb5cbce5aa4ad767516d2ce259b76cfe3c82818783d5f7bb84745847fbc558
base/display f05a0130309c2e52620aa14f04e9c499c4f8c04a1e58266e9fa9baa873ee024e
base/edit d794ffb6c2a2d04f96a6354ac0eded9c05f5592e7e856c3c28b48e4ba1cb030e
base/edlin f8729654f985160867d0af99790515366b9067af6c83ff64e0d830533da92b5a
base/exe2bin a2e3cb2b29abf32a92b5a7fbe65f508b8f30df41141ac9eeb75c19b1bcc003b8
base/fc 5e1811bea9d60ffb34cef8e5851bc907cd57b130a37812480f5e759bcd94147d
base/fdapm bdda9abb631447508a659f1aec970079fe72b60a01c4e194255ffb34bfe77288
base/fdhelper 5180fa61c70ccf9084504100728d79e1aeb6d0103d4787b9e8f1cedb5e1e12ec
base/fdisk 1f705c34586e3468769e0baf59342891ac4c2329bbe87a140fcb4902381ab10d
base/fdxms 5fc8a472944211cfd6b456bc9753a061d6c969aaa16eda9541f983a19594d500
base/fdxms286 5586614769c542f75254a1caa562643089a17f6501e2507bfa8f488918fa8779
base/find 71115327e2a05fe723f5a22ef2505067f9176ddcc714a1e2856b8fafb97afe1b
base/format 80a7f0616fbe07ceb74c074bab2e79da880e6dd24cc588a43463a7182a312eae
base/freecom 2529cf15c2ee7d7030ed99a6a88df2cf5eef87b9fe10f1c0fb643c38ea6aaa8e
base/graphics c9a305e20cf78d684d4e6d30caa5cbe290296edc92917257119aa58be5b9209c
base/himemx 5be13efe2c86e784c546c2a79d7f1811d512d90d99a779c9ea204b4f4829615b
base/htmlhelp e420ca8223bfaf8ae2e64a8f17d582b0ee3f9c32b361bb8b25065b7a95735a3d
base/jemm c571724c2025cc493ba3b194ec68d29a2a9913a5e9b844828d3cdb4c21548994
base/kernel 38ce3c63e399c8f18ab6230d8988a5d1a1aa9be4e109d15c6f4842b5e8fe61e6
base/keyb 9fd172421cfd6f30bf45cd9e46e45c78e40439209104c4e81498e327904e759e
base/keyb_lay c20e750bd97eb85a1210500ab7f756f38a2070359eb53791a6a5979070f30bf6
base/label 40713091ecc0de5d3f053876e6b6e61ba5cd22e4276ad1dd7581108d5d136d90
base/lbacache b688c237ed99f71b0f23ded2abe2f63278456461e5bc9c6e3e42e465481b7a17
base/mem 05608dc39dad9e89f4b783aae710b2ebeedbf0ffa5453d45d9484445fd752799
base/mirror 728f2fb4410c3029ecfe374817bc1961589b53ddafd5aeb53259bc455dd22421
base/mkeyb 872b427c818b9fe286c0019aa49c3733c53aabeccb30f88cdc8e3f1eae6d139b
base/mode b4df1f5616afab1a7bf8774a1331914677fa7bd2ac3fd8d9dcde461c1601c492
base/more 6c2861a0a4fdbbca726e677cb45cc7761d4ba7f6926c3e7c6903e03723ef5869
base/move 0aeca7a82f1ea367e779ff736526316bf11c5cc7374e94c5de49bd72983e25d4
base/nansi be9e1179efe1ce5c4473d0a6f7d917566fdb7f8d60b63a846521ce32d14b2cf6
base/nlsfunc e8abc552205408422008d3c9b775e87e945f09f2717779d0a62d84ede59061aa
base/print ab10f50918c6936517044d0afd3cee0ba1a2676c0ffe0c6658a25a7f32c046db
base/recover 9c980817629463d865cee65f0fb22a9ac575da4049ec224ed026d428c4e09578
base/replace 3eb27db2fa1da396502f367aa4a35bcb54b6d5dabb341b5895fd20a4db365f5e
base/share be39883e7dda38f9cafcb9fc2bf37647d8adea1605838417bbea3e9efd979fc7
base/shsucdx 411eacf4f4b64cace0031462004f6018352de5de3509698734a2cb0f967de05d
base/sort 3f99a877e86a37a627bfa95906e9774447b0eb43a7174992723f37c0eea40aaf
base/swsubst aaa196da6a88ee8e2aa4de0ed7696d4ebc6f209eb5fcc9b9b8f035f8c26f9192
base/tree bb56442d17ec06b75ad15d99ca77dad879f9ad06539f3a3d9653902d8ba09da5
base/undelete 9e02ad4b204c832d4a142036601757ae741689c3de5a919c2070fccf1c43b80f
base/unformat 46fefe1b3c37c1b4ce84e300be06cc7e25035c1578ea5c5fc836b7e1c04c5f22
base/xcopy aa8ab3742dd0dd1b31ff991b3ed9bdf65f4f11f0da2c6a22b321c3d1e58f995b
drivers/shsufdrv adb35e7f33683f787b8f1da32e964c75195dbf74d088c5c2197d1a7738233a94
drivers/udvd2 c90d6611bb32e3dd3b7362db2b0a680665c6efcb9d0490dd2bbb8cb187fbe3a7
net/fdnet fa4d04aab2977c23845d5db779af068a2d2dde6789e0b4fdf34cd042ec8b5d2f
net/fdnpkg 38aeb7f2154cf4a159d7249e1704316922efedb112479dcab39fb85594a548a1
net/mtcp 79439cba08ae1e44339c0c97a9247494dbe6173d3c5b3397dc68b6354dbf4827
tools/callver 327d2a0ade12911cbdf4a42e9d700789e6847dfbe9732b5b57ba2ea6a5d86ea3
tools/cwsdpmi 4c0d6aee9b14609f2bb9b620d2245ec50c5c866a018860beb7e99d03ccad2219
tools/pkgtools 1ae990094f184fa1726642d6d578e2e68423ed896e75bb68e452563b5a89b335
tools/v8power f1378905558e07c9379bd0461043d869880440cc7a20429ce4472e12d36ac7e0"
FDBANNER_SHA256="51d4ae13a3e1bb06eb05cc8b3e9ce8406349eebe806fd68fd9397cf10e2591c2"
FDBANNER_BYTES=6652

# No directory between $CACHE and a cached package may be a symbolic link:
# mkdir -p and mv would follow it and write a verified package outside the
# cache. $CACHE itself may be one, as out/<name> may for install_output;
# a link at the package's own path is refused where it is read.
cache_path_ok() {
	local member="$1" root parent directory
	root="$(realpath -m -- "$CACHE")" || refuse "cannot resolve $CACHE (GNU realpath is needed)"
	parent="$(realpath -m -- "$(dirname -- "$CACHE/$member")")" || refuse "cannot resolve $CACHE/$member"
	directory="$(dirname -- "$member")"
	if [ "$directory" != . ]; then
		root="$root/$directory"
	fi
	if [ "$parent" != "$root" ]; then
		refuse "the way to $CACHE/$member passes a symbolic link (to $parent); remove it and run again"
	fi
}

# A package zip is cached under its LiveCD path. fetch cannot place a file
# that comes out of another archive, so this step does what fetch does for a
# download: a cached copy is re-checked and refused, never replaced, when it
# differs; a missing one is listed for the LiveCD.
declare -A PACKAGE_SHA256=()
missing=()
while read -r package expected; do
	PACKAGE_SHA256["packages/$package.zip"]="$expected"
	cache_path_ok "packages/$package.zip"
	cached="$CACHE/packages/$package.zip"
	if [ -e "$cached" ] || [ -L "$cached" ]; then
		if [ -L "$cached" ] || [ ! -f "$cached" ]; then
			refuse "cached $cached is not a regular file; remove it and run again"
		fi
		check_sha256 "$cached" "$expected" cached
	else
		missing+=("packages/$package.zip")
	fi
done < <(printf '%s\n' "$PACKAGES")

# The LiveCD is needed only while a package is missing. Its ISO lands in WORK
# and leaves it as soon as the packages are out; each package is checked in
# WORK and only then enters the cache, as a verified download does.
if [ "${#missing[@]}" -gt 0 ]; then
	release_file FD14-LiveCD.zip "$LIVECD_SHA256" "$LIVECD_BYTES"
	zip_expect_members "$CACHE/FD14-LiveCD.zip" "${LIVECD_MEMBERS[@]}"
	zip_extract "$CACHE/FD14-LiveCD.zip" FD14LIVE.iso "$WORK/livecd" "$LIVECD_ISO_SHA256"
	mkdir -- "$WORK/iso" || refuse "cannot create $WORK/iso"
	python3 -I "$HERE/iso-extract.py" "$WORK/livecd/FD14LIVE.iso" "$WORK/iso" "${missing[@]}" \
		|| refuse "cannot copy the packages out of FD14LIVE.iso"
	rm -f -- "$WORK/livecd/FD14LIVE.iso"
	for member in "${missing[@]}"; do
		extracted="$WORK/iso/$member"
		if [ -L "$extracted" ] || [ ! -f "$extracted" ]; then
			refuse "$member from FD14LIVE.iso is not a regular file"
		fi
		check_sha256 "$extracted" "${PACKAGE_SHA256[$member]}" member
		mkdir -p -- "$(dirname -- "$CACHE/$member")" || refuse "cannot create the cache directory of $member"
		mv -fT -- "$extracted" "$CACHE/$member" || refuse "cannot move $member into $CACHE"
	done
fi
# Every package is cached and verified now; the LiveCD (about 300 MB) is not
# kept.
rm -f -- "$CACHE/FD14-LiveCD.zip"

# fetch would follow a linked directory too.
cache_path_ok packages/util/fdbanner.zip
fetch "$CACHE/packages/util/fdbanner.zip" "$FDBANNER_SHA256" "$FDBANNER_BYTES" \
	"$REPOSITORY/util/fdbanner.zip"

# --- the licence index -----------------------------------------------------------

# The sets' terms text, as the recipes' text step makes it: DOS readers
# expect CRLF.
crlf "$HERE/../licenses.txt" "$STAGE/LICENSES.TXT"

# --- the source archive --------------------------------------------------------

# The archive keeps one bit of each file's mode, execute (source_tar fixes
# the rest), so every file copied into the stage gets mode 0644 whatever the
# cache's modes are: a cache restored from exFAT or NTFS, say, marks every
# file executable.
stage_file() {
	cp -- "$1" "$2" || refuse "cannot stage $1"
	chmod 0644 -- "$2" || refuse "cannot set the mode of $2"
}

mkdir -p -- "$SOURCE" || refuse "cannot create $SOURCE"
stage_file "$STAGE/LICENSES.TXT" "$SOURCE/LICENSES.TXT"
# Exactly the pinned packages, never whatever else the cache holds, each
# checked again as staged.
while read -r package expected; do
	mkdir -p -- "$(dirname -- "$SOURCE/packages/$package.zip")" || refuse "cannot stage $package"
	stage_file "$CACHE/packages/$package.zip" "$SOURCE/packages/$package.zip"
	check_sha256 "$SOURCE/packages/$package.zip" "$expected" cached
done < <(printf '%s\n' "$PACKAGES" "util/fdbanner $FDBANNER_SHA256")

# The installer, its translations and the release build environment, at the
# FreeDOS-v1.4 tags their projects set for this release. Of fd-nls only the
# translations and fonts the Floppy Edition installer uses; the rest of it
# translates programs whose packages carry their own NLS files.
git_export https://github.com/shidel/FDI-x86 2ba66516bce2703a1b33e2c2190c7cebf339089f \
	"$SOURCE/installer/FDI-x86"
git_export https://github.com/shidel/FDI 23979ab50c9418052a84155795aa378b88bb73ab \
	"$SOURCE/installer/FDI"
git_export https://github.com/shidel/fd-nls 079e0a38eef71d39559768f263ecd025257409da \
	"$SOURCE/installer/fd-nls" LICENSE README.md fdi/language fdi-x86
git_export https://gitlab.com/FreeDOS/OS/builder.git 08f1920798d01276aca2576c74db7c1dcfd2870e \
	"$SOURCE/builder"

# The git exports give 0644 or 0755 as their trees say (git archive,
# extracted under the library's umask 022); the staged copies are 0644. Any
# other mode means a file system that keeps no POSIX modes (exFAT, NTFS, a
# WSL /mnt/c drive), whose execute bits would reach the archive.
odd_modes="$(
	LC_ALL=C find "$SOURCE" -type f ! -perm 0644 ! -perm 0755 -print \
		&& LC_ALL=C find "$SOURCE/LICENSES.TXT" "$SOURCE/packages" -type f ! -perm 0644 -print
)" || refuse "cannot list the modes of $SOURCE"
if [ -n "$odd_modes" ]; then
	refuse "unexpected file modes in the source stage (does the file system under $WORK keep POSIX modes?):
$odd_modes"
fi

# The three .timestamps files are part of the pinned trees: each project's
# file timestamp preservation file (its fdvcs.sh restores the files' times
# from it). No other dot entry may reach the archive.
source_tar "$STAGE/$SOURCE_NAME.tar" "$SOURCE_NAME" "$RELEASE_EPOCH" "$SOURCE_STAGE" \
	builder/.timestamps installer/FDI/.timestamps installer/FDI-x86/.timestamps

# Only the pinned archive leaves the work directory.
check_sha256 "$STAGE/$SOURCE_NAME.tar" "$SOURCE_SHA256" output
size="$(file_size "$STAGE/$SOURCE_NAME.tar")"
if [ "$size" != "$SOURCE_BYTES" ]; then
	refuse "$SOURCE_NAME.tar has $size bytes, pinned $SOURCE_BYTES"
fi
install_output "$STAGE/$SOURCE_NAME.tar" "$OUT"
echo "wrote $OUT/$SOURCE_NAME.tar, as pinned"
