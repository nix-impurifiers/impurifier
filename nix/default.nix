# SPDX-License-Identifier: GPL-3.0-or-later
{
  pkgs,
  namespace ? "nix-impurifier",
}:
let
  inherit (pkgs) lib;
  safeFile =
    prefix: path:
    lib.hasPrefix prefix path
    && builtins.match "[A-Za-z0-9/_.+-]+" path != null
    && lib.all (part: part != "." && part != "..") (lib.splitString "/" path);
  engine =
    pkgs.runCommand "${namespace}-engine-0.1.0" { nativeBuildInputs = [ pkgs.makeWrapper ]; }
      ''
        mkdir -p $out/bin $out/libexec
        cp ${../src/engine.py} $out/libexec/engine.py
        makeWrapper ${pkgs.python3}/bin/python3 $out/bin/nix-profile-engine \
          --add-flags "$out/libexec/engine.py --namespace ${namespace}" \
          --prefix PATH : ${
            lib.makeBinPath [
              pkgs.nix
              pkgs.coreutils
            ]
          }
      '';
  offlineCache =
    pkg:
    pkgs.runCommand "${pkg.pname or pkg.name}-offline-cache"
      {
        nativeBuildInputs = [
          pkgs.nix
          pkgs.python3
        ];
        closure = pkgs.closureInfo { rootPaths = [ pkg ]; };
      }
      ''
        mkdir -p $out/nar
        python3 ${./offline-cache.py} "$closure/registration" "$out"
      '';
  copyFiles =
    assets:
    lib.concatStringsSep "\n" (
      lib.mapAttrsToList (
        path: value:
        let
          asset =
            if builtins.isAttrs value && !(lib.isDerivation value) then
              (
                {
                  source,
                  mode ? "0644",
                }:
                {
                  inherit source mode;
                }
              )
                value
            else
              {
                source = value;
                mode = "0644";
              };
        in
        assert lib.assertMsg (builtins.match "0[0-7]{3}" asset.mode != null) "invalid file mode";
        ''install -Dm${asset.mode} ${lib.escapeShellArg (toString asset.source)} "$out${path}"''
      ) assets
    );
  makePayload =
    {
      pkg,
      name ? pkg.pname,
      version ? pkg.version,
      priority ? 5,
      offline ? false,
      description ? "Nix-projected ${name}",
      configFiles ? { },
      files ? { },
      users ? { },
      directories ? { },
      systemd ? null,
    }:
    assert lib.assertMsg (builtins.match "[a-z0-9][a-z0-9+.-]+" name != null) "invalid package name";
    assert lib.assertMsg (
      builtins.match "[A-Za-z0-9.+:~_-]+" version != null
    ) "invalid package version";
    assert lib.assertMsg (builtins.match "[^\n\r]+" description != null) "description must be one line";
    assert lib.assertMsg (builtins.isBool offline) "offline must be a boolean";
    assert lib.assertMsg (builtins.isInt priority) "priority must be an integer";
    assert lib.assertMsg (lib.all (safeFile "/etc/") (
      builtins.attrNames configFiles
    )) "configFiles must use safe /etc paths";
    assert lib.assertMsg (lib.all (
      path:
      safeFile "/usr/" path
      && !(lib.hasPrefix "/usr/share/nix-profile.d/" path)
      && !(lib.hasPrefix "/usr/share/${namespace}/" path)
    ) (builtins.attrNames files)) "files must use safe /usr paths outside the manifest registry";
    let
      host = import ./host.nix {
        inherit
          pkgs
          name
          users
          directories
          systemd
          ;
      };
      flavor = if offline then "offline" else "net";
      packageName = "${name}-${flavor}";
      cacheDir = "/usr/share/${namespace}/offline/${name}";
      manifest = pkgs.writeText "${name}.json" (
        builtins.toJSON (
          {
            inherit name version priority;
            installable = builtins.unsafeDiscardStringContext (toString pkg);
          }
          // lib.optionalAttrs offline { offlineCache = cacheDir; }
          // lib.optionalAttrs host.enabled { host = host.manifest; }
        )
      );
    in
    pkgs.runCommand "${packageName}-${version}-payload"
      {
        passthru = {
          inherit
            name
            version
            description
            packageName
            manifest
            offline
            ;
          hostEnabled = host.enabled;
          configPaths = builtins.attrNames configFiles;
        };
      }
      ''
        mkdir -p $out/usr/share/nix-profile.d
        cp ${manifest} $out/usr/share/nix-profile.d/${name}.json
        ${copyFiles (files // host.files)}
        ${copyFiles configFiles}
        ${lib.optionalString offline ''
          mkdir -p $out${cacheDir}
          cp -r ${offlineCache pkg}/. $out${cacheDir}/
        ''}
      '';
  makeRuntimeTree =
    {
      binDir ? "/usr/sbin",
    }:
    assert lib.assertMsg (lib.elem binDir [
      "/usr/bin"
      "/usr/sbin"
    ]) "invalid runtime binDir";
    pkgs.runCommand "${namespace}-runtime-tree"
      {
        passthru.binaryPath = "${binDir}/nix-profile-engine";
      }
      ''
        mkdir -p $out/usr/lib/${namespace} $out${binDir}
        cp ${../src/engine.py} $out/usr/lib/${namespace}/engine.py
        cat > $out${binDir}/nix-profile-engine <<'SCRIPT'
        #!/bin/sh
        export PATH=/usr/sbin:/usr/bin:/sbin:/bin:/nix/var/nix/profiles/default/bin
        exec /usr/bin/python3 /usr/lib/${namespace}/engine.py --namespace ${namespace} "$@"
        SCRIPT
        chmod 755 $out${binDir}/nix-profile-engine
      '';
in
{
  inherit
    engine
    offlineCache
    makePayload
    makeRuntimeTree
    ;
  unit = pkgs.runCommand "impurifier-unit" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    cp -r ${../.} source
    python3 source/tests/test_engine.py
    touch $out
  '';
}
