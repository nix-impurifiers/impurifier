# SPDX-License-Identifier: MIT
{ pkgs }:
# Pinned Nix's musl fallback uses SYS_close_range without including its header.
# Keep this compatibility fix local to the bundled-runtime fixture.
pkgs.pkgsStatic.nix.overrideScope (
  _final: previous: {
    nix-util = previous.nix-util.overrideAttrs (old: {
      postPatch = (old.postPatch or "") + ''
        sed -i '1i#include <sys/syscall.h>' unix/file-descriptor.cc
      '';
    });
  }
)
