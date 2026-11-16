{
  description = "unemu-media: reproducible builds of the open installation media sets of the unemu emulators";

  # The same nixpkgs revision as unemupc98's devShell and unemu-at-bios, so the
  # tools that shape image bytes (mtools above all) match the emulator's fixtures.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/d407951447dcd00442e97087bf374aad70c04cea";

  outputs = { self, nixpkgs }:
    let
      systems = [ "aarch64-darwin" "x86_64-darwin" "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f (import nixpkgs { inherit system; }));
    in
    {
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            pkgs.curl.bin
            pkgs.unzip
            pkgs.mtools
            pkgs.gnutar
            pkgs.zstd
            pkgs.python3
            pkgs.git
            pkgs.shellcheck
          ];
          # Pinned CA roots for curl only; downloads are hash-checked anyway.
          CURL_CA_BUNDLE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
          NIX_SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
        };
      });
    };
}
