#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compose native-package intent into a GC-rooted, atomically published profile."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile

STORE_PATH = re.compile(
    r"/nix/store/[0-9abcdfghijklmnpqrsvwxyz]{32}-[A-Za-z0-9+._?=-]+\Z"
)
NAME = re.compile(r"[a-z0-9][a-z0-9+.-]*\Z")
PROJECT = ("bin", "sbin", "lib", "libexec", "share", "include")


def validate(value, namespace="nix-impurifier"):
    required = {"name", "version", "priority", "installable"}
    if (
        not isinstance(value, dict)
        or set(value) - required - {"offlineCache", "host", "hardware"}
        or required - set(value)
    ):
        raise ValueError(
            "manifest requires name, version, priority, installable; unknown keys forbidden"
        )
    if not isinstance(value["name"], str) or not NAME.fullmatch(value["name"]):
        raise ValueError("invalid manifest name")
    if not isinstance(value["version"], str) or not value["version"]:
        raise ValueError("version must be a nonempty string")
    if type(value["priority"]) is not int:
        raise ValueError("priority must be an integer (lower wins)")
    if (
        not isinstance(value["installable"], str)
        or not STORE_PATH.fullmatch(value["installable"])
        or value["installable"].endswith(".drv")
    ):
        raise ValueError("installable must be a top-level Nix store output path")
    if (
        "offlineCache" in value
        and value["offlineCache"]
        != "/usr/share/" + namespace + "/offline/" + value["name"]
    ):
        raise ValueError("offline cache must use the package-owned directory")
    if "hardware" in value:
        bridge = value["hardware"]
        atom = re.compile(r"[A-Za-z0-9_.+-]+\Z")
        if (not isinstance(bridge, dict) or set(bridge) != {"libraries", "programs", "preload"}
                or not isinstance(bridge["libraries"], dict) or not bridge["libraries"]
                or not isinstance(bridge["programs"], list) or not bridge["programs"]
                or not isinstance(bridge["preload"], list)):
            raise ValueError("invalid hardware bridge")
        for name in list(bridge["libraries"]) + bridge["programs"] + bridge["preload"]:
            if not isinstance(name, str) or not atom.fullmatch(name) or name in {".", ".."}:
                raise ValueError("invalid hardware library/program name")
        for path in bridge["libraries"].values():
            if (not isinstance(path, str) or not path.startswith("/")
                    or not re.fullmatch(r"/[A-Za-z0-9/_.+-]+", path)
                    or any(part in {".", ".."} for part in path.split("/"))):
                raise ValueError("invalid hardware source path")
        if any(name not in bridge["libraries"] for name in bridge["preload"]):
            raise ValueError("preload must select a declared library")
    if "host" in value:
        host = value["host"]
        if (
            not isinstance(host, dict)
            or set(host) - {"sysusers", "tmpfiles", "service", "supervisor", "prepare", "lifecycle"}
            or {"sysusers", "tmpfiles", "service"} - set(host)
            or type(host["sysusers"]) is not bool
            or type(host["tmpfiles"]) is not bool
            or host.get("supervisor", "systemd") not in {"systemd", "openrc"}
            or type(host.get("prepare", False)) is not bool
        ):
            raise ValueError("invalid host integration")
        hooks = host.get("lifecycle", {})
        if (not isinstance(hooks, dict) or set(hooks) - {"onInstall", "onUpgrade"}
                or any(not isinstance(revision, str) or not re.fullmatch("[0-9a-f]{64}", revision)
                       for revision in hooks.values())):
            raise ValueError("invalid lifecycle hooks")
        service = host["service"]
        if service is not None and (
            not isinstance(service, dict)
            or set(service)
            != {
                "enable",
                "startOnInstall",
                "restartOnUpgrade",
                "stopOnRemove",
                "revision",
            }
            or not isinstance(service["revision"], str)
            or not re.fullmatch("[0-9a-f]{64}", service["revision"])
            or any(
                type(flag) is not bool
                for key, flag in service.items()
                if key != "revision"
            )
        ):
            raise ValueError("invalid service policies")
    return value


def run(*args):
    return subprocess.run(
        args, check=True, text=True, stdout=subprocess.PIPE
    ).stdout.strip()


def compose(destination, entries):
    """Merge directories; fail on equal-priority leaf collisions, including file/dir."""
    destination.mkdir(parents=True, exist_ok=True)
    selected = {}

    def add(source, relative, priority, ancestors):
        # Follow directory symlinks, but reject cycles rather than recurse forever.
        resolved = source.resolve(strict=True)
        if source.is_dir():
            if resolved in ancestors:
                raise ValueError(f"directory symlink cycle: {source}")
            old = selected.get(relative)
            if old and old[1] != "directory":
                raise ValueError(f"file/directory collision: {relative}")
            selected[relative] = (priority, "directory", source)
            for child in sorted(source.iterdir()):
                add(child, relative / child.name, priority, ancestors | {resolved})
        else:
            old = selected.get(relative)
            if old:
                if old[1] == "directory":
                    raise ValueError(f"file/directory collision: {relative}")
                if old[2].resolve() == resolved:
                    return
                if old[0] == priority:
                    raise ValueError(
                        f"equal-priority collision: {relative}: {old[2]} and {source}"
                    )
                if old[0] < priority:
                    return
            selected[relative] = (priority, "file", source)

    for _, output in entries:
        if not output.is_dir():
            raise ValueError(f"package output must be a directory: {output}")
    for priority, output in sorted(entries, key=lambda item: (item[0], str(item[1]))):
        for name in PROJECT:
            source = output / name
            if source.exists() or source.is_symlink():
                add(source, Path(name), priority, set())
    for relative, (_, kind, source) in sorted(selected.items()):
        target = destination / relative
        if kind == "directory":
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source)


def switch(profile, target):
    profile.parent.mkdir(parents=True, exist_ok=True)
    temporary = profile.with_name(profile.name + ".new")
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, profile)
    # Persist the rename; publication is the final state change.
    fd = os.open(profile.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def services(manifests):
    return {
        m["name"]: m for m in manifests if m.get("host", {}).get("service") is not None
    }


def supervisor(manifest):
    return manifest.get("host", {}).get("supervisor", "systemd")


def service_active(manifest, control=None):
    if supervisor(manifest) == "openrc":
        command = [str(control), "status"] if control else ["rc-service", manifest["name"], "status"]
    else:
        command = ["systemctl", "is-active", "--quiet", manifest["name"] + ".service"]
    return subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def service_command(action, manifest, control=None):
    if supervisor(manifest) == "openrc":
        if control:
            run(str(control), action)
        else:
            run("rc-service", manifest["name"], action)
    else:
        # Debian's helper honors policy-rc.d, including image/chroot builds.
        helper = shutil.which("deb-systemd-invoke")
        run(helper or "systemctl", action, manifest["name"] + ".service")


def openrc_control(manifest, state):
    native = Path("/etc/init.d") / manifest["name"]
    if native.exists():
        return native
    # Native removal deletes the runscript before the transaction trigger.
    # Keep its last effective contents with the rooted profile generation.
    for generation in sorted((state / "generations").iterdir()):
        script = generation / "services" / manifest["name"]
        metadata = generation / "manifest.json"
        if not script.is_file() or not metadata.is_file():
            continue
        for old in json.loads(metadata.read_text()):
            if (old["name"] == manifest["name"] and old["installable"] == manifest["installable"]
                    and old.get("host", {}).get("service") == manifest["host"]["service"]):
                return script
    raise ValueError("no cached OpenRC runscript for removed service: " + manifest["name"])


def atomic_write(path, text):
    temporary = path.with_name(path.name + ".new")
    with temporary.open("w") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def manifest_text(manifests):
    return json.dumps(manifests, sort_keys=True, indent=2) + "\n"


def generation_id(manifests):
    return hashlib.sha256(manifest_text(manifests).encode()).hexdigest()


def host_plan(state, namespace):
    path = state / "pending-hosts.json"
    plan = json.loads(path.read_text()) if path.exists() else {"manifests": [], "generations": []}
    if (not isinstance(plan, dict) or set(plan) != {"manifests", "generations"}
            or not isinstance(plan["manifests"], list) or not isinstance(plan["generations"], list)
            or any(not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)
                   for digest in plan["generations"])):
        raise ValueError("invalid pending host activation state")
    for manifest in plan["manifests"]:
        validate(manifest, namespace)
    return plan


def activation_starts(manifests, previous, pending, changed_hardware=frozenset()):
    old, new = services(previous), services(manifests)
    saved = json.loads(pending.read_text()) if pending.exists() else []
    if not isinstance(saved, list) or any(not isinstance(name, str) for name in saved):
        raise ValueError("invalid pending service activation state")
    starts = set()
    for name, manifest in new.items():
        policy = manifest["host"]["service"]
        if name not in old:
            if policy["startOnInstall"]:
                starts.add(name)
        elif policy["restartOnUpgrade"]:
            changed = (
                old[name]["installable"] != manifest["installable"]
                or old[name]["host"]["service"]["revision"] != policy["revision"]
                or name in changed_hardware
            )
            if name in saved or (changed and service_active(old[name])):
                starts.add(name)
    # Persist intent before a restart can turn an active service into a failed
    # one. A repaired package must resume that intent rather than try-restart an
    # inactive unit and incorrectly report successful recovery.
    atomic_write(pending, json.dumps(sorted(starts)) + "\n")
    return starts


def activate_host(manifests, previous, profile, target, starts, cleanup, state, namespace):
    old, new = services(previous), services(manifests)
    for manifest in manifests:
        host = manifest.get("host", {})
        if host.get("prepare"):
            run("/usr/lib/" + namespace + "/host/" + manifest["name"])
        if host.get("sysusers"):
            run("systemd-sysusers", "/usr/lib/sysusers.d/" + manifest["name"] + ".conf")
        if host.get("tmpfiles"):
            run("systemd-tmpfiles", "--create", "/usr/lib/tmpfiles.d/" + manifest["name"] + ".conf")
    for name, manifest in services(cleanup).items():
        if name not in new or supervisor(manifest) != supervisor(new[name]):
            if supervisor(manifest) == "openrc":
                control = openrc_control(manifest, state)
                if manifest["host"]["service"]["stopOnRemove"] and service_active(manifest, control):
                    service_command("stop", manifest, control)
                # rc-update needs the installed runscript even for deletion on
                # some releases; our enable link is still safe to remove after
                # the native package has deleted that script.
                if (Path("/etc/init.d") / name).exists():
                    run("rc-update", "del", name, "default")
                else:
                    (Path("/etc/runlevels/default") / name).unlink(missing_ok=True)
            else:
                unit = name + ".service"
                if manifest["host"]["service"]["stopOnRemove"]:
                    if run("systemctl", "show", "--property=LoadState", "--value", unit) != "not-found":
                        service_command("stop", manifest)
                helper = shutil.which("deb-systemd-helper")
                run(helper or "systemctl", "disable", unit)
    switch(profile, target / "profile")
    if any(supervisor(m) == "systemd" for m in list(services(cleanup).values()) + list(new.values())):
        run("systemctl", "daemon-reload")
    previous_by_name = {m["name"]: m for m in previous}
    for manifest in manifests:
        prior = previous_by_name.get(manifest["name"])
        hooks = manifest.get("host", {}).get("lifecycle", {})
        event = "onInstall" if prior is None else "onUpgrade"
        changed = (prior is None or prior["installable"] != manifest["installable"]
                   or prior["version"] != manifest["version"]
                   or prior.get("host", {}).get("lifecycle", {}) != hooks)
        if event in hooks and changed:
            run("/usr/lib/" + namespace + "/lifecycle/" + manifest["name"] + "/" + event)
    for name, manifest in new.items():
        policy = manifest["host"]["service"]
        if name not in old or supervisor(old[name]) != supervisor(manifest):
            if policy["enable"]:
                if supervisor(manifest) == "openrc":
                    run("rc-update", "add", name, "default")
                else:
                    helper = shutil.which("deb-systemd-helper")
                    run(helper or "systemctl", "enable", name + ".service")
        if name in starts:
            service_command("restart" if name in old else "start", manifest)


def hardware_snapshots(manifests, state):
    """Import explicitly selected host DSOs as immutable, generation-rooted data."""
    snapshots = {}
    for manifest in manifests:
        if "hardware" not in manifest:
            continue
        with tempfile.TemporaryDirectory(prefix=".hardware-", dir=state) as tmp:
            directory = Path(tmp) / ("host-libraries-" + manifest["name"])
            directory.mkdir(mode=0o755)
            for name, source in sorted(manifest["hardware"]["libraries"].items()):
                with open(source, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise ValueError("hardware library must be a regular file: " + source)
                    data = stream.read()
                    after = os.fstat(stream.fileno())
                if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError("hardware library changed during capture: " + source)
                if not data.startswith(b"\x7fELF"):
                    raise ValueError("hardware library must be an ELF object: " + source)
                destination = directory / name
                destination.write_bytes(data)
                destination.chmod(0o444)
            snapshots[manifest["name"]] = run("nix-store", "--add", str(directory))
    return snapshots


def wrap_hardware(profile, manifests, snapshots):
    claimed = set()
    for manifest in manifests:
        if "hardware" not in manifest:
            continue
        bridge = manifest["hardware"]
        snapshot = snapshots[manifest["name"]]
        for program in bridge["programs"]:
            if program in claimed:
                raise ValueError("multiple hardware wrappers for program: " + program)
            claimed.add(program)
            original = Path(manifest["installable"]) / "bin" / program
            target = profile / "bin" / program
            if not original.is_file() or not os.access(original, os.X_OK) or target.resolve() != original.resolve():
                raise ValueError("hardware program must be the selected application's executable: " + program)
            # compose emits a real bin directory containing leaf symlinks.
            target.unlink()
            preload = " ".join(str(Path(snapshot) / name) for name in bridge["preload"])
            script = "#!/bin/sh\nexport LD_LIBRARY_PATH=" + shlex.quote(snapshot) + '${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\n'
            if preload:
                script += "export LD_PRELOAD=" + shlex.quote(preload) + '${LD_PRELOAD:+ $LD_PRELOAD}\n'
            script += "exec " + shlex.quote(str(original)) + ' "$@"\n'
            target.write_text(script)
            target.chmod(0o755)


def reconcile(registry, state, profile, namespace="nix-impurifier"):
    manifests = []
    names = set()
    for descriptor in sorted(registry.glob("*.json")):
        value = validate(json.loads(descriptor.read_text()), namespace)
        if value["name"] in names or descriptor.stem != value["name"]:
            raise ValueError(f"duplicate name or filename mismatch: {descriptor}")
        names.add(value["name"])
        manifests.append(value)
    canonical = manifest_text(manifests)
    snapshots = hardware_snapshots(manifests, state)
    digest = (hashlib.sha256((canonical + json.dumps(snapshots, sort_keys=True)).encode()).hexdigest()
              if snapshots else generation_id(manifests))
    generations = state / "generations"
    generations.mkdir(exist_ok=True)
    target = generations / digest
    activation = state / "activated.json"
    previous = json.loads(activation.read_text()) if activation.exists() else []
    plan = host_plan(state, namespace)
    cleanup_by_name = {m["name"]: m for m in previous + plan["manifests"]}
    cleanup = [cleanup_by_name[name] for name in sorted(cleanup_by_name)]
    if (
        profile.is_symlink()
        and profile.resolve() == target / "profile"
        and previous == manifests
    ):
        (state / "pending-activation.json").unlink(missing_ok=True)
        (state / "pending-hosts.json").unlink(missing_ok=True)
        print(f"unchanged {digest}")
        return
    if not target.exists():
        staging = Path(tempfile.mkdtemp(prefix=".pending-", dir=generations))
        staging.chmod(0o755)
        try:
            roots = state / "roots" / digest
            roots.mkdir(parents=True, exist_ok=True)
            for value in manifests:
                options = []
                if "offlineCache" in value:
                    options = [
                        "--option",
                        "substituters",
                        "file://" + value["offlineCache"],
                        "--option",
                        "require-sigs",
                        "false",
                        "--option",
                        "builders",
                        "",
                    ]
                run(
                    "nix-store",
                    "--realise",
                    value["installable"],
                    "--add-root",
                    str(roots / value["name"]),
                    "--indirect",
                    *options,
                )
            if snapshots:
                (roots / "hardware").mkdir(exist_ok=True)
            for name, snapshot in snapshots.items():
                run("nix-store", "--realise", snapshot, "--add-root", str(roots / "hardware" / name), "--indirect")
            compose(
                staging / "profile",
                [(m["priority"], Path(m["installable"])) for m in manifests],
            )
            wrap_hardware(staging / "profile", manifests, snapshots)
            (staging / "hardware.json").write_text(json.dumps(snapshots, sort_keys=True))
            (staging / "manifest.json").write_text(canonical)
            # GC root names stay stable across publication and process crashes.
            os.rename(staging, target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    if not (target / "manifest.json").exists() or not (target / "profile").is_dir():
        raise ValueError(f"incomplete generation: {target}; remove it and retry")
    pending = state / "pending-activation.json"
    completed_generation = state / "activated-generation"
    prior_snapshots = {}
    if completed_generation.exists():
        metadata = generations / completed_generation.read_text().strip() / "hardware.json"
        if metadata.exists():
            prior_snapshots = json.loads(metadata.read_text())
    prior_bridges = {m["name"]: m.get("hardware") for m in previous}
    bridges = {m["name"]: m.get("hardware") for m in manifests}
    changed_hardware = {
        name for name in set(prior_snapshots) | set(snapshots)
        if prior_snapshots.get(name) != snapshots.get(name) or prior_bridges.get(name) != bridges.get(name)
    }
    starts = activation_starts(manifests, previous, pending, changed_hardware)
    for name, manifest in services(manifests).items():
        if supervisor(manifest) == "openrc":
            cached = target / "services" / name
            cached.parent.mkdir(exist_ok=True)
            shutil.copyfile(Path("/etc/init.d") / name, cached)
            cached.chmod(0o755)
    attempted = dict(cleanup_by_name)
    attempted.update({m["name"]: m for m in manifests})
    atomic_write(state / "pending-hosts.json", json.dumps({
        "manifests": [attempted[name] for name in sorted(attempted)],
        "generations": sorted(set(plan["generations"]) | {digest}),
    }) + "\n")
    activate_host(manifests, previous, profile, target, starts, cleanup, state, namespace)
    atomic_write(state / "activated-generation", digest + "\n")
    atomic_write(activation, canonical)
    pending.unlink(missing_ok=True)
    (state / "pending-hosts.json").unlink(missing_ok=True)
    print(f"activated {digest}")


def prune(state, profile, keep, namespace="nix-impurifier"):
    """Drop old generation roots. Nix GC remains a separate administrator action."""
    active = profile.resolve().parent if profile.is_symlink() else None
    generations = state / "generations"
    existing = sorted(
        (p for p in generations.glob("*") if not p.name.startswith(".")),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
    retained = set(existing[:keep]) | {active}
    activated = state / "activated.json"
    if activated.exists():
        retained.add(generations / generation_id(json.loads(activated.read_text())))
    completed_generation = state / "activated-generation"
    if completed_generation.exists():
        digest = completed_generation.read_text().strip()
        if not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("invalid completed generation")
        retained.add(generations / digest)
    retained.update(generations / digest for digest in host_plan(state, namespace)["generations"])
    for generation in existing:
        if generation not in retained:
            shutil.rmtree(generation)
    # Includes roots left by unsuccessful/crashed reconciliations.
    for roots in (state / "roots").glob("*"):
        if not (generations / roots.name).exists():
            shutil.rmtree(roots)
    for pending in generations.glob(".pending-*"):
        shutil.rmtree(pending)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="nix-impurifier")
    parser.add_argument(
        "--registry", type=Path, default=Path("/usr/share/nix-profile.d")
    )
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--profile", type=Path, default=Path("/opt/nix/profile"))
    parser.add_argument(
        "command",
        choices=["reconcile", "status", "prune"],
        default="reconcile",
        nargs="?",
    )
    parser.add_argument(
        "--keep", type=int, default=2, help="generations retained by prune, plus active"
    )
    args = parser.parse_args()
    if args.keep < 0:
        parser.error("--keep must be nonnegative")
    if not NAME.fullmatch(args.namespace):
        parser.error("invalid namespace")
    if args.state is None:
        args.state = Path("/var/lib") / args.namespace
    os.umask(0o022)
    args.state.mkdir(parents=True, exist_ok=True)
    with (args.state / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "status":
            if not args.profile.is_symlink():
                raise ValueError("no active profile")
            print((args.profile.resolve().parent / "manifest.json").read_text(), end="")
        elif args.command == "prune":
            prune(args.state, args.profile, args.keep, args.namespace)
        else:
            reconcile(
                args.registry,
                args.state.resolve(),
                args.profile.absolute(),
                args.namespace,
            )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"nix-profile-engine: {error}", file=sys.stderr)
        sys.exit(1)
