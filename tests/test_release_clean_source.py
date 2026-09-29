"""Exercise the collector's source gate against actual synthetic Git states."""
import subprocess

import pytest

from scripts import collect_release_artifacts as collector


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Synthetic Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / 'source.py').write_text('SYNTHETIC = 1\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'synthetic source')
    monkeypatch.setattr(collector, 'ROOT', root)
    return root, git(root, 'rev-parse', 'HEAD')


@pytest.mark.parametrize('state', ['modified', 'staged', 'untracked', 'deleted'])
def test_dirty_checkout_cannot_be_claimed_as_exact_commit(checkout, tmp_path, state):
    root, commit = checkout
    if state == 'untracked':
        (root / 'new.py').write_text('SYNTHETIC = 2\n')
    elif state == 'deleted':
        (root / 'source.py').unlink()
    else:
        (root / 'source.py').write_text('SYNTHETIC = 2\n')
        if state == 'staged':
            git(root, 'add', '.')
    output = tmp_path / 'stage'
    assert git(root, 'rev-parse', 'HEAD') == commit
    with pytest.raises(ValueError, match='clean source checkout'):
        collector.collect(root, output, 'macos-arm64', commit, 'unsigned-preview', root / 'missing.xml')
    assert not output.exists()


def test_clean_checkout_is_accepted(checkout):
    root, commit = checkout
    collector.require_clean_source(commit)


def test_source_changed_during_copy_cannot_emit_provenance(checkout, tmp_path, monkeypatch):
    root, commit = checkout
    monkeypatch.setattr(collector, 'COMMON_FILES', set())
    monkeypatch.setattr(collector, 'metadata_names', lambda target: set())
    monkeypatch.setattr(collector, 'check', lambda junit: None)
    dist = tmp_path / 'dist'
    dist.mkdir()
    for name in collector.artifact_names('macos-arm64', 'unsigned-preview'):
        (dist / name).write_bytes(b'synthetic')
    original = collector.shutil.copy2
    def copy_and_mutate(source, target):
        original(source, target)
        (root / 'source.py').write_text('SYNTHETIC = 2\n')
    monkeypatch.setattr(collector.shutil, 'copy2', copy_and_mutate)
    output = tmp_path / 'stage'
    with pytest.raises(ValueError, match='clean source checkout'):
        collector.collect(dist, output, 'macos-arm64', commit, 'unsigned-preview', tmp_path / 'unused.xml')
    assert not (output / 'macos-arm64-build-provenance.json').exists()
