import json
from pathlib import Path
import shutil
import sys
import pytest
import yaml
from scripts import package_release, merge_release_evidence, collect_release_artifacts, prepare_ci_signing
from tests.test_production_gate import evidence_set


def test_two_phase_workflow_has_no_publication():
    data = yaml.load((Path(__file__).resolve().parents[1]/'.github/workflows/release.yml').read_text(), Loader=yaml.BaseLoader)
    assert set(data['on']) == {'workflow_dispatch'}
    assert set(data['jobs']) == {'build', 'validate'}
    assert data['on']['workflow_dispatch']['inputs']['channel']['default'] == 'unsigned-preview'
    downloads = [s for s in data['jobs']['validate']['steps'] if 'download-artifact@' in s.get('uses', '')]
    assert len(downloads) == 2 and all(s['with'].get('run-id') for s in downloads)
    assert data['permissions']['contents'] == 'read'
    for job in data['jobs'].values():
        assert 'github.ref_protected' in job['if']
        assert "vars.RELEASE_PROTECTED_REF != ''" in job['if']
        assert 'github.ref == vars.RELEASE_PROTECTED_REF' in job['if']
        checkout = next(step for step in job['steps'] if 'actions/checkout@' in step.get('uses', ''))
        assert checkout['with']['ref'] == '${{ github.sha }}'


def test_macos_build_uses_native_arch_named_bundle():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/release.yml').read_text()
    assert 'test "$(uname -m)" = "$ARCH"' in workflow
    assert 'platform.machine()' in workflow
    assert 'package_macos_quick_actions_pkg.py' in workflow
    assert 'MACOS_INSTALLER_SIGNING_IDENTITY' in workflow
    assert 'Developer ID Installer:' in workflow
    assert 'package_release.py --package --arch "$ARCH"' in workflow
    assert 'dist/DOCXdodyr-$ARCH.app' in workflow


def test_package_production_flag_reaches_packager(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(sys, 'argv', ['package', '--package', '--production'])
    monkeypatch.setattr(package_release, 'package_macos_zip', lambda root, production: seen.append(production) or tmp_path/'synthetic.zip')
    monkeypatch.setattr(package_release, 'generate_sha256sums', lambda root: tmp_path/'SHA256SUMS.txt')
    assert package_release.main() == 0
    assert seen == [True]


def test_explicit_package_directory_reaches_zip_and_checksums(tmp_path, monkeypatch):
    seen = []
    candidate = tmp_path / 'isolated-dist'
    monkeypatch.setattr(sys, 'argv', ['package', '--package', '--development', '--dist-dir', str(candidate)])
    monkeypatch.setattr(package_release, 'package_macos_zip',
                        lambda root, production: seen.append(('zip', root, production)) or candidate / 'synthetic.zip')
    monkeypatch.setattr(package_release, 'generate_sha256sums',
                        lambda root: seen.append(('sums', root)) or candidate / 'SHA256SUMS.txt')
    assert package_release.main() == 0
    assert seen == [('zip', candidate, False), ('sums', candidate)]


def test_local_signing_provisioning_is_forbidden(monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    with pytest.raises(RuntimeError, match='restricted'):
        prepare_ci_signing.prepare('macos-arm64')


def test_merge_preserves_exact_build_and_evidence(tmp_path):
    build = tmp_path/'build'
    build.mkdir()
    commit = evidence_set(build)
    evidence = tmp_path/'evidence'
    evidence.mkdir()
    (build/'SHA256SUMS.txt').unlink()
    for file in build.glob('*-production-evidence.json'):
        file.rename(evidence/file.name)
    output = tmp_path/'validated'
    merge_release_evidence.merge(build, evidence, output, commit)
    assert package_release.verify_sha256sums(output/'SHA256SUMS.txt', output)['valid']


def test_evidence_cannot_replace_build_binaries(tmp_path):
    build = tmp_path/'build'; build.mkdir()
    evidence = tmp_path/'evidence'; evidence.mkdir()
    (evidence/'arbitrary.exe').write_bytes(b'synthetic')
    with pytest.raises(ValueError, match='cannot supply build artifacts'):
        merge_release_evidence.merge(build, evidence, tmp_path/'out', '1'*40)


@pytest.mark.parametrize('kind', ['same-input', 'nested-input', 'nested-output', 'output-parent', 'symlink-input', 'symlink-output'])
def test_merge_rejects_unsafe_roots_before_writing(tmp_path, kind):
    build = tmp_path / 'build'; build.mkdir()
    evidence = tmp_path / 'evidence'; evidence.mkdir()
    output = tmp_path / 'out'
    if kind == 'same-input':
        evidence = build
    elif kind == 'nested-input':
        evidence = build / 'evidence'; evidence.mkdir()
    elif kind == 'nested-output':
        output = build / 'stage'
    elif kind == 'output-parent':
        output = tmp_path
    elif kind == 'symlink-input':
        try:
            link = tmp_path / 'linked-build'; link.symlink_to(build, target_is_directory=True)
        except OSError:
            pytest.skip("Symlink creation requires administrative privileges on Windows")
        build = link
    else:
        target = tmp_path / 'target'; target.mkdir()
        try:
            output.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("Symlink creation requires administrative privileges on Windows")
    before = {str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*')}
    with pytest.raises(ValueError, match='overlap|symlink|regular directory'):
        merge_release_evidence.merge(build, evidence, output, '1' * 40)
    assert before == {str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*')}


def test_collect_requires_source_commit_match(tmp_path, monkeypatch):
    monkeypatch.setattr(collect_release_artifacts.subprocess, 'check_output', lambda *a, **k: '2'*40)
    with pytest.raises(ValueError, match='Checkout differs'):
        collect_release_artifacts.collect(tmp_path, tmp_path/'out', 'macos-arm64', '1'*40, 'signed-stable', tmp_path/'missing.xml')


def test_collect_emits_bound_inventory_without_approval(tmp_path, monkeypatch):
    build = tmp_path/'build'; build.mkdir()
    commit = evidence_set(build)
    monkeypatch.setattr(collect_release_artifacts, 'ROOT', build)
    monkeypatch.setattr(collect_release_artifacts.subprocess, 'check_output',
                        lambda args, **k: '' if args[1] == 'status' else commit)
    output = tmp_path/'stage'
    collect_release_artifacts.collect(build, output, 'macos-arm64', commit, 'signed-stable', build/'macos-arm64-source-tests.xml')
    provenance = json.loads((output/'macos-arm64-build-provenance.json').read_text())
    assert provenance['commit'] == commit
    assert provenance['source_checkout_policy'].startswith('clean-tracked-and-nonignored')
    assert not list(output.glob('*production-evidence*'))
    for name, digest in provenance['sha256'].items():
        assert package_release.compute_sha256(output/name) == digest


def test_preview_dmg_does_not_call_codesign(tmp_path, monkeypatch):
    from scripts import build_macos
    app = tmp_path/'synthetic.app'; app.mkdir()
    (app/'content').write_text('synthetic')
    monkeypatch.setattr(build_macos, 'DIST_DIR', tmp_path)
    monkeypatch.setattr(build_macos, 'verify_macos_arch', lambda *a: 'arm64')
    commands = []
    def run(args, **kwargs):
        commands.append(args)
        if args[:2] == ['hdiutil', 'create']:
            Path(args[-1]).write_bytes(b'synthetic dmg')
    monkeypatch.setattr(build_macos, 'run_command', run)
    import os
    monkeypatch.setattr(os, 'symlink', lambda *a, **k: None)
    result = build_macos.build_dmg(app, skip_sign=True)
    assert result.parent == tmp_path
    assert not any(command[0] == 'codesign' for command in commands)
    with pytest.raises(ValueError, match='cannot skip'):
        build_macos.build_dmg(app, production=True, skip_sign=True)
