import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ceres_research.cli import main, PROJECT_ROOT
from ceres_research.smoke import run


class EntryPoints(unittest.TestCase):
    def test_all_smoke_examples(self):
        result = run()
        for name in ("parent_selection", "cvrp_selection_crc", "turn_modes"):
            self.assertTrue(result[name]["passed"])
        self.assertEqual(result["new_model_calls"], 0)

    def test_missing_data_fails_clearly(self):
        with tempfile.TemporaryDirectory() as folder:
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                code = main(["replay", "parent-selection", "--data-root", folder,
                             "--output", str(Path(folder) / "out")])
            self.assertEqual(code, 2)
            self.assertIn("DATA_RESTORE.md", errors.getvalue())
            self.assertFalse((Path(folder) / "out").exists())

    def test_optimized_python_rejected(self):
        result = subprocess.run([sys.executable, "-O", str(PROJECT_ROOT / "scripts/ceres.py"), "smoke"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("-O/-OO is unsupported", result.stderr)

    def test_output_name_reserved_and_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "out"
            with patch("ceres_research.cli.verify_bundle", return_value={}), \
                 patch("ceres_research.parent_selection.run") as run, \
                 contextlib.redirect_stderr(io.StringIO()):
                command = ["replay", "parent-selection", "--data-root", str(Path(folder) / "data"),
                           "--output", str(output)]
                self.assertEqual(main(command), 0)
                self.assertTrue(output.is_dir())
                self.assertEqual(main(command), 2)
                run.assert_called_once()

    def test_dangling_output_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "link"
            output.symlink_to(Path(folder) / "missing")
            with patch("ceres_research.cli.verify_bundle", return_value={}), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["replay", "parent-selection", "--data-root", str(Path(folder) / "data"),
                                       "--output", str(output)]), 2)
            self.assertFalse((Path(folder) / "missing").exists())


if __name__ == "__main__":
    unittest.main()
