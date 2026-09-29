"""Fail-closed inspection of the actual binaries, independent of artifact names."""
from pathlib import Path
import struct
import subprocess

MACH_MAGICS = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}


def mach_files(root):
    for path in sorted(Path(root).rglob('*')):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as stream:
                if stream.read(4) in MACH_MAGICS:
                    yield path


def verify_macos_arch(app, expected=None):
    files = list(mach_files(app))
    main = Path(app) / 'Contents/MacOS/DOCXdodyr'
    if main not in files:
        raise ValueError('Missing Mach-O application executable')
    actual = None
    for path in files:
        archs = subprocess.run(['lipo', '-archs', str(path)], check=True, capture_output=True, text=True).stdout.split()
        if len(archs) != 1 or archs[0] not in ('arm64', 'x86_64'):
            raise ValueError(f'Expected a supported thin binary: {path.name}')
        actual = actual or archs[0]
        if archs[0] != actual or (expected and actual != expected):
            raise ValueError(f'Architecture mismatch: {path.name}')
    return actual


def verify_windows_pe(path, gui=False):
    with Path(path).open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('Missing DOS header')
        stream.seek(0x3c)
        offset = struct.unpack('<I', stream.read(4))[0]
        stream.seek(offset)
        if stream.read(6) != b'PE\0\0\x64\x86':
            raise ValueError('Expected Windows x64 PE')
        stream.seek(offset + 24)
        if stream.read(2) != b'\x0b\x02':
            raise ValueError('Expected PE32+')
        if gui:
            stream.seek(offset + 24 + 68)
            if stream.read(2) != b'\x02\x00':
                raise ValueError('Expected Windows GUI subsystem')
    return 'x64'
