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
  runtime = core.makeRuntimeTree { };
  cache = core.offlineCache pkgs.hello;
  runscript = pkgs.writeText "run" "#!/bin/sh\nexec /opt/nix/profile/bin/hello\n";
  runitPayload = core.makePayload {
    pkg = pkgs.hello;
    runit.run = runscript;
  };
  unsupportedRunit = builtins.tryEval (
    (core.makePayload {
      pkg = pkgs.hello;
      runit = {
        run = runscript;
        enable = false;
      };
    }).drvPath
  );
in
assert !unsupportedRunit.success;
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
  test -x ${runitPayload}/etc/sv/hello/run
  grep -x "MIT License" ${runtime}/usr/share/licenses/nix-impurifier/LICENSE
  test -d ${cache}/realisations
  test -d ${cache}/log
  test -f ${cache}/nix-cache-info
  touch $out
''
