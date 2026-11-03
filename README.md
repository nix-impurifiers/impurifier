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

`makePayload.mode` defaults to `"full"`. `"application"` emits only intent and
closure transport, while `"integration"` emits only host assets. ALPM uses a
stable integration package shared by the two flavors because native replacement
of differently named packages otherwise moves edited active configs to `.pacsave`.
`configPaths` lists only configuration files actually owned by the selected tree.

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

Accounts/directories compile to sysusers/tmpfiles declarations for systemd, or an idempotent helper for other supervisors. That helper uses native shadow account tools when available and otherwise BusyBox. `openrc.script` supplies a native init script with the same service policy fields as systemd. Backends select the supervisor and its native dependencies.
Systemd services start after profile publication. Removed OpenRC services use a generation-owned copy of their init script when native package removal has already deleted the installed script. Pending service restart intent
survives failed activation so a repaired package can recover the service without
mistaking its failed state for an administrator's earlier stop decision.
Successful and attempted host manifests remain recorded until activation completes, so removal after partial activation can clean up services already started. Active, completed and pending generations retain their closure roots during pruning.
Application, package database and service changes are not one atomic host
transaction. Native backends must document their own failure/recovery behavior.

The Nix provider/installer remains **deferred but in scope**. This repository does
not bootstrap Nix, create daemon accounts, or manage the global Nix configuration.
Consumers must be able to supply their own native Nix provider dependency. The hardware bridge uses explicit runtime wrappers and immutable host-library snapshots; changing a flake registry pin does not rebind an already-built application binary.

Run `nix flake check -L` for core unit checks. The dpkg, RPM, ALPM and APK backends exercise real
package-manager VM transactions against this shared source; dpkg additionally
checks Debian's own apt/dpkg userspace; APK checks Alpine userspace and OpenRC. Backend regressions must be checked when
changing the engine or payload contract. MIT.

Lifecycle hooks `lifecycle.onInstall` and `lifecycle.onUpgrade` accept shell bodies. They run as root after accounts/directories exist and the new profile is published, before service activation. Use `/opt/nix/profile/bin/<tool>` for projected commands and declare native dependencies for other commands. Install hooks run until the application's first successful activation; upgrade hooks run when its output, version or hook revision changes, including native downgrades. A failed hook leaves activation pending and may be retried, so hooks must be idempotent. They do not provide database rollback or exactly-once execution. Flavor-only changes to the same application do not rerun completed hooks.

Host-library bridging is opt-in:

```nix
hardware = {
  libraries."libdriver.so.1" = "/usr/lib/libdriver.so.1";
  programs = [ "my-tool" ];
  preload = []; # Optional subset of declared library names.
};
```

The engine captures explicitly selected ELF files into an immutable Nix-store directory, roots it with the generation, and wraps the selected application's `bin` commands with `LD_LIBRARY_PATH` and optional `LD_PRELOAD`. Native library-directory transaction hooks reconcile driver upgrades. Changed snapshots create a new generation and restart previously running managed services according to their upgrade policy. Older generations retain their original user-space snapshot. Missing or unreadable libraries abort before profile publication; the existing snapshot remains usable.

Consumers select compatible libraries, their non-Nix dependencies, paths and native package dependencies. This does not discover a GPU stack, validate its ABI, rebind already-running processes, or roll back kernel modules. GPU user/kernel compatibility and real-device behavior need hardware tests. Sources outside `/usr/lib`, `/usr/lib64`, `/lib` and `/lib64` need explicit reconciliation after updates. This is a bounded runtime bridge, not automatic GPU compatibility.

Runit integration accepts `runit.run` and optional `runit.serviceDir` (default `/var/service`). Native service sources live in `/etc/sv/<name>`; an existing runsvdir must watch the selected enable directory. Accounts/directories use the same native account helper. The engine verifies a live supervisor connection rather than trusting stale control files, preserves admin stops/disables, and retains a temporary down marker across failed enable attempts. Enabled-but-initially-stopped services are supported. This adapter requires `stopOnRemove = true`; starting an unenabled service is rejected during emission. The XBPS VM suite verifies the runit contract, including supervisor absence and retry.

Native backends pass their pinned source metadata as `sourceInfo`. `runtimeVersion` derives a numeric native package version from that source's last-modified timestamp, so repository updates can upgrade the engine and newly emitted applications require a sufficiently recent runtime. This is artifact/dependency metadata, not project SemVer release tagging. Direct imports can override `runtimeVersion` when managing a different native version policy. Host preparation has a descriptor revision of its own: changing users or directory ownership/modes is reconciled even when the application output and upstream version stay the same.

## Native-owned payloads and optional runtime clients

`makeNativePayload { pkg = myPackage; }` copies the complete Nix runtime closure
into a filesystem tree at its original absolute store paths. It emits no Nix
registration, daemon, profile manifests, or target-side database. The consumer
must provide native ownership and command/application integration. No relocation
is performed. Inputs are consumer-provided derivations; they need not originate
in nixpkgs. The resulting programs must support the target environment.

Native-owned files must not simultaneously be managed and garbage-collected by
Nix. Merely finding an external Nix executable does not register or transfer
ownership of native payloads. Backends must define ownership and coexistence
before enabling optional Nix operations for those payloads.

`makeRuntimeTree` additionally accepts `bundledNixStore`, a static ELF
`nix-store` executable, and `bundledNixNotices`, a nonempty list of upstream
license/notice files. Supply all notices and corresponding source obligations
appropriate to the binary. The executable is installed in the backend's private
library directory. Bundling an executable does not provision daemon accounts,
configure trust, or make store operations database-free.

The engine prefers a compatible external `nix-store` on its configured PATH,
then its bundled client. `IMPURIFIER_NIX_STORE=/absolute/path` makes an explicit
selection and disables fallback. Selection checks the client version before
performing store operations; a failed operation is never retried with a different
client. Store destination and trust remain Nix configuration, not executable
selection policy. These options currently apply to shared-engine consumers;
per-prefix launchers require their own integration and verification.

`makeNativeTrees { pkg; name ? pkg.pname; version ? pkg.version;
programs ? [ pkg.meta.mainProgram or pkg.pname ]; binDir ? "/usr/bin"; }`
exports native-format-neutral package trees. `packages.json` lists package
identities; each directory contains `tree/` and `metadata.json` with `name`,
`version`, and `dependencies`. Store objects additionally record `storePath`.
The `nix-store-<store hash>` identity namespace is reserved. Reference edges omit
self references. Backends translate them into their own exact native dependency
expressions and compile their own archives. Empty `programs` permits a dependency
or library payload without command exports. This is build-time metadata; no
runtime registry or Nix database is created.
