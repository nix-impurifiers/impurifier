# SPDX-License-Identifier: MIT
{ ... }:
{ lib, inputs, ... }:
{
  flake.lib = lib.mkForce {
    impurifier = args: import ../../../nix ({ sourceInfo = inputs.self; } // args);
  };
  perSystem =
    { pkgs, ... }:
    let
      core = import ../../../nix {
        inherit pkgs;
        sourceInfo = inputs.self;
      };
    in
    {
      packages.default = core.engine;
      checks.unit = core.unit;
      checks.payload = import ../../../tests/payload.nix { inherit pkgs core; };
      formatter = pkgs.nixfmt-tree;
    };
}
