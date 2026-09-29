"""Поведенческие проверки изоляции preview macOS build без запуска PyInstaller."""

from pathlib import Path
import os
import sys

import pytest

from scripts import build_macos
import version


@pytest.mark.parametrize('extra', [['--skip-sign'], ['--identity', 'Synthetic identity'],
                                  ['--production'], ['--notarize'], ['--skip-build']])
def test_adhoc_preview_rejects_conflicting_modes_before_actions(tmp_path, monkeypatch, extra):
    monkeypatch.setattr(sys, 'argv', ['build', '--output-root', str(tmp_path / 'preview'),
                                   '--adhoc-preview', *extra])
    monkeypatch.setattr(build_macos, 'ensure_assets', lambda: pytest.fail('must reject before build actions'))
    with pytest.raises(SystemExit) as error:
        build_macos.main()
    assert error.value.code == 2


def test_adhoc_preview_uses_explicit_identity_and_never_notarizes(tmp_path, monkeypatch):
    root = tmp_path / 'preview'
    app = root / 'dist' / build_macos.architecture_bundle_name('arm64')
    monkeypatch.setattr(sys, 'argv', ['build', '--output-root', str(root), '--adhoc-preview'])
    monkeypatch.setenv('APPLE_SIGNING_IDENTITY', 'Developer ID Application: Synthetic')
    monkeypatch.setattr(build_macos, 'ensure_assets', lambda: None)
    monkeypatch.setattr(build_macos, 'build_app', lambda **kwargs: app)
    seen = []
    monkeypatch.setattr(build_macos, 'sign_app', lambda path, identity: seen.append(('sign', path, identity)))
    monkeypatch.setattr(build_macos, 'verify_macos_arch', lambda *args: 'arm64')
    monkeypatch.setattr(build_macos, 'verify_app', lambda path: {})
    def dmg(path, **kwargs):
        seen.append(('dmg', kwargs['skip_sign'], kwargs['dist_dir']))
        return root / 'synthetic.dmg'
    monkeypatch.setattr(build_macos, 'build_dmg', dmg)
    monkeypatch.setattr(build_macos, 'notarize_dmg', lambda *args: pytest.fail('notarization forbidden'))
    build_macos.main()
    assert seen == [('sign', app, '-'), ('dmg', True, root / 'dist')]


def _sandbox(monkeypatch, tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(build_macos, "REPO_ROOT", repo)
    monkeypatch.setattr(build_macos, "DIST_DIR", repo / "dist")
    monkeypatch.setattr(build_macos, "BUILD_DIR", repo / "build")
    return repo


def test_preview_root_is_created_with_private_dist_and_build(monkeypatch, tmp_path):
    _sandbox(monkeypatch, tmp_path)

    dist, build = build_macos._prepare_output_root(tmp_path / "preview")

    assert dist == tmp_path / "preview" / "dist"
    assert build == tmp_path / "preview" / "build"
    assert dist.parent.is_dir()


def test_preview_root_rejects_nonempty_directory(monkeypatch, tmp_path):
    _sandbox(monkeypatch, tmp_path)
    root = tmp_path / "preview"
    root.mkdir()
    (root / "unrelated-output").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="fresh and empty"):
        build_macos._prepare_output_root(root)


def test_preview_root_rejects_symlink(monkeypatch, tmp_path):
    _sandbox(monkeypatch, tmp_path)
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "preview-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation requires administrative privileges on Windows")

    with pytest.raises(ValueError, match="symlinks"):
        build_macos._prepare_output_root(link)


def test_preview_root_rejects_symlink_ancestor(monkeypatch, tmp_path):
    _sandbox(monkeypatch, tmp_path)
    target = tmp_path / "real"
    target.mkdir()
    parent = tmp_path / "link-parent"
    try:
        parent.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation requires administrative privileges on Windows")

    with pytest.raises(ValueError, match="ancestors"):
        build_macos._prepare_output_root(parent / "preview")


def test_preview_root_rejects_repository_overlap(monkeypatch, tmp_path):
    repo = _sandbox(monkeypatch, tmp_path)
    (repo / "web").mkdir()
    (repo / "web" / "index.html").write_text("<html></html>", encoding="utf-8")
    with pytest.raises(ValueError, match="overlaps"):
        build_macos._prepare_output_root(repo / "nested-preview")


def test_preview_root_allows_fresh_release_audit_child(monkeypatch, tmp_path):
    repo = _sandbox(monkeypatch, tmp_path)

    dist, build = build_macos._prepare_output_root(repo / ".release-audit" / "preview")

    assert dist.parent.is_dir()
    assert dist == repo / '.release-audit' / 'preview' / 'dist'
    assert build == repo / '.release-audit' / 'preview' / 'build'


def test_build_app_routes_fake_pyinstaller_to_preview_and_preserves_legacy(monkeypatch, tmp_path):
    repo = _sandbox(monkeypatch, tmp_path)
    (repo / "web").mkdir()
    (repo / "web" / "index.html").write_text("<html></html>", encoding="utf-8")
    (repo / "macos_context_menu.py").write_text("# synthetic quick action launcher\n", encoding="utf-8")
    legacy_dist = repo / "dist"
    legacy_build = repo / "build"
    legacy_dist.mkdir()
    legacy_build.mkdir()
    sentinel_dist = legacy_dist / "sentinel"
    sentinel_build = legacy_build / "sentinel"
    sentinel_dist.write_text("keep", encoding="utf-8")
    sentinel_build.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(build_macos, "ASSETS_DIR", Path(__file__).resolve().parents[1] / "assets")
    monkeypatch.setattr(build_macos, "SPEC_FILE", repo / "DOCXdodyr.spec")
    monkeypatch.setattr(build_macos.sys, "version_info", (3, 11, 0))
    monkeypatch.setattr(build_macos.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(build_macos, "verify_macos_arch", lambda *args: "arm64")
    commands = []
    config_dirs = []

    def fake_run(command, check=True, cwd=build_macos.REPO_ROOT):
        commands.append(command)
        if "PyInstaller" in command:
            config_dirs.append(Path(os.environ["PYINSTALLER_CONFIG_DIR"]))
        if "PyInstaller" in command:
            dist = Path(command[command.index("--distpath") + 1])
            app = dist / build_macos.APP_BUNDLE_NAME
            (app / "Contents" / "MacOS").mkdir(parents=True)
            (app / "Contents" / "Resources").mkdir()
            executable = app / "Contents" / "MacOS" / build_macos.APP_NAME
            executable.write_bytes(b"synthetic")
            executable.chmod(0o755)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(build_macos, "run_command", fake_run)
    preview = repo / ".release-audit" / "preview-macos-arm64-20260916"

    app = build_macos.build_app(output_root=preview, arch="arm64")

    assert app == preview / "dist" / build_macos.architecture_bundle_name("arm64")
    assert not (preview / "dist" / build_macos.APP_BUNDLE_NAME).exists()
    assert config_dirs == [preview / "build-pyinstaller-config"]
    assert "PYINSTALLER_CONFIG_DIR" not in os.environ
    assert (app / "Contents" / "Resources" / "macos_context_menu.py").read_text(encoding="utf-8") == "# synthetic quick action launcher\n"
    pyinstaller = next(command for command in commands if "PyInstaller" in command)
    assert pyinstaller[pyinstaller.index("--distpath") + 1] == str(preview / "dist")
    assert pyinstaller[pyinstaller.index("--workpath") + 1] == str(preview / "build")
    assert sentinel_dist.read_text(encoding="utf-8") == "keep"
    assert sentinel_build.read_text(encoding="utf-8") == "keep"


def test_preview_root_does_not_remove_legacy_dist_or_build(monkeypatch, tmp_path):
    repo = _sandbox(monkeypatch, tmp_path)
    legacy_dist = repo / "dist"
    legacy_build = repo / "build"
    legacy_dist.mkdir()
    legacy_build.mkdir()
    (legacy_dist / "old.app").write_text("preserve", encoding="utf-8")
    (legacy_build / "old.txt").write_text("preserve", encoding="utf-8")

    build_macos._prepare_output_root(tmp_path / "preview")

    assert (legacy_dist / "old.app").read_text(encoding="utf-8") == "preserve"
    assert (legacy_build / "old.txt").read_text(encoding="utf-8") == "preserve"


def test_dmg_stages_architecture_bundle_under_canonical_install_name(monkeypatch, tmp_path):
    app = tmp_path / build_macos.architecture_bundle_name("arm64")
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / build_macos.APP_NAME).write_bytes(b"synthetic")
    seen = {}

    monkeypatch.setattr(build_macos, "verify_macos_arch", lambda path, arch: arch)

    def fake_run(command, **kwargs):
        if command[:2] == ["hdiutil", "create"]:
            staging = Path(command[command.index("-srcfolder") + 1])
            seen["entries"] = {entry.name for entry in staging.iterdir()}
            Path(command[-1]).write_bytes(b"synthetic dmg")
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(build_macos, "run_command", fake_run)
    monkeypatch.setattr(build_macos.os, "symlink", lambda src, dst: Path(dst).touch())
    result = build_macos.build_dmg(app, arch="arm64", skip_sign=True, dist_dir=tmp_path)

    assert result.name == f"DOCXdodyr-{version.__version__}-macos-arm64-development.dmg"
    assert seen["entries"] == {build_macos.APP_BUNDLE_NAME, "Applications"}
