# Закреплённые собственные wheels (Pinned Local Wheels)

## proxy_tools 0.1.0

- **Назначение:** Транзитивная зависимость `pywebview==6.2.1` (`module_property`). Pure-Python библиотека.
- **Причина включения:** На официальном индексе PyPI для `proxy_tools==0.1.0` доступен только исходный архив `proxy_tools-0.1.0.tar.gz` (`sdist`). Сборка wheel на этапе релизной установки запрещена политикой безопасности (`--only-binary=:all: --no-index`).
- **Исходный дистрибутив PyPI:**
  - URL: `https://files.pythonhosted.org/packages/f2/cf/77d3e19b7fabd03895caca7857ef51e4c409e0ca6b37ee6e9f7daa50b642/proxy_tools-0.1.0.tar.gz`
  - SHA-256: `ccb3751f529c047e2d8a58440d86b205303cf0fe8146f784d1cbcd94f0a28010`
- **Собранный Pure-Python Wheel:**
  - Файл: `proxy_tools-0.1.0-py3-none-any.whl`
  - SHA-256: `6a43d59c7435339bf8cc99a6e6de5d92584586b6a73175809645d69b17f48e71`
  - Воспроизводимая команда сборки: `python -m pip wheel --no-deps proxy_tools-0.1.0.tar.gz`
  - Архитектура: универсальный (`py3-none-any`, подходит для macOS arm64/x86_64 и Windows x64)
- **Лицензия:** BSD 3-Clause (сохранена в метаданных пакета).
