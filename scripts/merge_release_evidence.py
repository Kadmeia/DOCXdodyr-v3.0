"""Merge immutable build files and evidence without permitting evidence to replace binaries."""
import argparse
from pathlib import Path
import shutil
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.release_gate import TARGETS, verify
from scripts.package_release import compute_sha256, generate_sha256sums


def merge(build, evidence, output, commit):
    build, evidence, output = Path(build), Path(evidence), Path(output)
    for root in (build, evidence):
        if root.is_symlink() or not root.is_dir():
            raise ValueError('Artifact input must be a regular directory')
    if output.is_symlink():
        raise ValueError('Validation output cannot be a symlink')
    roots = [path.resolve() for path in (build, evidence, output)]
    for index, root in enumerate(roots):
        for other in roots[index + 1:]:
            if root.is_relative_to(other) or other.is_relative_to(root):
                raise ValueError('Artifact input and output directories must not overlap')
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Validation output must be empty')
    output.mkdir(parents=True, exist_ok=True)
    allowed = {f'{target}-production-evidence.json' for target in TARGETS}
    for root, evidence_only in ((Path(build), False), (Path(evidence), True)):
        for source in sorted(root.rglob('*')):
            if source.is_symlink():
                raise ValueError('Symlink in downloaded artifacts')
            if not source.is_file():
                continue
            if evidence_only and source.name not in allowed:
                raise ValueError('Native evidence cannot supply build artifacts')
            if not evidence_only and (source.name in allowed or source.name == 'SHA256SUMS.txt'):
                raise ValueError('Build cannot supply approval evidence or merged checksums')
            destination = output/source.name
            if destination.exists():
                if compute_sha256(destination) != compute_sha256(source):
                    raise ValueError('Conflicting artifact names')
            else:
                shutil.copy2(source, destination)
    generate_sha256sums(output)
    verify(output, commit)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for flag in ('build', 'evidence', 'output'):
        p.add_argument('--'+flag, type=Path, required=True)
    p.add_argument('--commit', required=True)
    a = p.parse_args()
    merge(a.build, a.evidence, a.output, a.commit)
