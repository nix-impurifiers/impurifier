# SPDX-License-Identifier: MIT
{ pkgs, core }:
let
  trees = core.makeNativeTrees { pkg = pkgs.hello; };
  invalid = builtins.tryEval (
    (core.makeNativeTrees {
      pkg = pkgs.hello;
      name = "nix-store-reserved";
    }).drvPath
  );
in
assert !invalid.success;
pkgs.runCommand "native-object-reference-graph" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    python3 - ${trees} ${pkgs.hello} <<'PY'
  import json
  from pathlib import Path
  import sys
  root = Path(sys.argv[1])
  names = json.loads((root / 'packages.json').read_text())
  assert 'hello' in names
  assert len(names) > 1
  for name in names:
      metadata = json.loads((root / name / 'metadata.json').read_text())
      assert set(metadata['dependencies']).issubset(names)
      if 'storePath' in metadata:
          assert (root / name / 'tree' / metadata['storePath'].lstrip('/')).exists()
      assert not (root / name / 'tree/nix/var/nix/db/db.sqlite').exists()
  command = root / 'hello/tree/usr/bin/hello'
  assert command.is_symlink()
  assert str(command.readlink()) == sys.argv[2] + '/bin/hello'
  PY
    touch "$out"
''
