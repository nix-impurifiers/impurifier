# SPDX-License-Identifier: MIT
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('engine', Path(__file__).parents[1] / 'src/engine.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

class RuntimeSelection(unittest.TestCase):
    def version(self, value):
        return subprocess.CompletedProcess([], 0, stdout=value)

    def test_external_is_preferred(self):
        with patch.dict(engine.os.environ, {'IMPURIFIER_BUNDLED_NIX_STORE': '/bundled'}, clear=True), patch.object(engine.shutil, 'which', return_value='/external'), patch.object(engine.subprocess, 'run', return_value=self.version('nix-store (Nix) 2.34.9')):
            self.assertEqual(engine.select_nix_store(), '/external')

    def test_bundle_is_used_when_external_missing_or_incompatible(self):
        for external in (None, '/old'):
            with self.subTest(external=external), patch.dict(engine.os.environ, {'IMPURIFIER_BUNDLED_NIX_STORE': '/bundled'}, clear=True), patch.object(engine.shutil, 'which', return_value=external), patch.object(engine.subprocess, 'run', side_effect=([self.version('nix-store (Nix) 1.0')] if external else []) + [self.version('nix-store (Nix) 2.34.9')]):
                self.assertEqual(engine.select_nix_store(), '/bundled')

    def test_explicit_selection_does_not_silently_fall_back(self):
        with patch.dict(engine.os.environ, {'IMPURIFIER_NIX_STORE': '/explicit', 'IMPURIFIER_BUNDLED_NIX_STORE': '/bundled'}, clear=True), patch.object(engine.subprocess, 'run', return_value=self.version('not nix')):
            with self.assertRaisesRegex(ValueError, 'compatible'):
                engine.select_nix_store()

    def test_operation_failure_is_not_retried_with_another_store(self):
        failure = subprocess.CalledProcessError(1, ['nix-store'])
        with patch.object(engine, 'select_nix_store', return_value='/selected'), patch.object(engine.subprocess, 'run', side_effect=failure) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                engine.run('nix-store', '--realise', '/example')
            self.assertEqual(run.call_count, 1)

if __name__ == '__main__':
    unittest.main()
