# SPDX-License-Identifier: MIT
import importlib.util
import json
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "engine", pathlib.Path(__file__).parents[1] / "src/engine.py"
)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class ManifestTests(unittest.TestCase):
    def test_reject_invalid_inputs(self):
        valid = dict(
            name="tool",
            version="1",
            priority=5,
            installable="/nix/store/" + "0" * 32 + "-tool",
        )
        for patch in [
            dict(installable="/tmp/tool"),
            dict(priority=True),
            dict(name="../tool"),
            dict(installable=valid["installable"] + ".drv"),
            dict(binaryCache="https://evil"),
            dict(installable=valid["installable"] + "/bin"),
        ]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                engine.validate(valid | patch)
        self.assertEqual(engine.validate(valid), valid)

    def test_offline_cache_is_owned_by_selected_backend(self):
        value = dict(
            name="tool",
            version="1",
            priority=5,
            installable="/nix/store/" + "0" * 32 + "-tool",
            offlineCache="/usr/share/rpm-impurifier/offline/tool",
        )
        with self.assertRaises(ValueError):
            engine.validate(value, "dpkg-impurifier")
        self.assertEqual(engine.validate(value, "rpm-impurifier"), value)
        with self.assertRaises(ValueError):
            engine.validate(
                value | {"offlineCache": "/usr/share/rpm-impurifier/offline/other"},
                "rpm-impurifier",
            )

    def test_openrc_host_manifest(self):
        policy = dict(enable=True, startOnInstall=True, restartOnUpgrade=True,
                      stopOnRemove=True, revision="a" * 64)
        value = dict(name="tool", version="1", priority=5,
                     installable="/nix/store/" + "0" * 32 + "-tool",
                     host=dict(sysusers=False, tmpfiles=False, prepare=True,
                               supervisor="openrc", service=policy))
        self.assertEqual(engine.validate(value), value)
        with self.assertRaises(ValueError):
            engine.validate(value | {"host": value["host"] | {"supervisor": "arbitrary-shell"}})

    def test_lifecycle_rejects_arbitrary_events_and_script_paths(self):
        value = dict(name="tool", version="1", priority=5,
                     installable="/nix/store/" + "0" * 32 + "-tool",
                     host=dict(sysusers=False, tmpfiles=False, service=None,
                               lifecycle={"onInstall": "a" * 64}))
        self.assertEqual(engine.validate(value), value)
        for hooks in [{"onRemove": "a" * 64}, {"onInstall": "/tmp/script"}]:
            with self.subTest(hooks=hooks), self.assertRaises(ValueError):
                engine.validate(value | {"host": value["host"] | {"lifecycle": hooks}})

    def test_hardware_paths_and_preload_are_constrained(self):
        bridge = dict(libraries={"libdriver.so": "/usr/lib/libdriver.so"}, programs=["tool"], preload=[])
        value = dict(name="tool", version="1", priority=5,
                     installable="/nix/store/" + "0" * 32 + "-tool", hardware=bridge)
        self.assertEqual(engine.validate(value), value)
        for patch in [dict(libraries={"../escape": "/usr/lib/driver"}),
                      dict(libraries={"libdriver.so": "/usr/lib/../driver"}),
                      dict(preload=["undeclared.so"]), dict(programs=["../tool"])]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                engine.validate(value | {"hardware": bridge | patch})

    def test_runit_service_directory_is_constrained(self):
        value = dict(name="tool", version="1", priority=5,
                     installable="/nix/store/" + "0" * 32 + "-tool",
                     host=dict(sysusers=False, tmpfiles=False, service=None,
                               supervisor="runit", prepare=True, serviceDir="/var/service"))
        self.assertEqual(engine.validate(value), value)
        with self.assertRaises(ValueError):
            engine.validate(value | {"host": value["host"] | {"serviceDir": "/var/../service"}})

    def test_collision_and_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for name in ["a", "b"]:
                (root / name / "bin").mkdir(parents=True)
                (root / name / "bin/tool").write_text(name)
            entries = [(5, root / "a"), (5, root / "b")]
            with self.assertRaises(ValueError):
                engine.compose(root / "bad", entries)
            engine.compose(root / "good", [(10, root / "a"), (5, root / "b")])
            self.assertEqual((root / "good/bin/tool").read_text(), "b")

    def test_directories_and_file_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "a/bin").mkdir(parents=True)
            (root / "a/bin/tool").write_text("a")
            (root / "b").mkdir()
            (root / "b/bin").write_text("b")
            with self.assertRaises(ValueError):
                engine.compose(root / "bad", [(5, root / "a"), (5, root / "b")])

    def test_prune_preserves_active_and_drops_failed_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            state = root / "state"
            for name in ["old", "active", "failed"]:
                (state / "roots" / name).mkdir(parents=True)
            for name in ["old", "active"]:
                (state / "generations" / name / "profile").mkdir(parents=True)
            profile = root / "profile"
            profile.symlink_to(state / "generations/active/profile")
            engine.prune(state, profile, 0)
            self.assertTrue(profile.is_dir())
            self.assertFalse((state / "generations/old").exists())
            self.assertFalse((state / "roots/old").exists())
            self.assertFalse((state / "roots/failed").exists())
            self.assertTrue((state / "roots/active").exists())

    def test_prune_keeps_completed_and_pending_host_generations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            state = root / "state"
            completed = []
            completed_id = engine.generation_id(completed)
            pending_id = "a" * 64
            hardware_completed = "b" * 64
            for name in [completed_id, pending_id, hardware_completed, "discard"]:
                (state / "generations" / name / "profile").mkdir(parents=True)
                (state / "roots" / name).mkdir(parents=True)
            (state / "activated.json").write_text(json.dumps(completed))
            (state / "activated-generation").write_text(hardware_completed)
            (state / "pending-hosts.json").write_text(json.dumps({
                "manifests": [], "generations": [pending_id],
            }))
            profile = root / "profile"
            profile.symlink_to(state / "generations" / pending_id / "profile")
            engine.prune(state, profile, 0)
            self.assertTrue((state / "generations" / completed_id).exists())
            self.assertTrue((state / "generations" / hardware_completed).exists())
            self.assertTrue((state / "generations" / pending_id).exists())
            self.assertFalse((state / "generations/discard").exists())

    def test_directory_symlink_cycle_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "a/bin").mkdir(parents=True)
            (root / "a/bin/cycle").symlink_to(root / "a/bin")
            with self.assertRaises(ValueError):
                engine.compose(root / "profile", [(5, root / "a")])


if __name__ == "__main__":
    unittest.main()
