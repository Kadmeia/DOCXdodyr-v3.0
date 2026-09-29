# -*- coding: utf-8 -*-
"""Точечные тесты macOS Quick Action/Service моста."""

from __future__ import annotations

import plistlib
from pathlib import Path
import subprocess

import pytest

import macos_context_menu as menu
import version


def _fake_app(tmp_path: Path) -> Path:
    app = tmp_path / "DOCXdodyr.app"
    executable = app / "Contents" / "MacOS" / "DOCXdodyr"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"stub")
    resource = app / "Contents" / "Resources" / "macos_context_menu.py"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(Path(menu.__file__).read_bytes())
    return app


def test_build_command_uses_argv_and_preserves_hostile_finder_names(tmp_path):
    app = _fake_app(tmp_path)
    selected = tmp_path / 'Папка с пробелами; $(touch PWNED)' / 'a "quoted".docx'
    selected.parent.mkdir()
    selected.write_bytes(b"docx")

    command = menu.build_command("anonymize", [selected], app_path=app)

    assert command[:3] == [
        str(app / "Contents" / "MacOS" / "DOCXdodyr"),
        "--anonymize",
        "--no-open-output",
    ]
    assert command[3] == str(selected.absolute())
    assert "$(touch PWNED)" in command[3]
    assert all(isinstance(item, str) for item in command)


def test_launch_wait_uses_shell_false_and_array(monkeypatch, tmp_path):
    app = _fake_app(tmp_path)
    selected = tmp_path / "sample.docx"
    selected.write_bytes(b"docx")
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(menu.subprocess, "run", fake_run)
    assert menu.launch_context_action("restore", [selected], app_path=app, wait=True) == 0
    assert seen["command"][1:3] == ["--restore", "--no-open-output"]
    assert seen["kwargs"]["shell"] is False
    assert seen["kwargs"]["check"] is False


def test_detached_cli_returns_success_instead_of_pid(monkeypatch, tmp_path):
    app = _fake_app(tmp_path)
    selected = tmp_path / "sample.docx"
    selected.write_bytes(b"docx")
    monkeypatch.setattr(menu, "launch_context_action", lambda *args, **kwargs: 65535)
    assert menu.main(["--action", "anonymize", "--app-path", str(app), str(selected)]) == 0


def test_materialized_workflows_are_valid_plists_and_quote_finder_args(tmp_path):
    app = _fake_app(tmp_path)
    bundles = menu.materialize_quick_actions(tmp_path / "Services", app_path=app)
    assert {path.name for path in bundles} == set(menu.WORKFLOW_NAMES.values())

    for bundle in bundles:
        info = plistlib.loads((bundle / "Contents" / "Info.plist").read_bytes())
        document_path = bundle / "Contents" / "Resources" / "document.wflow"
        assert document_path.is_file()
        assert not (bundle / "Contents" / "document.wflow").exists()
        document = plistlib.loads(document_path.read_bytes())
        assert info["CFBundleIdentifier"].startswith("ru.docxdodyr.quick-action.")
        service = info["NSServices"][0]
        assert service["NSMessage"] == "runWorkflowAsService"
        assert service["NSRequiredContext"] == {"NSApplicationIdentifier": "com.apple.finder"}
        assert service["NSSendFileTypes"] == ["public.item"]
        assert document["AMDocumentVersion"] == "2"
        assert document["connectors"] == {}
        metadata = document["workflowMetaData"]
        assert metadata == {
            "serviceApplicationBundleID": "com.apple.finder",
            "serviceApplicationPath": "/System/Library/CoreServices/Finder.app",
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": 0,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        }
        action = document["actions"][0]["action"]
        assert action["AMAccepts"] == {
            "Container": "List",
            "Optional": False,
            "Types": ["com.apple.cocoa.path"],
        }
        assert action["ActionParameters"]["inputMethod"] == 1
        assert action["ActionParameters"]["shell"] == "/bin/zsh"
        script = action["ActionParameters"]["COMMAND_STRING"]
        assert '"$@"' in script
        assert "/Contents/MacOS/DOCXdodyr" in script
        assert "--no-open-output" in script
        assert "--headless" not in script
        assert "/usr/bin/python3" not in script
        assert "eval" not in script
        assert "unquoted" not in script


def test_install_and_uninstall_only_touch_docxdodyr_services(tmp_path):
    home = tmp_path / "home"
    installed = menu.install_quick_actions(home=home, app_path="/Applications/DOCXdodyr.app")
    assert len(installed) == 2
    unrelated = home / "Library" / "Services" / "Other.workflow"
    (unrelated / "Contents").mkdir(parents=True)
    (unrelated / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "example.other"}))

    removed = menu.uninstall_quick_actions(home=home)
    assert {path.name for path in removed} == set(menu.WORKFLOW_NAMES.values())
    assert unrelated.exists()


def test_materialize_replaces_owned_legacy_workflow_without_stale_document(tmp_path):
    app = _fake_app(tmp_path)
    services = tmp_path / "Services"
    menu.materialize_quick_actions(services, app_path=app)
    legacy = services / menu.WORKFLOW_NAMES["anonymize"] / "Contents" / "document.wflow"
    legacy.write_bytes(b"stale legacy workflow")

    menu.materialize_quick_actions(services, app_path=app)

    assert not legacy.exists()
    assert (
        services
        / menu.WORKFLOW_NAMES["anonymize"]
        / "Contents"
        / "Resources"
        / "document.wflow"
    ).is_file()


def test_materialize_does_not_delete_unowned_workflow(tmp_path):
    app = _fake_app(tmp_path)
    services = tmp_path / "Services"
    bundle = services / menu.WORKFLOW_NAMES["anonymize"]
    info = bundle / "Contents" / "Info.plist"
    info.parent.mkdir(parents=True)
    info.write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.example.other"}))

    with pytest.raises(menu.ContextMenuError, match="чужой workflow"):
        menu.materialize_quick_actions(services, app_path=app)

    assert info.exists()


def test_system_uninstall_only_removes_owned_workflows(tmp_path):
    services = tmp_path / "Library" / "Services"
    installed = menu.materialize_quick_actions(services, app_path="/Applications/DOCXdodyr.app")
    unrelated = services / "Other.workflow" / "Contents"
    unrelated.mkdir(parents=True)
    (unrelated / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "example.other"}))
    removed = menu.uninstall_system_quick_actions(root=tmp_path)
    assert {path.name for path in removed} == {path.name for path in installed}
    assert unrelated.parent.exists()


def test_package_script_isolated_and_includes_installer(monkeypatch, tmp_path):
    from scripts import package_macos_quick_actions as packager

    app = _fake_app(tmp_path)
    output = tmp_path / "dist"
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(packager.subprocess, "run", fake_run)
    result = packager.package_quick_actions(app, output, arch="arm64", skip_sign=True)
    assert result.name.endswith("-quick-actions.dmg")
    verify_call = next(command for command in calls if command[:2] == ["codesign", "--verify"])
    create_call = next(command for command in calls if command[:2] == ["hdiutil", "create"])
    assert calls.index(verify_call) < calls.index(create_call)
    assert "--deep" in verify_call and "--strict" in verify_call
    staging = Path(create_call[create_call.index("-srcfolder") + 1])
    assert not staging.exists(), "staging tree must be removed after packaging"
    source_text = Path(packager.__file__).read_text(encoding="utf-8")
    assert "/usr/bin/ditto" in source_text
    assert '"/usr/bin/python3"' not in source_text


def test_pkg_packager_normalizes_app_name_and_uses_pkgbuild_productbuild(monkeypatch, tmp_path):
    from scripts import package_macos_quick_actions_pkg as packager

    source = tmp_path / "DOCXdodyr-arm64.app"
    executable = source / "Contents" / "MacOS" / "DOCXdodyr"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"stub")
    resource = source / "Contents" / "Resources" / "macos_context_menu.py"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(Path(packager.__file__).resolve().parents[1].joinpath("macos_context_menu.py").read_bytes())
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(packager.subprocess, "run", fake_run)
    result = packager.package_quick_actions_pkg(source, tmp_path / "dist", arch="arm64")
    assert result.name == f"DOCXdodyr-{version.__version__}-macos-arm64-quick-actions.pkg"
    verify_call = next(command for command in calls if command[:2] == ["codesign", "--verify"])
    pkgbuild_call = next(command for command in calls if command[0] == "pkgbuild")
    productbuild_call = next(command for command in calls if command[0] == "productbuild")
    assert calls.index(verify_call) < calls.index(pkgbuild_call)
    assert Path(verify_call[-1]).as_posix().endswith("/Applications/DOCXdodyr.app")
    assert pkgbuild_call[pkgbuild_call.index("--install-location") + 1] == "/"
    pkg_root = Path(pkgbuild_call[pkgbuild_call.index("--root") + 1])
    assert not pkg_root.exists(), "PKG staging must be removed"
    assert "--sign" not in pkgbuild_call
    assert "--sign" not in productbuild_call
    source_text = Path(packager.__file__).read_text(encoding="utf-8")
    assert 'stage / "Applications" / "DOCXdodyr.app"' in source_text
    assert 'stage / "Library" / "Services"' in source_text


def test_dmg_packager_normalizes_arch_named_app(monkeypatch, tmp_path):
    from scripts import package_macos_quick_actions as packager

    source = tmp_path / "DOCXdodyr-x86_64.app"
    executable = source / "Contents" / "MacOS" / "DOCXdodyr"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"stub")
    launcher = source / "Contents" / "Resources" / "macos_context_menu.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(Path(menu.__file__).read_bytes())
    calls = []
    monkeypatch.setattr(
        packager.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0),
    )
    monkeypatch.setattr(packager.os, "symlink", lambda src, dst, *args, **kwargs: Path(dst).touch())
    packager.package_quick_actions(source, tmp_path / "dist", arch="x86_64", skip_sign=True)
    verify_call = next(command for command in calls if command[:2] == ["codesign", "--verify"])
    assert Path(verify_call[-1]).as_posix().endswith("/DOCXdodyr.app")


def test_pkg_packager_marks_unsigned_preview_and_rejects_wrong_signer(monkeypatch, tmp_path):
    from scripts import package_macos_quick_actions_pkg as packager

    source = tmp_path / "DOCXdodyr-arm64.app"
    executable = source / "Contents" / "MacOS" / "DOCXdodyr"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"stub")
    resource = source / "Contents" / "Resources" / "macos_context_menu.py"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(Path(packager.__file__).resolve().parents[1].joinpath("macos_context_menu.py").read_bytes())
    calls = []
    monkeypatch.setattr(
        packager.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0),
    )
    result = packager.package_quick_actions_pkg(
        source, tmp_path / "dist", arch="arm64", development=True
    )
    assert result.name == f"DOCXdodyr-{version.__version__}-macos-arm64-development-quick-actions.pkg"
    with pytest.raises(ValueError, match="Developer ID Installer"):
        packager.package_quick_actions_pkg(
            source, tmp_path / "dist-2", arch="arm64", sign_identity="Developer ID Application: Wrong"
        )


def test_main_help_documents_no_open_output():
    source = Path(menu.__file__).resolve().parent / "main.py"
    assert "--no-open-output" in source.read_text(encoding="utf-8")


def test_macos_builder_embeds_launcher_before_packager_verification():
    build_source = (Path(menu.__file__).resolve().parent / "scripts" / "build_macos.py").read_text(encoding="utf-8")
    copy_marker = "context_menu_target = resources_dir / context_menu_source.name"
    verify_marker = "verify_macos_arch(intermediate_app_path, arch)"
    assert copy_marker in build_source
    assert verify_marker in build_source
    assert build_source.index(copy_marker) < build_source.index(verify_marker)
