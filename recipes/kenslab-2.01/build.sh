#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Ken's Labyrinth 2.01, the full version (the Epic MegaGames registered
# version of 1993), which Ken Silverman released as freeware on 1999-11-16,
# for the set of media.toml: unemu/media/kenslab:2.01. The upstream release
# is an archive without disks, LABFULL.ZIP from the author's official Ken's
# Labyrinth page (https://advsys.net/ken/klab.htm); the set's one 1.44M
# diskette holds its files as released. The version is the one the game
# displays, V2.01 on its title art (the author's LAB21F.PNG, "from
# LABFULL.ZIP"); the author's page names the archive v2.1, but the program's
# own display wins.
#
# Outputs, in out/kenslab-2.01/:
#   labfull.img   a deterministic 1.44M FAT image holding all 19 files of
#                 LABFULL.ZIP, unmodified, under their own names, in the
#                 archive's order and with the archive's dates and times
#   README.TXT    the archive's README.TXT, unmodified: the author's freeware
#                 terms, the set's licence text
#   LICENSES.TXT  the licence index (licenses.txt, CC0-1.0) with CRLF line
#                 ends
#
# Input, pinned by SHA-256 and size and cached in cache/kenslab-2.01/:
#   - LABFULL.ZIP, from the author's page or its Wayback Machine captures of
#     2024-11-28 and 2013-10-26, which serve the same bytes. The author has
#     served these bytes since 2013 at the latest; the file list is the 1999
#     release's plus KENSBFIX.EXE, the author's 2008 fix for the 'Digitized
#     Sound' crash, and every file common to both is byte-identical. Its
#     member list is checked before anything is extracted, and every member
#     is checked against its pin below. The licence asks that no file of the
#     archive be removed: the disk holds every one.
#
# Nothing downloaded is ever executed. When every output already matches its
# pin in media.toml, the recipe stops at once, without the network.
#
# Run it inside nix develop: bash recipes/kenslab-2.01/build.sh
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source-path=SCRIPTDIR source=../../lib/recipe.sh
. "$HERE/../../lib/recipe.sh"

RECIPE=kenslab-2.01
MANIFEST="$HERE/media.toml"
CACHE="$ROOT/cache/$RECIPE"
OUT="$ROOT/out/$RECIPE"
# 2008-07-03 00:00 UTC, the date of the archive's latest change (the author's
# page dates LABFULL.ZIP 07/03/2008, when KENSBFIX.EXE was added): the date
# of the volume label and the time of the files the helpers write.
RELEASE_EPOCH=1215043200
# The volume serial and label of the disk, fixed by this recipe.
SERIAL=0x20080703
LABEL=KENSLAB

if outputs_match "$MANIFEST" "$OUT"; then
	echo "every output in $OUT matches its pin"
	exit 0
fi

require_tools curl unzip
export SOURCE_DATE_EPOCH="$RELEASE_EPOCH"
work_dir
STAGE="$WORK/out"
FILES="$WORK/files"

# --- the archive ---------------------------------------------------------------

ARCHIVE_SHA256="643aa0ca8d1a7d2c88afecd952da00f3b912be8b83c4c1d2f4ab9b50491fadfd"
ARCHIVE_BYTES=940242
fetch "$CACHE/labfull.zip" "$ARCHIVE_SHA256" "$ARCHIVE_BYTES" \
	https://advsys.net/ken/klab/labfull.zip \
	https://web.archive.org/web/20241128150708id_/https://advsys.net/ken/klab/labfull.zip \
	https://web.archive.org/web/20131026024809id_/http://advsys.net/ken/klab/labfull.zip

# Every member of the archive, in its order, with its SHA-256. Anything else
# refuses the archive.
MEMBERS="BOARDS.KZP 4cf55fc511b52ac063e1f717440ff98fedb85ccf5912e96051bf449b4a6c9be2
HELPME.DOC 8e8b5f5deee7a85750eda7e91a3917da74075d75850d567a673e0f16d4a1141a
HINTS.TXT 7451c1bb0aa8a35b3abb3c4152c088e8a39f893e722327cb61a66310f02736d1
HISCORE.DAT 44183a493a7c198644c1f206c63c49cdb3ae928fb5d67505fb90492c37b77005
INSTS.DAT 5aa606bbaede7b245c1f8856d19ac32c8e91d168a2c121963b8249011ca4552d
KEN.EXE 6d33f1502c80cdf2307b86ae77662f478f3c537367631043489442d373bb75c1
KENSBFIX.EXE 22a0c4ca5bd86dc85292aaaa90e392bd4f960d10177a5945c42aeb88a253d8a8
LAB3D.KZP c42bdfc754eea7ae24115780904f618f8473ab85c99df7d6d62f40482c89d498
README.TXT b4a4fbf1f9ef0601bde0169fd95b649bf4d41415e5db41b6a24f5a28dda69b39
SAVGAME4.DAT 279409cbb8b4a444e45a05d657b177228b62a4ab5dd87c6b63f910cdd7edb381
SAVGAME5.DAT 667cc46206963f8801d5b176d2d57fda920206e7bb49a4878c69cebc8996b06e
SAVGAME6.DAT 80a28f9c053b8678614be6bc8424e7b1be97963f67c5528818451eee3d37302c
SAVGAME7.DAT 741f5ae211309aa75e25417cd96a59aac99924677c4a1d9691d1f0cbd7036913
SETUP.EXE c41dd777ea81ac9b64520a4e9435441126928e7391334318859c5b5a9816212b
SONGS.KZP 92e7c69baee46899960e18036d1985488443e88f351d8916cbf872529d36a273
SOUNDS.KZP f371359c8a325ec9e6800a3693f258e63928ef45fa5c86f90fbc5a97e045d217
STORY.KZP 3d437db09c2dfd6db830baddbfc432e7f55c2a193c242e2ab2ae579c982f470b
TABLES.DAT 0627fdf1cfce4173c08a905a63cc8ba7a65831a5ce64042d9d9427e2b505e5bc
WALLS.KZP 7643f5f26ed9906f1ede6b6e54c18265debeffb4e8c9140fbf8c51fb0af4d42d"

names=()
while read -r member _; do
	names+=("$member")
done < <(printf '%s\n' "$MEMBERS")
zip_expect_members "$CACHE/labfull.zip" "${names[@]}"

# Each member into a fresh directory, checked against its pin; unzip gives
# it the archive's date and time (TZ=UTC0), which mcopy -m keeps.
files=()
while read -r member expected; do
	zip_extract "$CACHE/labfull.zip" "$member" "$FILES" "$expected"
	files+=("$FILES/$member")
done < <(printf '%s\n' "$MEMBERS")

# --- the disk -------------------------------------------------------------------

# The 19 files hold 1,315,635 bytes, which fit one 1.44M diskette.
fat_image "$STAGE/labfull.img" 1.44M "$SERIAL" "$LABEL" "${files[@]}"

# --- the licence text and the licence index -------------------------------------

# README.TXT, as released (CRLF already), is the set's licence text.
cp -- "$FILES/README.TXT" "$STAGE/README.TXT" || refuse "cannot stage README.TXT"
chmod 0644 -- "$STAGE/README.TXT" || refuse "cannot set the mode of README.TXT"
stamp "$STAGE/README.TXT"
# DOS readers expect CRLF.
crlf "$HERE/licenses.txt" "$STAGE/LICENSES.TXT"

install_outputs "$MANIFEST" "$STAGE" "$OUT"
echo "wrote $OUT: labfull.img, README.TXT and LICENSES.TXT"
