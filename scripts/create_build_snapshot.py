"""Record local build inputs without exporting their contents or claiming a clean commit."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    names = subprocess.check_output(['git', 'ls-files', '-c', '-o', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
    inventory = {}
    # Explicit source/resource scope; private audit, corpora and build outputs
    # cannot silently become declared application inputs.
    directories = {'app', 'web', 'assets', 'docs', 'pullenti_legal', 'requirements', 'scripts'}
    root_suffixes = {'.py', '.spec', '.txt', '.md', '.json'}
    for name in sorted(set(names) - {''}):
        path = Path(name)
        if not (path.parts[0] in directories or len(path.parts) == 1 and path.suffix in root_suffixes):
            continue
        full = ROOT / path
        if any(p.is_symlink() for p in (full, *full.parents)):
            raise ValueError('Symlink in declared source inputs')
        if full.is_file():
            with full.open('rb') as stream:
                inventory[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    encoded = json.dumps(inventory, sort_keys=True).encode()
    return {'schema': 'docxdodyr.local-build-snapshot/v1',
            'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            'dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).strip()),
            'source_sha256': hashlib.sha256(encoded).hexdigest(), 'source_files': inventory,
            'installed_distributions': {d.metadata['Name']: d.version for d in importlib.metadata.distributions() if d.metadata.get('Name')},
            'bundle_qwen': os.environ.get('DOCXDODYR_BUNDLE_QWEN') == '1',
            'limits': 'Local source/resource hashes and installed version metadata; not remote attestation or hashes of all venv binaries. Model weights are external and verified by the Qwen execution gate.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = snapshot()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps({'source_sha256': report['source_sha256'], 'files': len(report['source_files']), 'dirty': report['dirty']}))
