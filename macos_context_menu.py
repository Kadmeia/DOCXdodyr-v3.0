#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""Безопасный мост macOS Quick Action → установленный DOCXdodyr.

Automator передаёт выбранные Finder-пути в shell-обёртку workflow как
отдельные аргументы (``"$@"``), после чего workflow запускает frozen-бинарник
напрямую. CLI этого модуля также валидирует пути и запускает бинарник через
``subprocess`` со списком аргументов и ``shell=False``. Пользовательский путь
никогда не интерполируется в shell-команду.

Модуль не импортирует backend приложения и поэтому пригоден для запуска из
системного ``/usr/bin/python3`` на чистой macOS. Он также материализует два
стандартных Automator workflow-бандла в ``~/Library/Services``.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
from typing import Iterable, Mapping, Sequence


APP_NAME = "DOCXdodyr"
APP_BUNDLE_NAME = f"{APP_NAME}.app"
DEFAULT_APP_PATH = Path("/Applications") / APP_BUNDLE_NAME
SERVICE_DIR_NAME = "Services"
WORKFLOW_NAMES = {
    "anonymize": "Обезличить DOCXdodyr.workflow",
    "restore": "Восстановить DOCXdodyr.workflow",
}
ACTION_FLAGS = {
    "anonymize": "--anonymize",
    "restore": "--restore",
}


class ContextMenuError(RuntimeError):
    """Ошибка подготовки или запуска Finder-действия."""


def _resource_root() -> Path:
    return Path(__file__).resolve().parent


def _candidate_app_paths(app_path: str | os.PathLike[str] | None = None) -> Iterable[Path]:
    if app_path:
        yield Path(app_path).expanduser()
    env_path = os.environ.get("DOCXDODYR_APP_PATH")
    if env_path:
        yield Path(env_path).expanduser()

    # When this module is bundled under Contents/Resources, parents[2] is the
    # containing .app. This lets a copied workflow keep working if the app is
    # installed outside /Applications.
    local = _resource_root()
    for parent in (local, *local.parents):
        if parent.name == APP_BUNDLE_NAME:
            yield parent
    yield DEFAULT_APP_PATH


def resolve_app_bundle(app_path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve an installed DOCXdodyr.app and reject non-bundles."""
    seen: set[Path] = set()
    for candidate in _candidate_app_paths(app_path):
        candidate = candidate.resolve(strict=False)
        if candidate in seen:
            continue
        seen.add(candidate)
        executable = candidate / "Contents" / "MacOS" / APP_NAME
        if candidate.name == APP_BUNDLE_NAME and executable.is_file():
            return candidate
    raise ContextMenuError(
        "DOCXdodyr.app не найден. Установите приложение в /Applications "
        "или задайте DOCXDODYR_APP_PATH."
    )


def normalize_paths(paths: Sequence[str | os.PathLike[str]]) -> list[str]:
    """Проверить и нормализовать существующие Finder-пути.

    Путь возвращается как абсолютная строка без раскрытия symlink. Это важно
    для Finder security-scoped ресурсов и не меняет объект, выбранный юзером.
    """
    result: list[str] = []
    for raw in paths:
        value = os.fspath(raw)
        if not value or value.startswith("-"):
            continue
        path = Path(value).expanduser()
        if not path.exists():
            raise ContextMenuError(f"Путь не существует: {value}")
        absolute = str(path.absolute())
        if absolute not in result:
            result.append(absolute)
    if not result:
        raise ContextMenuError("Finder не передал файлов или папок для обработки.")
    return result


def build_command(
    action: str,
    paths: Sequence[str | os.PathLike[str]],
    *,
    app_path: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Build an argv vector for the frozen binary; never returns a shell string."""
    if action not in ACTION_FLAGS:
        raise ContextMenuError(f"Неизвестное действие Quick Action: {action}")
    app = resolve_app_bundle(app_path)
    normalized = normalize_paths(paths)
    executable = app / "Contents" / "MacOS" / APP_NAME
    return [
        str(executable),
        ACTION_FLAGS[action],
        "--no-open-output",
        *normalized,
    ]


def launch_context_action(
    action: str,
    paths: Sequence[str | os.PathLike[str]],
    *,
    app_path: str | os.PathLike[str] | None = None,
    wait: bool = False,
    env: Mapping[str, str] | None = None,
) -> int:
    """Launch the operation in the background and return its PID or exit code."""
    command = build_command(action, paths, app_path=app_path)
    app = Path(command[0]).parents[2]
    process_env = os.environ.copy()
    if env:
        process_env.update(env)

    if wait:
        completed = subprocess.run(
            command,
            cwd=str(app),
            env=process_env,
            check=False,
            shell=False,
        )
        return completed.returncode

    process = subprocess.Popen(
        command,
        cwd=str(app),
        env=process_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        shell=False,
    )
    return process.pid


def _workflow_document(action: str, launcher_path: Path) -> dict:
    """Return the minimal Automator document.wflow property-list structure."""
    if action not in ACTION_FLAGS:
        raise ContextMenuError(f"Неизвестное действие Quick Action: {action}")
    # Automator's Run Shell Script action is only a transport boundary. It
    # preserves Finder arguments with "$@" and invokes the already-installed
    # frozen binary directly. Do not route this through /usr/bin/python3:
    # modern macOS installations are not required to ship system Python.
    executable = launcher_path / "Contents" / "MacOS" / APP_NAME
    script = (
        f'exec {shlex_quote(str(executable))} {ACTION_FLAGS[action]} '
        '--no-open-output -- "$@"\n'
    )
    # This is the on-disk schema written by Automator for a Service.  The
    # older hand-written plist (AMDocumentVersion 1 + INPUT_METHOD) can be
    # opened by ``automator`` but is not registered by Finder and is treated
    # as an empty workflow.  Keep the action's input as a list of Cocoa paths
    # and pass those paths to the shell action as argv (inputMethod=1).
    action_uuid = {
        "InputUUID": "C4C44192-33E8-44B4-A112-34B123F72B6B",
        "OutputUUID": "F17AB958-FBF0-4981-BC35-F65D339BEEF3",
        "UUID": "1D1ED2AB-83CF-4826-AC1B-2C117AC5F9B3",
    }
    run_shell_action = {
        "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
        "ActionName": "Run Shell Script",
        "ActionParameters": {
            "COMMAND_STRING": script.rstrip("\n"),
            "CheckedForUserDefaultShell": True,
            "inputMethod": 1,
            "shell": "/bin/zsh",
            "source": "",
        },
        "AMAccepts": {
            "Container": "List",
            "Optional": False,
            "Types": ["com.apple.cocoa.path"],
        },
        "AMActionVersion": "2.0.3",
        "AMApplication": ["Automator"],
        "AMParameterProperties": {
            "COMMAND_STRING": {},
            "CheckedForUserDefaultShell": {},
            "inputMethod": {},
            "shell": {},
            "source": {},
        },
        "AMProvides": {
            "Container": "List",
            "Types": ["com.apple.cocoa.string"],
        },
        "BundleIdentifier": "com.apple.RunShellScript",
        "CanShowSelectedItemsWhenRun": False,
        "CanShowWhenRun": True,
        "Category": ["AMCategoryUtilities"],
        "CFBundleVersion": "2.0.3",
        "Class Name": "RunShellScriptAction",
        "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
        "UnlocalizedApplications": ["Automator"],
        "arguments": {
            "0": {
                "default value": 1,
                "name": "inputMethod",
                "required": "0",
                "type": "0",
                "uuid": "0",
            },
            "1": {
                "default value": "",
                "name": "source",
                "required": "0",
                "type": "0",
                "uuid": "1",
            },
            "2": {
                "default value": True,
                "name": "CheckedForUserDefaultShell",
                "required": "0",
                "type": "0",
                "uuid": "2",
            },
            "3": {
                "default value": "",
                "name": "COMMAND_STRING",
                "required": "0",
                "type": "0",
                "uuid": "3",
            },
            "4": {
                "default value": "/bin/sh",
                "name": "shell",
                "required": "0",
                "type": "0",
                "uuid": "4",
            },
        },
        "isViewVisible": True,
        "location": "309.500000:631.000000",
        "nibPath": "/System/Library/Automator/Run Shell Script.action/Contents/Resources/en.lproj/main.nib",
        **action_uuid,
    }
    return {
        "AMApplicationBuild": "346",
        "AMApplicationVersion": "2.3",
        "AMDocumentVersion": "2",
        "actions": [{"action": run_shell_action, "isViewVisible": True}],
        "connectors": {},
        "workflowMetaData": {
            "serviceApplicationBundleID": "com.apple.finder",
            "serviceApplicationPath": "/System/Library/CoreServices/Finder.app",
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": 0,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }


def shlex_quote(value: str) -> str:
    """POSIX-quote only the trusted launcher path/action, never Finder input."""
    return "'" + value.replace("'", "'\\''") + "'"


def materialize_quick_actions(
    destination: str | os.PathLike[str],
    *,
    app_path: str | os.PathLike[str] = DEFAULT_APP_PATH,
    launcher_path: str | os.PathLike[str] | None = None,
) -> list[Path]:
    """Create the two installable ``.workflow`` bundles in *destination*."""
    target = Path(destination).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    launcher = Path(launcher_path) if launcher_path else (
        Path(app_path) / "Contents" / "Resources" / "macos_context_menu.py"
    )
    # ``launcher_path`` is retained for callers that also package the Python
    # installer helper. The workflow itself points to the frozen executable,
    # so derive the containing .app without reading or embedding user input.
    app_bundle = Path(app_path)
    if launcher.name == "macos_context_menu.py" and launcher.parent.name == "Resources":
        app_bundle = launcher.parents[2]
    created: list[Path] = []
    for action, workflow_name in WORKFLOW_NAMES.items():
        bundle = target / workflow_name
        if bundle.exists():
            info_path = bundle / "Contents" / "Info.plist"
            owned = False
            if info_path.is_file():
                try:
                    existing_info = plistlib.loads(info_path.read_bytes())
                    owned = str(existing_info.get("CFBundleIdentifier", "")).startswith(
                        "ru.docxdodyr.quick-action."
                    )
                except Exception:
                    owned = False
            # Versions before the Service schema fix had no ownership marker
            # but used this exact bundle name and legacy document location.
            # Only recognize that known Run Shell Script layout; never remove
            # an unrelated workflow merely because its display name matches.
            legacy_document = bundle / "Contents" / "document.wflow"
            if not owned and legacy_document.is_file():
                try:
                    legacy = plistlib.loads(legacy_document.read_bytes())
                    legacy_actions = legacy.get("actions", [])
                    owned = any(
                        item.get("action", {}).get("BundleIdentifier") == "com.apple.RunShellScript"
                        for item in legacy_actions
                        if isinstance(item, dict)
                    )
                except Exception:
                    owned = False
            if owned:
                shutil.rmtree(bundle)
            else:
                raise ContextMenuError(
                    f"Не удалось безопасно заменить чужой workflow: {bundle}"
                )
        contents = bundle / "Contents"
        resources = contents / "Resources"
        resources.mkdir(parents=True, exist_ok=True)
        info = {
            "CFBundleDevelopmentRegion": "en_US",
            "CFBundleDisplayName": workflow_name.removesuffix(".workflow"),
            "CFBundleIdentifier": f"ru.docxdodyr.quick-action.{action}",
            "CFBundleName": workflow_name.removesuffix(".workflow"),
            "CFBundlePackageType": "BNDL",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "1",
            # Finder discovers Automator Services through NSServices in the
            # bundle Info.plist. workflowMetaData alone is not sufficient.
            "NSServices": [
                {
                    "NSMenuItem": {"default": workflow_name.removesuffix(".workflow")},
                    "NSMessage": "runWorkflowAsService",
                    "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"},
                    "NSSendFileTypes": ["public.item"],
                }
            ],
        }
        (contents / "Info.plist").write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_XML, sort_keys=False))
        document = _workflow_document(action, app_bundle)
        # Automator only loads Service documents from Contents/Resources. A
        # document directly under Contents may look like a plist but is not a
        # registered workflow (automator returns OSStatus -10813).
        (resources / "document.wflow").write_bytes(
            plistlib.dumps(document, fmt=plistlib.FMT_XML, sort_keys=False)
        )
        created.append(bundle)
    return created


def install_quick_actions(
    *,
    home: str | os.PathLike[str] | None = None,
    app_path: str | os.PathLike[str] = DEFAULT_APP_PATH,
) -> list[Path]:
    """Install workflows into the per-user Services directory."""
    root = Path(home).expanduser() if home else Path.home()
    return materialize_quick_actions(root / "Library" / SERVICE_DIR_NAME, app_path=app_path)


def uninstall_quick_actions(*, home: str | os.PathLike[str] | None = None) -> list[Path]:
    """Remove only DOCXdodyr-owned workflows from the per-user Services dir."""
    root = Path(home).expanduser() if home else Path.home()
    removed: list[Path] = []
    for name in WORKFLOW_NAMES.values():
        path = root / "Library" / SERVICE_DIR_NAME / name
        if path.is_dir() and (path / "Contents" / "Info.plist").is_file():
            path_info = plistlib.loads((path / "Contents" / "Info.plist").read_bytes())
            if str(path_info.get("CFBundleIdentifier", "")).startswith("ru.docxdodyr.quick-action."):
                shutil.rmtree(path)
                removed.append(path)
    return removed


def uninstall_system_quick_actions(*, root: str | os.PathLike[str] = "/") -> list[Path]:
    """Remove only DOCXdodyr workflows from a package-rooted Services dir.

    ``root`` is injectable for tests and package uninstallers. The default is
    the real system root and therefore requires the caller to have permission
    to modify ``/Library/Services``.
    """
    services = Path(root).expanduser() / "Library" / SERVICE_DIR_NAME
    removed: list[Path] = []
    for name in WORKFLOW_NAMES.values():
        path = services / name
        info_path = path / "Contents" / "Info.plist"
        if not path.is_dir() or not info_path.is_file():
            continue
        try:
            info = plistlib.loads(info_path.read_bytes())
        except Exception:
            continue
        if str(info.get("CFBundleIdentifier", "")).startswith("ru.docxdodyr.quick-action."):
            shutil.rmtree(path)
            removed.append(path)
    return removed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DOCXdodyr macOS Quick Action launcher")
    parser.add_argument("--action", choices=tuple(ACTION_FLAGS), help="операция для Finder")
    parser.add_argument("--install", action="store_true", help="установить Quick Actions в ~/Library/Services")
    parser.add_argument("--uninstall", action="store_true", help="удалить только Quick Actions DOCXdodyr")
    parser.add_argument("--uninstall-system", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, default=Path("/"), help=argparse.SUPPRESS)
    parser.add_argument("--home", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--app-path", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--wait", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("paths", nargs="*", help="файлы/папки из Finder")
    args = parser.parse_args(argv)
    if args.install:
        install_quick_actions(home=args.home, app_path=args.app_path or DEFAULT_APP_PATH)
        return 0
    if args.uninstall:
        uninstall_quick_actions(home=args.home)
        return 0
    if args.uninstall_system:
        uninstall_system_quick_actions(root=args.root)
        return 0
    if not args.action:
        parser.error("укажите --action, --install или --uninstall")
    try:
        result = launch_context_action(args.action, args.paths, app_path=args.app_path, wait=args.wait)
        # A detached PID is not a process exit status. Returning it from
        # ``main`` would make ``SystemExit(pid)`` report a failed Service when
        # the PID is greater than 255.
        return result if args.wait else 0
    except ContextMenuError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
