# SPDX-License-Identifier: MIT
{
  pkgs,
  name,
  first,
  second,
  upgraded,
  tools,
  initialize,
  install,
  upgrade,
  removeFirst,
  removeSecond,
  verify,
}:
pkgs.testers.runNixOSTest {
  name = "${name}-native-owned-closure";
  requiredFeatures.kvm = false;
  requiredFeatures.devnet = false;
  nodes.machine = { lib, ... }: {
    virtualisation.memorySize = 1536;
    virtualisation.qemu.package = lib.mkForce pkgs.qemu;
    virtualisation.qemu.forceAccel = false;
    environment.systemPackages = tools;
  };
  testScript = ''
    start_all()
    machine.wait_for_unit("multi-user.target")
    machine.succeed("mkdir -p /srv/target /tmp/packages")
    machine.succeed("cp ${first}/* /tmp/packages/; cp ${second}/* /tmp/packages/")
    machine.succeed(${builtins.toJSON initialize})
    with subtest("native install into a target with no Nix runtime"):
        machine.succeed(${builtins.toJSON install})
        machine.succeed("chroot /srv/target /usr/bin/native-first | grep native-first-1")
        machine.succeed("chroot /srv/target /usr/bin/native-second | grep native-second-1")
        machine.succeed("test ! -e /srv/target/usr/bin/nix && test ! -e /srv/target/usr/bin/nix-store && test ! -e /srv/target/nix/var/nix/db/db.sqlite && test ! -e /srv/target/nix/var/nix/daemon-socket/socket")
        machine.succeed(${builtins.toJSON verify})
    with subtest("upgrade and remove one application while retaining shared dependencies"):
        machine.succeed("rm /tmp/packages/*; cp ${upgraded}/* /tmp/packages/")
        machine.succeed(${builtins.toJSON upgrade})
        machine.succeed("chroot /srv/target /usr/bin/native-first | grep native-first-2")
        machine.succeed(${builtins.toJSON removeFirst})
        machine.fail("chroot /srv/target /usr/bin/native-first")
        machine.succeed("chroot /srv/target /usr/bin/native-second | grep native-second-1")
        machine.succeed(${builtins.toJSON verify})
    with subtest("native cleanup releases the final closure"):
        machine.succeed(${builtins.toJSON removeSecond})
        machine.succeed("test ! -d /srv/target/nix/store || test -z \"$(find /srv/target/nix/store -mindepth 1 -maxdepth 1 -print -quit)\"")
  '';
}
