import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("deploy", Path(__file__).resolve().parents[1] / "scripts/deploy.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class DeploymentTests(unittest.TestCase):
    def test_sync_detects_edits_deletions_and_unknown_files_before_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = [Path(directory) / name for name in ("source", "target")]
            source.mkdir()
            (source / "plugin.cfg").write_text("first")
            deploy.synchronize(source, target)
            self.assertTrue(deploy.synchronize(source, target, check=True)["matches"])
            (source / "plugin.cfg").write_text("next")
            (target / "plugin.cfg").write_text("local")
            with self.assertRaisesRegex(ValueError, "conflicts"):
                deploy.synchronize(source, target)
            self.assertEqual((target / "plugin.cfg").read_text(), "local")
            (target / "plugin.cfg").write_text("first")
            deploy.synchronize(source, target)
            (source / "plugin.cfg").unlink()
            deploy.synchronize(source, target)
            self.assertFalse((target / "plugin.cfg").exists())
            (target / "unknown.gd").write_text("local")
            with self.assertRaisesRegex(ValueError, "conflicts"):
                deploy.synchronize(source, target)


    def test_fresh_deployment_excludes_tests_and_generated_test_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = [Path(directory) / name for name in ("source", "target")]
            (source / "tests/fixtures").mkdir(parents=True)
            (source / "plugin.cfg").write_text("plugin")
            (source / "tests/test_example.py").write_text("test")
            (source / "tests/fixtures/scene.tscn").write_text("fixture")
            deploy.synchronize(source, target)
            self.assertFalse((target / "tests").exists())
            self.assertTrue(deploy.synchronize(source, target, check=True)["matches"])
            self.assertNotIn("tests/test_example.py", json.loads((target / "deployment.json").read_text())["files"])

    def test_legacy_tests_are_pruned_only_after_manifest_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = [Path(directory) / name for name in ("source", "target")]
            (source / "tests").mkdir(parents=True)
            (source / "plugin.cfg").write_text("plugin")
            (source / "tests/test_example.py").write_text("test")
            target.mkdir()
            (target / "plugin.cfg").write_text("plugin")
            (target / "tests/__pycache__").mkdir(parents=True)
            (target / "tests/test_example.py").write_text("test")
            (target / "tests/test_scene.gd.uid").write_text("generated uid")
            (target / "tests/__pycache__/test_example.pyc").write_bytes(b"cache")
            (target / "deployment.json").write_text(json.dumps({"files": deploy.deploy_files(target, include_tests=True)}))
            self.assertFalse(deploy.synchronize(source, target, check=True)["matches"])
            (target / "tests/test_example.py").write_text("local edit")
            with self.assertRaisesRegex(ValueError, "conflicts"):
                deploy.synchronize(source, target)
            self.assertEqual((target / "tests/test_example.py").read_text(), "local edit")
            (target / "tests/test_example.py").write_text("test")
            deploy.synchronize(source, target)
            self.assertFalse((target / "tests").exists())
            self.assertTrue(deploy.synchronize(source, target, check=True)["matches"])

    def test_unknown_tests_and_symlinks_are_not_silently_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = [Path(directory) / name for name in ("source", "target")]
            source.mkdir()
            (source / "plugin.cfg").write_text("plugin")
            deploy.synchronize(source, target)
            (target / "tests").mkdir()
            (target / "tests/local.py").write_text("local")
            with self.assertRaisesRegex(ValueError, "conflicts"):
                deploy.synchronize(source, target)
            (target / "tests/local.py").unlink()
            (target / "tests/link").symlink_to(source / "plugin.cfg")
            with self.assertRaisesRegex(ValueError, "symlink"):
                deploy.synchronize(source, target)
            self.assertTrue((target / "tests/link").is_symlink())
