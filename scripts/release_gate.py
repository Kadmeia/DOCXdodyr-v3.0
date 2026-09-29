"""Production publication gate; run only in a protected release environment.

Evidence is produced/reviewed on each clean native target, then downloaded by
artifact ID from this workflow run. Never accept a user-supplied external URL.
"""
import argparse
import json
import re
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from version import __version__
from scripts.package_release import compute_sha256, verify_sha256sums

TARGETS = ('macos-arm64', 'macos-x86_64', 'windows-x64')
COMMON_FILES = {'EULA.txt', 'EULA.md', 'PRIVACY_POLICY.md', 'THIRD_PARTY_NOTICES.md', 'RELEASE_NOTES.md'}
CHECKS = ('source_tests_no_skips', 'packaged_cli', 'packaged_gui', 'synthetic_processing', 'ocr', 'clean_install', 'clean_update', 'clean_uninstall', 'standard_user', 'architecture', 'signature', 'timestamp', 'documentation', 'redistribution_rights', 'legal_review', 'real_qwen', 'native_keyring', 'secret_pii_scan')


def metadata_names(target):
    names = {f'{target}-{suffix}' for suffix in (
        'environment-sbom.json', 'build-provenance.json', 'verification.json', 'source-tests.xml')}
    if target.startswith('macos'):
        names.add(f'{target}-deployment.json')
    return names


def artifact_names(target, channel='signed-stable'):
    """Return the exact binary artifacts required for one target/channel."""
    if channel not in ('signed-stable', 'unsigned-preview'):
        raise ValueError('Unknown release channel')
    label = '' if channel == 'signed-stable' else '-development'
    suffixes = ('dmg', 'zip') if target.startswith('macos') else ('exe', 'zip')
    names = {f'DOCXdodyr-{__version__}-{target}{label}.{suffix}' for suffix in suffixes}
    if target.startswith('macos'):
        names.add(f'DOCXdodyr-{__version__}-{target}{label}-quick-actions.pkg')
    return names


def verify(dist, commit):
    dist = Path(dist)
    if dist.is_symlink() or not dist.is_dir():
        raise ValueError('Production inventory must be a regular directory')
    entries = list(dist.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in entries):
        raise ValueError('Production inventory must contain only regular files')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Expected an immutable source commit')
    sums = verify_sha256sums(dist/'SHA256SUMS.txt', dist)
    if not sums['valid']:
        raise ValueError('Invalid or incomplete checksums')
    expected = set(COMMON_FILES)
    for target in TARGETS:
        names = artifact_names(target)
        expected |= names
        metadata = metadata_names(target) | COMMON_FILES
        expected.update(metadata | {f'{target}-production-evidence.json'})
        evidence = json.loads((dist/f'{target}-production-evidence.json').read_text())
        for metadata_name in metadata:
            metadata_path = dist / metadata_name
            if not metadata_path.is_file() or metadata_path.is_symlink():
                raise ValueError(f'Missing production metadata for {target}: {metadata_name}')
        if evidence.get('commit') != commit or evidence.get('version') != __version__ or evidence.get('target') != target:
            raise ValueError('Evidence is for a different build')
        provenance = json.loads((dist/f'{target}-build-provenance.json').read_text())
        if (provenance.get('schema') != 'docxdodyr.build/v1' or provenance.get('commit') != commit
                or provenance.get('version') != __version__ or provenance.get('target') != target
                or provenance.get('channel') != 'signed-stable'):
            raise ValueError('Build provenance is for a different build or preview')
        sbom = json.loads((dist/f'{target}-environment-sbom.json').read_text())
        if (sbom.get('bomFormat') != 'CycloneDX' or not sbom.get('components')
                or sbom.get('metadata', {}).get('component', {}).get('version') != __version__):
            raise ValueError('Invalid environment SBOM')
        from scripts.check_test_results import check
        check(dist/f'{target}-source-tests.xml')
        required = CHECKS + (('notarization', 'stapling', 'macos14', 'no_rosetta') if target.startswith('macos') else ('windows10', 'windows11', 'webview2'))
        if any(evidence.get('checks', {}).get(check) != 'pass' for check in required):
            raise ValueError(f'Missing production evidence for {target}')
        for name in names | metadata:
            path = dist/name
            if not path.is_file() or path.is_symlink() or evidence.get('sha256', {}).get(name) != compute_sha256(path):
                raise ValueError('Artifact does not match verified evidence')
            if name != f'{target}-build-provenance.json' and provenance.get('sha256', {}).get(name) != compute_sha256(path):
                raise ValueError('Artifact does not match build provenance')
    if (set(sums['verified_files']) != expected or
            {path.name for path in entries} != expected | {'SHA256SUMS.txt'}):
        raise ValueError('Unexpected or missing production artifacts')
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist', type=Path, default=Path('dist'))
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    verify(args.dist, args.commit)
    print('Production evidence matches all required artifacts')
