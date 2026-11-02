# SPDX-License-Identifier: GPL-3.0-or-later
{
  pkgs,
  name,
  users,
  directories,
  systemd,
}:
let
  inherit (pkgs) lib;
  safeName = value: builtins.match "[a-z_][a-z0-9_-]*" value != null;
  safePath =
    value:
    builtins.match "/[A-Za-z0-9/_.+-]+" value != null
    && lib.all (part: part != "." && part != "..") (lib.splitString "/" value);
  userLines = lib.concatStringsSep "\n" (
    lib.mapAttrsToList (
      user:
      {
        group ? user,
        description ? user,
        home ? "/var/lib/${user}",
      }:
      assert lib.assertMsg (
        safeName user && safeName group && safePath home && !(lib.hasInfix "\n" description)
      ) "invalid system user declaration";
      ''
        g ${group} -
            u ${user} -:${group} ${builtins.toJSON description} ${home} /usr/sbin/nologin''
    ) users
  );
  directoryLines = lib.concatStringsSep "\n" (
    lib.mapAttrsToList (
      path:
      {
        owner ? "root:root",
        mode ? "0755",
      }:
      let
        parts = lib.splitString ":" owner;
      in
      assert lib.assertMsg (
        safePath path
        && builtins.length parts == 2
        && lib.all safeName parts
        && builtins.match "0[0-7]{3}" mode != null
      ) "invalid directory declaration";
      "d ${path} ${mode} ${builtins.elemAt parts 0} ${builtins.elemAt parts 1} -"
    ) directories
  );
  service =
    if systemd == null then
      null
    else
      (
        {
          unit,
          enable ? true,
          startOnInstall ? true,
          restartOnUpgrade ? true,
          stopOnRemove ? true,
        }:
        assert lib.assertMsg (lib.all builtins.isBool [
          enable
          startOnInstall
          restartOnUpgrade
          stopOnRemove
        ]) "systemd policies must be booleans";
        {
          inherit
            unit
            enable
            startOnInstall
            restartOnUpgrade
            stopOnRemove
            ;
        }
      )
        systemd;
in
{
  files =
    lib.optionalAttrs (users != { }) {
      "/usr/lib/sysusers.d/${name}.conf" = pkgs.writeText "${name}-sysusers" userLines;
    }
    // lib.optionalAttrs (directories != { }) {
      "/usr/lib/tmpfiles.d/${name}.conf" = pkgs.writeText "${name}-tmpfiles" directoryLines;
    }
    // lib.optionalAttrs (service != null) {
      "/usr/lib/systemd/system/${name}.service" = service.unit;
    };
  manifest = {
    sysusers = users != { };
    tmpfiles = directories != { };
    service =
      if service == null then
        null
      else
        (builtins.removeAttrs service [ "unit" ])
        // {
          revision = builtins.hashString "sha256" (
            builtins.unsafeDiscardStringContext (toString service.unit)
          );
        };
  };
  enabled = users != { } || directories != { } || systemd != null;
}
