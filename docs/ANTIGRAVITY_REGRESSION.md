# Antigravity regression kit

Этот набор предназначен для повторного запуска после любых изменений в
backend или Web UI. Он использует существующие pytest-сценарии и несколько
компактных локальных фикстур; сетевой доступ и реальные веса Qwen по умолчанию
запрещены.

## Быстрый запуск

Из корня проекта:

```bash
./venv/bin/python -m tests.antigravity_regression.runner \
  --json-report artifacts/antigravity-report.json \
  --junit-report artifacts/antigravity-junit.xml
```

Печатать доступные сценарии:

```bash
./venv/bin/python -m tests.antigravity_regression.runner --list
```

Запуск только одного блока или сценария:

```bash
./venv/bin/python -m tests.antigravity_regression.runner --kind ui
./venv/bin/python -m tests.antigravity_regression.runner --scenario backend.restart_concurrency_privacy
```

Для проверки устойчивости сервиса можно повторить набор. `--soak 10` означает
десять последовательных итераций; на каждом сценарии действует timeout из
`scenarios.json`, который можно переопределить флагом `--timeout 90`.

```bash
./venv/bin/python -m tests.antigravity_regression.runner --soak 10 --keep-fixtures
```

## Что проверяется

Блок `ui` имитирует действия пользователя: запуск и темы, выбор файлов и
папок, параметры сохранения, фильтры и навигацию review, массовое решение,
политику скрытого содержимого с подтверждением необратимых действий,
автопоиск дешифратора, необратимый PDF и безопасный offline-Qwen.

Блок `backend` вызывает публичные API-контракты напрямую: inventory методов,
настройки, ошибки валидации, очередь review, отсутствие PII в очереди/audit,
зашифрованный контекст между перезапусками, отказ при отсутствии контекста,
параллельные чтения, progress/status и безопасные ветки отсутствующих Pullenti
и OCR.

## Коды завершения

`0` — все выбранные сценарии пройдены; `1` — ошибка сценария; `2` — ошибка
manifest/параметров; `3` — timeout или ошибка runner; `4` — найден PII-маркер
в отчёте. JSON-отчёт содержит статусы, итерацию soak, длительности и хвост
вывода. JUnit предназначен для CI/Antigravity.

Пример шага Antigravity после изменения кода:

1. Запустить `--list` и выбрать изменённый блок.
2. Выполнить сценарий с `--soak 3`.
3. При коде `0` передать JSON/JUnit в проверку; при ином коде открыть
   `output_tail` соответствующего сценария.

