# SPDX-License-Identifier: MIT
{ pkgs, core }:
let
  payload = core.makeNativePayload { pkg = pkgs.hello; };
in
pkgs.runCommand "native-payload-contract" { } ''
  test -x ${payload}${pkgs.hello}/bin/hello
  test ! -e ${payload}/nix/var/nix/db/db.sqlite
  test ! -e ${payload}/usr/share/nix-profile.d
  test ! -e ${payload}/nix/var/nix/daemon-socket
  diff ${pkgs.hello}/bin/hello ${payload}${pkgs.hello}/bin/hello
  touch $out
''
