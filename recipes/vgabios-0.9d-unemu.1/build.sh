#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# The unemu VGA BIOS, release v0.9d-unemu.1 of github.com/unemu-org/vgabios
# (the unemu fork of the LGPL VGABIOS 0.9d), standard VGA build, for the
# firmware package of firmware.toml: unemu/firmware/vgabios-0.9d-unemu.1-std.
# The fork's own release workflow builds the ROM and its source archive with
# the fork's Nix flake and attaches both to the release; this recipe
# downloads those two assets, and no other, and installs them unmodified. It
# builds nothing from source (bd memory 'firmware-rules', rule (a)).
#
# Outputs, in out/vgabios-0.9d-unemu.1/, byte-identical to the release assets:
#   vgabios-std.bin                the standard VGA build, a 32 KiB ROM: the
#                                  bytes unemupc's firmware catalogue pins
#                                  for this id
#   vgabios-0.9d-unemu.1-src.tar   its corresponding source, the archive the
#                                  catalogue pins
#
# Every asset's SHA-256 is pinned in firmware.toml, the only place it is
# written: the outputs are the downloads, unmodified. The sizes below are the
# assets' exact sizes, the most a download may bring. Downloads are cached in
# cache/vgabios-0.9d-unemu.1/, checked before use and never executed. When
# every output already matches its pin, the recipe stops at once, without the
# network.
#
# Run it inside nix develop: bash recipes/vgabios-0.9d-unemu.1/build.sh
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# shellcheck source-path=SCRIPTDIR source=../../lib/recipe.sh
. "$HERE/../../lib/recipe.sh"

RECIPE=vgabios-0.9d-unemu.1
MANIFEST="$HERE/firmware.toml"
CACHE="$ROOT/cache/$RECIPE"
OUT="$ROOT/out/$RECIPE"
RELEASE="https://github.com/unemu-org/vgabios/releases/download/v0.9d-unemu.1"
# Every asset the package takes from the release, with its size in bytes.
ASSETS="vgabios-std.bin 32768
vgabios-0.9d-unemu.1-src.tar 604160"

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
