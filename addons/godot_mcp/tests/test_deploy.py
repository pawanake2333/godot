import importlib.util
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
