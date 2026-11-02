# SPDX-License-Identifier: GPL-3.0-or-later
{
  pkgs,
  name,
  users,
  directories,
  systemd,
  openrc ? null,
  hostBackend ? (if openrc != null then "openrc" else "systemd"),
  namespace ? "nix-impurifier",
}:
let
  inherit (pkgs) lib;
  safeName = value: builtins.match "[a-z_][a-z0-9_-]*" value != null;
  safePath =
    value:
    builtins.match "/[A-Za-z0-9/_.+-]+" value != null
    && lib.all (part: part != "." && part != "..") (lib.splitString "/" value);
  userSpec = lib.mapAttrs (
    user:
    {
      group ? user,
      description ? user,
      home ? "/var/lib/${user}",
    }:
    assert lib.assertMsg (
      safeName user
      && safeName group
      && safePath home
      && !(lib.hasInfix "\n" description)
      && !(lib.hasInfix "\r" description)
    ) "invalid system user declaration";
    {
      inherit group description home;
    }
  ) users;
  directorySpec = lib.mapAttrs (
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
    {
      inherit owner mode;
      user = builtins.elemAt parts 0;
      group = builtins.elemAt parts 1;
    }
  ) directories;
  normalizeService =
    field: value:
    if value == null then
      null
    else
      assert lib.assertMsg (
        builtins.hasAttr field value
        && lib.all (
          key:
          lib.elem key [
            field
            "enable"
            "startOnInstall"
            "restartOnUpgrade"
            "stopOnRemove"
          ]
        ) (builtins.attrNames value)
      ) "invalid service declaration";
      let
        policy = {
          enable = value.enable or true;
          startOnInstall = value.startOnInstall or true;
          restartOnUpgrade = value.restartOnUpgrade or true;
          stopOnRemove = value.stopOnRemove or true;
        };
      in
      assert lib.assertMsg (lib.all builtins.isBool (
        builtins.attrValues policy
      )) "service policies must be booleans";
      {
        source = builtins.getAttr field value;
        inherit policy;
      };
  service =
    if hostBackend == "openrc" then
      normalizeService "script" openrc
    else
      normalizeService "unit" systemd;
  prepare = hostBackend == "openrc" && (users != { } || directories != { });
  userLines = lib.concatStringsSep "\n" (
    lib.mapAttrsToList (user: spec: ''
      g ${spec.group} -
      u ${user} -:${spec.group} ${builtins.toJSON spec.description} ${spec.home} /usr/sbin/nologin
    '') userSpec
  );
  directoryLines = lib.concatStringsSep "\n" (
    lib.mapAttrsToList (path: spec: "d ${path} ${spec.mode} ${spec.user} ${spec.group} -") directorySpec
  );
  prepareScript = pkgs.writeText "${name}-prepare-host" ''
    #!/bin/sh
    set -eu
    ${lib.concatMapStringsSep "\n" (group: ''
      if ! /usr/bin/python3 -c 'import grp,sys; grp.getgrnam(sys.argv[1])' ${lib.escapeShellArg group} >/dev/null 2>&1; then
        busybox addgroup -S ${lib.escapeShellArg group}
      fi
    '') (lib.unique (lib.mapAttrsToList (_: spec: spec.group) userSpec))}
    ${lib.concatStringsSep "\n" (
      lib.mapAttrsToList (user: spec: ''
        if ! /usr/bin/python3 -c 'import pwd,sys; pwd.getpwnam(sys.argv[1])' ${lib.escapeShellArg user} >/dev/null 2>&1; then
          busybox adduser -S -D -H -h ${lib.escapeShellArg spec.home} -g ${lib.escapeShellArg spec.description} \
            -s /sbin/nologin -G ${lib.escapeShellArg spec.group} ${lib.escapeShellArg user}
        fi
      '') userSpec
    )}
    ${lib.concatStringsSep "\n" (
      lib.mapAttrsToList (path: spec: ''
        mkdir -p ${lib.escapeShellArg path}
        chown ${lib.escapeShellArg spec.owner} ${lib.escapeShellArg path}
        chmod ${spec.mode} ${lib.escapeShellArg path}
      '') directorySpec
    )}
  '';
in
assert lib.assertMsg (lib.elem hostBackend [
  "systemd"
  "openrc"
]) "unsupported host backend";
assert lib.assertMsg (
  systemd == null || hostBackend == "systemd"
) "systemd declarations require the systemd host backend";
assert lib.assertMsg (
  openrc == null || hostBackend == "openrc"
) "OpenRC declarations require the OpenRC host backend";
{
  files =
    lib.optionalAttrs (hostBackend == "systemd" && users != { }) {
      "/usr/lib/sysusers.d/${name}.conf" = pkgs.writeText "${name}-sysusers" userLines;
    }
    // lib.optionalAttrs (hostBackend == "systemd" && directories != { }) {
      "/usr/lib/tmpfiles.d/${name}.conf" = pkgs.writeText "${name}-tmpfiles" directoryLines;
    }
    // lib.optionalAttrs (service != null && hostBackend == "systemd") {
      "/usr/lib/systemd/system/${name}.service" = service.source;
    }
    // lib.optionalAttrs (service != null && hostBackend == "openrc") {
      "/etc/init.d/${name}" = {
        source = service.source;
        mode = "0755";
      };
    }
    // lib.optionalAttrs prepare {
      "/usr/lib/${namespace}/host/${name}" = {
        source = prepareScript;
        mode = "0755";
      };
    };
  manifest = {
    sysusers = hostBackend == "systemd" && users != { };
    tmpfiles = hostBackend == "systemd" && directories != { };
    service =
      if service == null then
        null
      else
        service.policy
        // {
          revision = builtins.hashString "sha256" (
            builtins.unsafeDiscardStringContext (toString service.source)
          );
        };
  }
  // lib.optionalAttrs (hostBackend == "openrc") {
    supervisor = "openrc";
    inherit prepare;
  };
  enabled = users != { } || directories != { } || service != null;
}
