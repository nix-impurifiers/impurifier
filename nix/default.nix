# SPDX-License-Identifier: MIT
{
  pkgs,
  namespace ? "nix-impurifier",
  sourceInfo ? { },
  # Native package ordering follows the pinned source, not SemVer releases.
  runtimeVersion ? "1.${
    toString (if builtins.isAttrs sourceInfo then sourceInfo.lastModified or 0 else 0)
  }",
}:
let
  inherit (pkgs) lib;
  safeFile =
    prefix: path:
    lib.hasPrefix prefix path
    && builtins.match "[A-Za-z0-9/_.+-]+" path != null
    && lib.all (part: part != "." && part != "..") (lib.splitString "/" path);
  engine =
    pkgs.runCommand "${namespace}-engine-${runtimeVersion}"
      { nativeBuildInputs = [ pkgs.makeWrapper ]; }
      ''
        mkdir -p $out/bin $out/libexec
        install -Dm644 ${../LICENSE} $out/share/licenses/${namespace}/LICENSE
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
      openrc ? null,
      runit ? null,
      lifecycle ? { },
      hardware ? null,
      hostBackend ? (
        if runit != null then
          "runit"
        else if openrc != null then
          "openrc"
        else
          "systemd"
      ),
      mode ? "full",
    }:
    assert lib.assertMsg (builtins.match "[a-z0-9][a-z0-9+.-]+" name != null) "invalid package name";
    assert lib.assertMsg (
      builtins.match "[A-Za-z0-9.+:~_-]+" version != null
    ) "invalid package version";
    assert lib.assertMsg (builtins.match "[^\n\r]+" description != null) "description must be one line";
    assert lib.assertMsg (lib.elem mode [
      "full"
      "application"
      "integration"
    ]) "invalid payload mode";
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
          openrc
          runit
          lifecycle
          hostBackend
          namespace
          ;
      };
      hardwareSpec =
        if hardware == null then
          null
        else
          (
            {
              libraries,
              programs,
              preload ? [ ],
            }:
            assert lib.assertMsg (
              builtins.isAttrs libraries
              && libraries != { }
              && lib.all (safeFile "/") (builtins.attrValues libraries)
            ) "hardware libraries require absolute safe source paths";
            assert lib.assertMsg (lib.all (
              value: builtins.match "[A-Za-z0-9_.+-]+" value != null && value != "." && value != ".."
            ) (builtins.attrNames libraries ++ programs ++ preload)) "invalid hardware library or program name";
            assert lib.assertMsg (
              programs != [ ] && lib.all (value: builtins.hasAttr value libraries) preload
            ) "invalid hardware programs or preload selection";
            {
              inherit libraries programs preload;
            }
          )
            hardware;
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
          // lib.optionalAttrs (hardwareSpec != null) { hardware = hardwareSpec; }
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
          configPaths = lib.optionals (mode != "application") (builtins.attrNames configFiles);
        };
      }
      ''
        mkdir -p $out
        ${lib.optionalString (mode != "integration") ''
          mkdir -p $out/usr/share/nix-profile.d
          cp ${manifest} $out/usr/share/nix-profile.d/${name}.json
        ''}
        ${lib.optionalString (mode != "application") (copyFiles (files // host.files))}
        ${lib.optionalString (mode != "application") (copyFiles configFiles)}
        ${lib.optionalString (offline && mode != "integration") ''
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
        install -Dm644 ${../LICENSE} $out/usr/share/licenses/${namespace}/LICENSE
        cp ${../src/engine.py} $out/usr/lib/${namespace}/engine.py
        cat > $out${binDir}/nix-profile-engine <<'SCRIPT'
        #!/bin/sh
        export PATH=/usr/sbin:/usr/bin:/sbin:/bin:/nix/var/nix/profiles/default/bin
        exec /usr/bin/python3 /usr/lib/${namespace}/engine.py --namespace ${namespace} "$@"
        SCRIPT
        chmod 755 $out${binDir}/nix-profile-engine
      '';
in
assert lib.assertMsg (
  builtins.match "[0-9]+(\\.[0-9]+)*" runtimeVersion != null
) "invalid native runtime version";
{
  inherit
    runtimeVersion
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
