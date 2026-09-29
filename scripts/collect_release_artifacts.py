"""Stage one native build without inventing clean-machine or signing evidence."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.release_gate import TARGETS, COMMON_FILES, metadata_names, artifact_names
from scripts.package_release import compute_sha256
from scripts.check_test_results import check
from version import __version__


def require_clean_source(commit):
    """Exact-commit staging accepts only a clean tracked/nonignored checkout.

    Ignored build outputs and dependencies are outside this check. It does not
    attest that a binary was built by a trusted runner; that is a separate gate.
    """
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if actual != commit:
        raise ValueError('Checkout differs from requested source commit')
    status = subprocess.check_output(
        ['git', 'status', '--porcelain=v1', '--untracked-files=all', '--ignore-submodules=none'],
        cwd=ROOT, text=True)
    if status.strip():
        # Do not export filenames or content from a dirty private checkout.
        raise ValueError('Exact-commit collection requires a clean source checkout')


def collect(dist, output, target, commit, channel, junit):
    if target not in TARGETS or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('Expected native target and exact commit')
    if channel not in ('signed-stable', 'unsigned-preview'):
        raise ValueError('Unknown release channel')
    require_clean_source(commit)
    dist, output, junit = Path(dist).absolute(), Path(output).absolute(), Path(junit).absolute()
    # Reject links in ancestors too: a regular leaf inside a linked directory
    # must not redirect staging or substitute artifact inputs.
    for path in (dist, output, junit):
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError('Release paths cannot contain symlinks')
    if not dist.is_dir():
        raise ValueError('Artifact input must be a regular directory')
    resolved_dist, resolved_output = dist.resolve(), output.resolve()
    if (resolved_output.is_relative_to(resolved_dist)
            or resolved_dist.is_relative_to(resolved_output)):
        raise ValueError('Artifact input and output directories must not overlap')
    source_root = ROOT.resolve()
    if source_root == resolved_output or source_root.is_relative_to(resolved_output):
        raise ValueError('Staging cannot contain the source checkout')
    if junit.resolve().is_relative_to(resolved_output):
        raise ValueError('Staging cannot contain source test evidence')
    # No upload or signing here. Non-empty staging dirs are never reused.
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Release staging directory must be empty')
    dist = Path(dist)
    check(junit)
    names = artifact_names(target, channel)
    sources = {name: dist/name for name in names}
    sources.update({name: ROOT/name for name in COMMON_FILES})
    for name in metadata_names(target) - {f'{target}-build-provenance.json'}:
        sources[name] = Path(junit) if name.endswith('source-tests.xml') else dist/name
    for name, source in sources.items():
        if not source.is_file() or any(part.is_symlink() for part in (source, *source.parents)):
            raise ValueError('Missing or unsafe release artifact: ' + name)
    # Validate the complete input set before creating any staged output.
    output.mkdir(parents=True, exist_ok=True)
    for name, source in sources.items():
        shutil.copy2(source, output/name)
    require_clean_source(commit)
    provenance = {'schema': 'docxdodyr.build/v1', 'commit': commit, 'version': __version__,
        'target': target, 'channel': channel,
        'source_checkout_policy': 'clean-tracked-and-nonignored-before-and-after-collection; ignored-inputs-not-attested',
        'source_test_scope': 'base-source; packaged/real-Qwen/clean-machine checks require separate native evidence',
        'sha256': {name: compute_sha256(output/name) for name in sorted(sources)}}
    (output/f'{target}-build-provenance.json').write_text(json.dumps(provenance, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--dist', type=Path, default=ROOT/'dist')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--target', choices=TARGETS, required=True)
    p.add_argument('--commit', required=True)
    p.add_argument('--channel', choices=('signed-stable', 'unsigned-preview'), required=True)
    p.add_argument('--junit', type=Path, required=True)
    a = p.parse_args()
    collect(a.dist, a.output, a.target, a.commit, a.channel, a.junit)
