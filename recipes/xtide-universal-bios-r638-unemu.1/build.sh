#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The XTIDE Universal BIOS r638 XT+ build configured for 16-bit ATA at 1F0h,
# release r638-unemu.1 of github.com/unemu-org/xtide-universal-bios, for the
# firmware package of firmware.toml:
# unemu/firmware/ibm-pc/xub-r638-xtp-ata1f0:r1. The fork's own release
# workflow builds r638 with the fork's Nix flake, configures, pads and
# checksums the ROM, and attaches it to the release with
# the r638 source archive; this recipe downloads those two assets, and no
# other, and installs them unmodified. It builds and transforms nothing (bd
# memory 'firmware-rules', rule (a); the transformation exception, rule (b),
# is not used).
#
# Outputs, in out/xtide-universal-bios-r638-unemu.1/, byte-identical to the
# release assets:
#   ide_xtp-r638-ata1f0-8k.bin         the configured 8 KiB ROM: the bytes
#                                      unemupc's firmware catalogue pins for
#                                      this id
#   xtide-universal-bios-r638-src.tar  its corresponding source, the r638
#                                      Subversion trunk; the archive the
#                                      catalogue pins
#
# Every asset's SHA-256 is pinned in firmware.toml, the only place it is
# written: the outputs are the downloads, unmodified. The sizes below are the
# assets' exact sizes, the most a download may bring. Downloads are cached in
# cache/xtide-universal-bios-r638-unemu.1/, checked before use and never
# executed. When every output already matches its pin, the recipe stops at
# once, without the network.
#
# Run it inside nix develop:
#   bash recipes/xtide-universal-bios-r638-unemu.1/build.sh
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source-path=SCRIPTDIR source=../../lib/recipe.sh
. "$HERE/../../lib/recipe.sh"

RECIPE=xtide-universal-bios-r638-unemu.1
MANIFEST="$HERE/firmware.toml"
CACHE="$ROOT/cache/$RECIPE"
OUT="$ROOT/out/$RECIPE"
RELEASE="https://github.com/unemu-org/xtide-universal-bios/releases/download/r638-unemu.1"
# Every asset the package takes from the release, with its size in bytes.
ASSETS="ide_xtp-r638-ata1f0-8k.bin 8192
xtide-universal-bios-r638-src.tar 2201600"

if outputs_match "$MANIFEST" "$OUT"; then
	echo "every output in $OUT matches its pin"
	exit 0
fi

require_tools curl
work_dir
STAGE="$WORK/out"
# The manifest pins exactly the assets above, each under its asset name; each
# is fetched with the manifest's pin and staged as it came.
fetch_release_assets "$MANIFEST" "$CACHE" "$STAGE" "$RELEASE" "$ASSETS"
install_outputs "$MANIFEST" "$STAGE" "$OUT"
echo "wrote $OUT: the ROM and its source archive, as released"
