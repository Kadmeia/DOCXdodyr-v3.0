"""Install a complete native release lock from verified wheels; fail on drift."""
import argparse
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]


def pins(path):
    result={}
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.startswith('-r '):
            result.update(pins(path.parent/line[3:].strip()))
        match=re.match(r'([\w.-]+)==([^\s;\\]+)',line)
        if match:result[re.sub(r'[-_.]+','-',match[1]).lower()]=match[2]
    return result


def check_lock(target):
    if target == 'macos-x86_64':
        lock = ROOT / 'requirements' / 'lock-macos-intel.txt'
        suffix = 'macos'
    elif target == 'windows-x64':
        lock = ROOT / 'requirements' / 'lock-windows.txt'
        suffix = 'windows'
    else:
        lock = ROOT / 'requirements' / 'lock-macos.txt'
        suffix = 'macos'
    required = pins(ROOT / 'requirements/requirements-dev.txt')
    required.update(pins(ROOT / 'requirements' / f'requirements-{suffix}.txt'))
    if target == 'macos-x86_64':
        intel_overrides = {
            'cryptography': '48.0.1',
            'numpy': '1.26.4',
            'opencv-python': '4.10.0.84',
        }
        required = {k: intel_overrides.get(k, v) for k, v in required.items()}
    locked = pins(lock)
    missing = [name for name, version in required.items() if locked.get(name) != version]
    if missing:
        raise RuntimeError('Release lock is incomplete or stale: ' + ', '.join(sorted(missing)))
    return lock


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', choices=('macos-arm64', 'macos-x86_64', 'windows-x64'), required=True)
    args = parser.parse_args()
    expected = 'windows-x64' if sys.platform == 'win32' and platform.machine().lower() in ('amd64', 'x86_64') else 'macos-' + platform.machine() if sys.platform == 'darwin' else None
    if expected != args.target or platform.python_version() not in ('3.11.15', '3.11.16'):
        raise SystemExit('Use the native target runner and canonical Python 3.11.15 or verified portable 3.11.16')
    lock = check_lock(args.target)
    with tempfile.TemporaryDirectory(prefix='docxdodyr-wheels-') as folder:
        subprocess.run([sys.executable, '-m', 'pip', 'download', '--only-binary=:all:', '--require-hashes', '--find-links', str(ROOT / 'requirements/wheels'), '-r', str(lock), '-d', folder], check=True)
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--require-hashes', '--find-links', folder, '-r', str(lock)], check=True)
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)
