{
  description = "software: reproducible builds of the open installation media sets and firmware packages of the unemu emulators";

  # The same nixpkgs revision as unemupc98's devShell and unemu-at-bios. The
  # recipes need none of it (upcli builds them); the FreeDOS source archive's
  # script does, and GNU tar, unzip and git here gave its pinned bytes.
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
