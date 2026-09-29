"""Synthetic repositories only: scanner evidence must not contain secret values."""
import json
import subprocess

import pytest

from scripts import scan_release_sources as scanner


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)


def test_history_finds_removed_secret_without_exporting_it(tmp_path):
    git(tmp_path, 'init', '-q')
    git(tmp_path, 'config', 'user.name', 'Synthetic Fixture')
    git(tmp_path, 'config', 'user.email', 'fixture@example.invalid')
    secret = 'ghp_' + 'X' * 36
    (tmp_path / 'sample.txt').write_text('synthetic\n' + secret + '\n')
    (tmp_path / 'binary.dat').write_bytes(b'\0' + secret.encode())
    try:
        (tmp_path / 'link').symlink_to('sample.txt')
    except OSError:
        pytest.skip("Symlink creation requires administrative privileges on Windows")
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'synthetic baseline')
    first = git(tmp_path, 'rev-parse', 'HEAD').decode().strip()
    (tmp_path / 'sample.txt').write_text('synthetic cleaned\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-qm', 'synthetic cleanup')
    before = git(tmp_path, 'status', '--porcelain')
    report = scanner.scan_history(tmp_path)
    assert report['commits'] == 2
    assert len(report['findings']) == 1
    hit = report['findings'][0]
    assert (hit['file'], hit['commit'], hit['line'], hit['rule']) == (
        'sample.txt', first, 2, 'github-token')
    assert secret not in json.dumps(report)
    assert {'binary.dat', 'link'} == {item['file'] for item in report['skipped']}
    assert git(tmp_path, 'status', '--porcelain') == before


def test_worktree_scan_does_not_follow_symlinked_parent(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'private.txt').write_text('ghp_' + 'Y' * 36)
    try:
        (root / 'linked').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation requires administrative privileges on Windows")
    monkeypatch.setattr(scanner, 'ROOT', root)
    monkeypatch.setattr(scanner.subprocess, 'check_output', lambda *a, **k: b'linked/private.txt\0')
    report = scanner.scan()
    assert report['files_hashed'] == 0
    assert report['findings'] == []
    assert len(report['skipped']) == 1
