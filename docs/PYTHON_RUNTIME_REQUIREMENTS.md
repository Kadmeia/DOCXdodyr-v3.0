# Требования к среде выполнения Python

Проект DOCXдодыр 3.0 использует только **CPython 3.11.x**: для запуска из
исходников, тестов, дополнительных модулей и сборки установщиков. Другие
ветки интерпретатора не поддерживаются. Зависимости устанавливаются в
отдельное окружение Python 3.11; системный Python macOS не используется.

## Создание окружения

macOS:

```bash
python3.11 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt -r requirements/requirements-dev.txt
python -m pip check
python -m pytest -q
```

Windows PowerShell:

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r requirements/requirements-dev.txt
python -m pip check
python -m pytest -q
```

Старое виртуальное окружение не обновляет интерпретатор при установке
зависимостей. Создайте новое окружение и проверьте `python --version` перед
работой. Все команды `python` в документации предполагают активированное
окружение Python 3.11.

## Сборка и зависимости

- Метаданные пакета: `requires-python = ">=3.11,<3.12"`.
- CI использует Python 3.11; release workflow фиксирует patch-версию.
- `requirements.txt` включает закреплённые зависимости ядра и платформ;
  Qwen устанавливается отдельно из `requirements/requirements-qwen.txt`.
- Платформенные hash-lock файлы в `requirements/` применяются сборочными
  скриптами. Их совместимость проверяется на целевой ОС и архитектуре.
- Для релиза обязательны `pip check`, полный pytest, проверка публичного
  дерева и проверки собранного приложения. Выбор Python сам по себе не
  подтверждает совместимость нативных библиотек или готовность релиза.
