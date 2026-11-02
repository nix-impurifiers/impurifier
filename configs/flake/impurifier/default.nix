# SPDX-License-Identifier: MIT
{ ... }:
{ lib, ... }:
{
  flake.lib = lib.mkForce { impurifier = args: import ../../../nix args; };
  perSystem =
    { pkgs, ... }:
    let
      core = import ../../../nix { inherit pkgs; };
    in
    {
      packages.default = core.engine;
      checks.unit = core.unit;
      checks.payload = import ../../../tests/payload.nix { inherit pkgs core; };
      formatter = pkgs.nixfmt-tree;
    };
}
