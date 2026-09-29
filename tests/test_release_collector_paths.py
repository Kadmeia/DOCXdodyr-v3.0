"""Collector rejects redirected/overlapping roots before writing artifacts."""
from pathlib import Path

import pytest

from scripts import collect_release_artifacts as collector


@pytest.mark.parametrize('kind', ['same', 'nested', 'parent', 'linked-input',
                                  'linked-output', 'linked-parent', 'linked-junit'])
def test_unsafe_collection_paths_leave_destination_untouched(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(collector, 'require_clean_source', lambda commit: None)
    root = tmp_path / 'source'; root.mkdir()
    monkeypatch.setattr(collector, 'ROOT', root)
    dist = tmp_path / 'dist'; dist.mkdir()
    output = tmp_path / 'stage'
    junit = tmp_path / 'tests.xml'; junit.write_text('synthetic')
    try:
        if kind == 'same':
            output = dist
        elif kind == 'nested':
            output = dist / 'stage'
        elif kind == 'parent':
            output = tmp_path
        elif kind == 'linked-input':
            link = tmp_path / 'input-link'; link.symlink_to(dist, target_is_directory=True)
            dist = link
        elif kind == 'linked-output':
            target = tmp_path / 'target'; target.mkdir()
            output.symlink_to(target, target_is_directory=True)
        elif kind == 'linked-parent':
            target = tmp_path / 'target'; target.mkdir()
            link = tmp_path / 'parent-link'; link.symlink_to(target, target_is_directory=True)
            output = link / 'stage'
        else:
            link = tmp_path / 'junit-link'; link.symlink_to(junit)
            junit = link
    except OSError:
        pytest.skip("Symlink creation requires administrative privileges on Windows")
    before = set(tmp_path.rglob('*'))
    with pytest.raises(ValueError, match='symlink|overlap'):
        collector.collect(dist, output, 'macos-arm64', 'a' * 40, 'unsigned-preview', junit)
    assert set(tmp_path.rglob('*')) == before


def test_missing_artifact_does_not_create_partial_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, 'require_clean_source', lambda commit: None)
    monkeypatch.setattr(collector, 'check', lambda junit: None)
    monkeypatch.setattr(collector, 'COMMON_FILES', set())
    monkeypatch.setattr(collector, 'metadata_names', lambda target: set())
    dist = tmp_path / 'dist'; dist.mkdir()
    output = tmp_path / 'stage'
    with pytest.raises(ValueError, match='Missing or unsafe'):
        collector.collect(dist, output, 'macos-arm64', 'a' * 40, 'unsigned-preview', tmp_path / 'test.xml')
    assert not output.exists()
