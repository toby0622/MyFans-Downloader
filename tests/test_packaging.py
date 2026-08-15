from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


def _load_license_collector():
    script = Path(__file__).resolve().parents[1] / "packaging" / "prepare_licenses.py"
    spec = importlib.util.spec_from_file_location("release_license_collector", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load license collector: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LICENSE_COLLECTOR = _load_license_collector()


class LicenseCollectorTests(unittest.TestCase):
    def test_official_python_license_filename_is_supported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            license_file = Path(temp_dir) / "LICENSE.txt"
            license_file.write_text("Python license", encoding="utf-8")

            result = LICENSE_COLLECTOR._find_python_license(Path(temp_dir))

            self.assertTrue(result.samefile(license_file))

    def test_missing_python_license_lists_checked_candidates(self):
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.assertRaisesRegex(FileNotFoundError, "LICENSE_PYTHON.txt"),
        ):
            LICENSE_COLLECTOR._find_python_license(Path(temp_dir))


if __name__ == "__main__":
    unittest.main()
