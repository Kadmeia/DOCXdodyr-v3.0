"""Execute the frozen application with isolated synthetic user data; never skip."""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from version import __version__
from scripts.binary_arch import verify_macos_arch, verify_windows_pe


def fingerprint(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def _terminate_process_tree(proc, psutil_module, timeout=5):
    """Never report success on an unverified process or descendant exit."""
    if proc.poll() is not None:
        return
    failures = []
    try:
        process = psutil_module.Process(proc.pid)
    except psutil_module.NoSuchProcess:
        process = None
    except psutil_module.AccessDenied:
        process = None
        failures.append('Cannot inspect process descendants')
    if process is not None:
        try:
            children = process.children(recursive=True)
        except psutil_module.NoSuchProcess:
            children = []
        except psutil_module.AccessDenied:
            children = []
            failures.append('Cannot inspect process descendants')
        for child in children:
            try:
                child.kill()
            except (psutil_module.NoSuchProcess, psutil_module.AccessDenied):
                pass
    else:
        children = []
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    for child in children:
        try:
            child.wait(timeout=timeout)
        except psutil_module.NoSuchProcess:
            pass
        except (psutil_module.TimeoutExpired, psutil_module.AccessDenied):
            failures.append('Child process exit could not be verified')
    try:
        proc.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, ChildProcessError):
        failures.append('Native process exit could not be verified')
    if proc.poll() is None:
        failures.append('Native process remained alive after cleanup')
    if failures:
        raise RuntimeError('; '.join(failures))


def verify(root, check_gui=False, clean_machine=False):
    root = Path(root).resolve()
    if sys.platform == 'darwin':
        arch = verify_macos_arch(root)
        runner_arch = platform.machine()
        if arch != runner_arch:
            raise RuntimeError(f'Packaged architecture {arch} does not match native runner {runner_arch}')
        binary = root / 'Contents/MacOS/DOCXdodyr'
    elif sys.platform == 'win32':
        binary = root / 'DOCXdodyr.exe'
        arch = verify_windows_pe(binary, gui=True)
    else:
        raise RuntimeError('Run on a supported native target')
    before = fingerprint(root)
    with tempfile.TemporaryDirectory(prefix='docxdodyr-packaged-') as folder:
        env = dict(os.environ)
        for key in ('DATA', 'USER_DATA', 'CONFIG', 'CACHE', 'LOG'):
            env[f'DOCXDODYR_{key}_DIR'] = str(Path(folder)/key.lower())
        env['DOCXDODYR_TEST_KEYRING'] = '1'
        for flag in ('--version', '--self-test', '--capabilities', '--diagnostics', '--smoke-test'):
            report_path = Path(folder) / "cli-result.txt"
            report_path.unlink(missing_ok=True)
            result = subprocess.run([str(binary), flag, "--report-file", str(report_path)], env=env, cwd=folder, capture_output=True, text=True, timeout=90, check=True)
            output = report_path.read_text(encoding="utf-8")
            if flag == '--version':
                if __version__ not in output:
                    raise RuntimeError('Frozen version output is missing or incorrect')
            else:
                payload = json.loads(output)
                if 'error' in payload or payload.get('status') == 'error':
                    raise RuntimeError('Frozen CLI reports an error')
                if flag == '--self-test' and (payload.get('status') != 'ok' or not payload.get('paths', {}).get('web_index_present')):
                    raise RuntimeError('Frozen self-test did not verify UI resources')
                if flag == '--smoke-test' and (payload.get('status') != 'ok' or any(
                    payload.get(key) is not True for key in ('docx_anonymization', 'decoder', 'docx_restoration')
                )):
                    raise RuntimeError('Frozen smoke test did not prove a DOCX roundtrip')
    gui_status = 'not-tested'
    if check_gui:
        import psutil
        import time
        with tempfile.TemporaryDirectory(prefix='docxdodyr-gui-') as gui_folder:
            gui_env = dict(os.environ)
            for key in ('DATA', 'USER_DATA', 'CONFIG', 'CACHE', 'LOG'):
                gui_env[f'DOCXDODYR_{key}_DIR'] = str(Path(gui_folder) / key.lower())
            gui_env['DOCXDODYR_TEST_KEYRING'] = '1'
            proc = subprocess.Popen([str(binary)], env=gui_env, cwd=gui_folder)
            try:
                time.sleep(3.5)
                # Process liveness/RSS is only a launch smoke signal, not GUI/UI proof.
                p = psutil.Process(proc.pid)
                if not (p.is_running() and p.memory_info().rss > 20 * 1024 * 1024):
                    gui_status = 'fail'
            finally:
                _terminate_process_tree(proc, psutil)
            if gui_status == 'fail':
                raise RuntimeError('Frozen GUI process launch smoke failed')

    # A CLI flag records operator intent only; clean-OS provenance is not tested here.
    clean_status = 'not-tested'

    if before != fingerprint(root):
        raise RuntimeError('Application wrote into its installation directory')
    return {
        'version': __version__,
        'architecture': arch,
        'runner_architecture': platform.machine(),
        'cli': 'pass',
        'synthetic_docx_roundtrip': 'pass',
        'install_directory_unchanged': True,
        'gui': gui_status,
        'clean_machine': clean_status,
        'clean_machine_requested': bool(clean_machine),
        'gui_process_smoke': 'pass' if check_gui and gui_status != 'fail' else ('fail' if check_gui else 'not-tested'),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('application', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check-gui', action='store_true', help='Record isolated process launch smoke only (not GUI/UI evidence)')
    parser.add_argument('--clean-machine', action='store_true', help='Record that clean-machine verification was requested; evidence remains not-tested')
    args = parser.parse_args()
    report = verify(args.application, check_gui=args.check_gui, clean_machine=args.clean_machine)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print('Frozen CLI checks passed; GUI and clean-machine evidence are reported separately')
