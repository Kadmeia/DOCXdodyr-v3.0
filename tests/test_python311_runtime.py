"""Runtime entrypoints must reject incompatible Python before loading the app."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class Python311RuntimeTests(unittest.TestCase):
    def test_entrypoint_rejects_unsupported_runtime_before_dependencies(self):
        for minor in (8, 9, 10, 12, 13):
            with self.subTest(minor=minor):
                code = (
                    "import sys, runpy; "
                    f"sys.version_info=(3,{minor},0); "
                    f"runpy.run_path({str(ROOT / 'main.py')!r}, run_name='__main__')"
                )
                result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Python 3.11', result.stderr)
                self.assertNotIn('ModuleNotFoundError', result.stderr)

    def test_spec_rejects_wrong_runtime_before_pyinstaller_import(self):
        code = (
            "import sys, runpy; sys.version_info=(3,12,0); "
            f"runpy.run_path({str(ROOT / 'DOCXdodyr.spec')!r})"
        )
        result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires Python 3.11', result.stderr)
        self.assertNotIn('ModuleNotFoundError', result.stderr)

    def test_windows_build_function_rejects_incompatible_runtime_before_cleanup(self):
        from scripts import build_windows
        for minor in (10, 12):
            with patch.object(build_windows.sys, 'platform', 'win32'), patch.object(build_windows.sys, 'version_info', (3, minor, 0)), patch.object(build_windows.shutil, 'rmtree') as cleanup:
                with self.assertRaisesRegex(RuntimeError, 'Python 3.11'):
                    build_windows.build_app()
                cleanup.assert_not_called()

    def test_windows_requirements_marker_detects_missing_and_changed_inputs(self):
        launcher = (ROOT / 'run_windows.bat').read_text()
        command = next(line for line in launcher.splitlines() if "marker=Path('venv/.docxdodyr-ready')" in line)
        check_code = command.split(' -c "', 1)[1][:-1]
        with tempfile.TemporaryDirectory(prefix='docxdodyr marker ') as tmp:
            root = Path(tmp)
            (root / 'venv').mkdir()
            (root / 'requirements/nested').mkdir(parents=True)
            sources = [root / 'requirements.txt', root / 'requirements/nested/core.txt']
            for source in sources:
                source.touch()
                os.utime(source, ns=(100, 100))
            def check():
                return subprocess.run([sys.executable, '-c', check_code], cwd=root, capture_output=True).returncode
            self.assertEqual(check(), 1)
            marker = root / 'venv/.docxdodyr-ready'
            marker.touch()
            os.utime(marker, ns=(200, 200))
            self.assertEqual(check(), 0)
            for source in sources:
                os.utime(source, ns=(300, 300))
                self.assertEqual(check(), 1)
                os.utime(source, ns=(100, 100))
            self.assertEqual(check(), 0)

    @unittest.skipIf(sys.platform == 'win32', 'macOS launcher tests are not supported on Windows')
    @unittest.skipUnless(shutil.which('bash'), 'POSIX shell launcher requires bash')
    def test_macos_launcher_preserves_spaced_arguments_and_exit_status(self):
        with tempfile.TemporaryDirectory(prefix='docxdodyr launcher ') as tmp:
            root = Path(tmp)
            shutil.copyfile(ROOT / 'run_macos.command', root / 'run_macos.command')
            (root / 'venv/bin').mkdir(parents=True)
            (root / 'requirements').mkdir()
            (root / 'requirements.txt').touch()
            (root / 'venv/.docxdodyr-ready').touch()
            shim = root / 'venv/bin/python'
            shim.write_text('#!/bin/bash\nif [ "$1" = "-c" ]; then exit 0; fi\nprintf "%s\\n" "$@" > received.txt\nexit 17\n')
            shim.chmod(0o755)
            result = subprocess.run(['bash', str(root / 'run_macos.command'), 'a document.docx', 'dir with spaces/b.xlsx'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 17, result.stderr)
            self.assertEqual((root / 'received.txt').read_text().splitlines(), ['main.py', 'a document.docx', 'dir with spaces/b.xlsx'])

    @unittest.skipIf(sys.platform == 'win32', 'macOS launcher tests are not supported on Windows')
    @unittest.skipUnless(shutil.which('bash'), 'POSIX shell launcher requires bash')
    def test_macos_launcher_stops_for_wrong_existing_runtime(self):
        with tempfile.TemporaryDirectory(prefix='docxdodyr launcher ') as tmp:
            root = Path(tmp)
            shutil.copyfile(ROOT / 'run_macos.command', root / 'run_macos.command')
            (root / 'venv/bin').mkdir(parents=True)
            shim = root / 'venv/bin/python'
            shim.write_text('#!/bin/bash\nexit 1\n')
            shim.chmod(0o755)
            result = subprocess.run(['bash', str(root / 'run_macos.command')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Python 3.11', result.stdout)


if __name__ == '__main__':
    unittest.main()
