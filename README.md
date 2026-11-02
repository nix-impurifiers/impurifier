# impurifier

Shared profile engine and payload compiler for `nix-impurifiers`.
Package-manager backends own their artifact formats, dependency expressions,
transaction hooks, and native configuration semantics. This core owns descriptor
validation, closure caches, profile composition, GC roots and activation state.

```nix
let core = inputs.impurifier.lib.impurifier {
  inherit pkgs;
  namespace = "rpm-impurifier";
}; in {
  payload = core.makePayload { pkg = pkgs.hello; offline = true; };
  runtimeTree = core.makeRuntimeTree { };
}
```

Backends can consume this repository as a pinned non-flake input and import
`nix/default.nix` with the same arguments. `makePayload` returns a filesystem tree
with native host assets and `/usr/share/nix-profile.d/<name>.json`. Its passthru
exposes `name`, `version`, `description`, `packageName`, `offline`, `configPaths`,
`hostEnabled` and `manifest`, for the backend's metadata compiler.

`makeRuntimeTree` places the native Python launcher under `/usr/sbin` by default;
`binDir = "/usr/bin"` supports layouts such as ALPM's. It uses the host's Python
and an existing Nix runtime. `engine` is a Nix-wrapped executable for development.
`offlineCache` emits a complete local NAR cache. `unit` runs the engine's unit
contracts for backend check suites to include.

The engine's `--namespace` selects its package-owned cache prefix and default
state directory. Backends explicitly set it; the dpkg backend retains its
existing paths across the extraction. Profiles are atomically published at
`/opt/nix/profile`, with closure roots established before composition completes.
The CLI provides `reconcile`, `status`, and `prune --keep N`.

Manifest cache pointers cannot change global Nix trust. Net substitution uses
administrator configuration. Offline import selects the package-owned local
cache and disables remote builders. Equal-priority leaf collisions, file versus
directory collisions, malformed descriptors and cyclic directory links fail
before publication. Lower priority numbers win leaf conflicts.

Accounts/directories currently compile to sysusers/tmpfiles declarations.
Systemd services start after profile publication. Pending service restart intent
survives failed activation so a repaired package can recover the service without
mistaking its failed state for an administrator's earlier stop decision.
Application, package database and service changes are not one atomic host
transaction. Native backends must document their own failure/recovery behavior.

The Nix provider/installer remains **deferred but in scope**. This repository does
not bootstrap Nix, create daemon accounts, or manage the global Nix configuration.
Consumers must be able to supply their own native Nix provider dependency. The
hardware bridge also needs a separately validated runtime design: changing a
flake registry pin does not rebind an already-built application binary.

Run `nix flake check -L` for core unit checks. The dpkg and RPM backends run real
package-manager VM transactions against this shared source; dpkg additionally
checks Debian's own apt/dpkg userspace. Backend regressions must be checked when
changing the engine or payload contract. GPL-3.0-or-later.
