# SPDX-License-Identifier: MIT
{ pkgs, core }:
let
  args = {
    pkg = pkgs.hello;
    lifecycle.onInstall = "echo initialized";
    configFiles."/etc/impurifier-test.conf" = pkgs.writeText "config" "original\n";
  };
  application = core.makePayload (args // { mode = "application"; });
  integration = core.makePayload (args // { mode = "integration"; });
  full = core.makePayload args;
  runtime = core.makeRuntimeTree {};
in
assert application.configPaths == [ ];
assert integration.configPaths == [ "/etc/impurifier-test.conf" ];
pkgs.runCommand "impurifier-payload-partition-contract" { } ''
  test -f ${application}/usr/share/nix-profile.d/hello.json
  test ! -e ${application}/etc/impurifier-test.conf
  test -x ${integration}/usr/lib/nix-impurifier/lifecycle/hello/onInstall
  test ! -e ${application}/usr/lib/nix-impurifier/lifecycle/hello/onInstall
  test -f ${integration}/etc/impurifier-test.conf
  test ! -e ${integration}/usr/share/nix-profile.d/hello.json
  test -f ${full}/etc/impurifier-test.conf
  test -f ${full}/usr/share/nix-profile.d/hello.json
  grep -x "MIT License" ${runtime}/usr/share/licenses/nix-impurifier/LICENSE
  touch $out
''
