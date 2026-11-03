# Testing

`nix flake check -L` runs the shared schema/composition/pruning contracts.
The consuming backend VM suites verify package transactions, offline closure
imports, GC, native configuration preservation and service recovery. Run both
dpkg VM suites when changing activation behavior and each other backend's native
transaction suite when changing descriptors or payload layout.

A local override before publishing a core revision is temporary:
`nix flake check --override-input impurifier path:../impurifier --no-write-lock-file`.
Committed backend lockfiles resolve to GitHub revisions.

`native-payload` verifies an unregistered closure tree and unchanged executable
bytes. Runtime-selection unit tests cover external preference, bundled fallback,
explicit selection failure, and absence of operation-level retries. The dpkg
`vm-native` and `vm-bundled` suites establish the native-owned and bundled-client
contracts against real Debian package tools in isolated guest roots.
