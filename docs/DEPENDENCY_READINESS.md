# Воспроизводимость зависимостей

Текущая целевая платформа: macOS 14+ (arm64 и x86_64), Python 3.11.x.
Development-сборка на macOS 26.2 не подтверждает работу на macOS 14;
необходимы проверки установленного приложения на минимальной целевой ОС.

Ниже сохранены исторические результаты ревизии 11 сентября 2026,
Python 3.11.15. Они не подтверждают состояние текущей сборки или поддержку
целевой ОС; для релиза проверки требуется повторить.

Проверка pip resolver с --only-binary=:all:, целевыми platform tags и маркерами ОС выполнена для macosx_12_0_arm64, macosx_12_0_x86_64 и win_amd64. Она не является запуском на целевой машине.

- ARM64/Windows: Для `proxy_tools==0.1.0` (pure-Python зависимость pywebview) на PyPI доступен только sdist `tar.gz`. Собран и закреплён локальный pure-Python wheel `requirements/wheels/proxy_tools-0.1.0-py3-none-any.whl` (SHA-256: `6a43d59c7435339bf8cc99a6e6de5d92584586b6a73175809645d69b17f48e71`), собранный из проверенного sdist (`ccb3751f529c047e2d8a58440d86b205303cf0fe8146f784d1cbcd94f0a28010`). Документация и рецепт зафиксированы в `requirements/wheels/README.md`.
- Intel/macOS 12: `cryptography==50.0.0` не распространяет официальных бинарных wheels под x86_64 macOS на PyPI (доступны только wheels `macosx_11_0_arm64`). Для Intel Mac требуется либо предварительная контролируемая сборка wheel из проверенного sdist на нативном Intel-раннере, либо использование корпоративного wheelhouse. Понижение версии криптографии запрещено политикой безопасности.
- Полный набор транзитивных зависимостей и инструментов сборки/тестирования (`build==1.2.2.post1`, `pyinstaller==6.22.2`, `pytest==8.4.2`, `pytest-mock==3.15.1`, `setuptools==80.9.0`, `pyproject-hooks`, `altgraph`, `macholib`, `pefile`, `pywin32-ctypes`, `iniconfig`, `pluggy`) включён в `requirements/lock-macos.txt` и `requirements/lock-windows.txt` с официальными SHA-256 хэшами PyPI. Функция `prepare_release_environment.check_lock()` успешно проходит проверку полноты для `macos-arm64` и `windows-x64`.
- PullentiPython 0.1 импортирует pkg_resources; setuptools==80.9.0 включён как runtime dependency.
- Локальный Homebrew Python 3.11 на macOS 26 имеет флаг `minos 26.0` (сборка из бутылки под текущую ОС). Для текущего production-релиза под macOS 14 требуется Python runtime с фактическим `minos` не выше 14.0 (например, подходящая сборка python.org или python-build-standalone); значение проверяется у выбранных бинарных файлов. Проверка `scripts/audit_macos_deployment.py` корректно блокирует production-статус сборки на Homebrew Python.

Порядок доведения: выбрать совместимый Python runtime (python.org / standalone), разрешить зависимости на каждой нативной платформе, включить полное замыкание зависимостей и build tools в hash-lock, проверить wheel-only download, установить из локального wheelhouse с --no-index --require-hashes, выполнить pip check, source/packaged/native проверки, сохранить SBOM из этого окружения и license inventory всех фактически упакованных компонентов. До этого production gate остаётся в статусе NO-GO.

