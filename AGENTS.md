# Инструкция для Antigravity IDE — DOCXdodyr v3.0

## О проекте

**DOCXdodyr (DOCXдодыр)** — локальный инструмент для замены персональных данных и реквизитов в документах (DOCX, XLSX, PDF, сканы) перед передачей. Написан на Python 3.11. Имеет GUI (pywebview), CLI, поддержку OCR (Apple Vision / Tesseract), опциональный Qwen-LLM постпроцессор.

**Текущая версия:** v3.0.2
**Python:** строго 3.11.x (проверяется в CI)
**Платформы:** Windows 10/11 x64, macOS 14+ (arm64 и x86_64)

---

## Структура проекта

```
DOCXdodyr-v3.0/
├── app/                    # Ядро приложения (все бизнес-модули)
│   ├── backend_api.py      # Главный API (4000+ строк) — точка входа для GUI
│   ├── placeholders.py     # Логика замены ПДн (~100KB)
│   ├── detection_engine.py # Детектор сущностей
│   ├── legal_pullenti.py   # Pullenti NER-картридж для рус. юр. документов
│   ├── hidden_data.py      # Скрытые данные DOCX/PDF
│   ├── document_restorer.py# Восстановление документа по дешифратору
│   ├── ocr_backend.py      # OCR (Apple Vision / Tesseract)
│   ├── qwen_offline.py     # Qwen LLM (опционально, оффлайн)
│   ├── privacy_audit.py    # Аудит метаданных документов
│   ├── log_sanitizer.py    # Санитизация путей и ПДн в логах
│   └── review_context.py   # Шифрование контекста проверки (Fernet + Keychain)
├── main.py                 # GUI-точка входа (pywebview)
├── tests/                  # 105+ pytest-тестов
├── scripts/                # Сборка, CI, сканеры безопасности
│   ├── scan_release_sources.py  # Статический сканер секретов
│   └── check_public_tree.py     # Проверка публичного дерева
├── .github/workflows/      # CI (ci.yml, release.yml)
├── docs/                   # Документация разработчика
├── web/                    # Frontend (HTML/JS для pywebview)
├── pullenti_legal/         # Отдельный Python-пакет (pullenti-картридж)
├── settings.example.json   # Пример настроек (settings.json в gitignore)
└── version.py              # Версия и метаданные приложения
```

---

## Ключевые правила для агента

### ЗАПРЕЩЕНО

1. **Никогда не коммить** и не добавлять в код:
   - Реальные документы (docx, pdf, xlsx, rar, zip и т.д.)
   - Файлы `settings.json`, `review_queue.json`, `*.enc`
   - Файлы дешифраторов (`дешифратор*.json`)
   - Модели (`*.safetensors`, `*.bin`, `*.onnx`, `*.pt`)
   - Файлы из директорий: `scratch/`, `corpus/`, `manual_tests/`, `tests/fresh_corpus*/`,
     `tests/test_data/`, `tests/output_files/`, `tests/source_files/`

2. **Никогда не хардкодить** секреты, токены, API-ключи. В CI-файлах только через ${{ secrets.* }}.

3. **Не изменять правила CI** (.github/workflows/) без явного запроса.

4. **Не запускать** `scripts/build_macos.py`, `scripts/build_windows.py` без явного запроса.

5. **Не удалять** проверки в `scripts/check_public_tree.py` и `scripts/scan_release_sources.py`.

---

### ВАЖНО

- **Тестовые данные** создаются только синтетически через `tests/create_test_files.py` и
  `tests/generate_mock_data.py`. В тестах используй только синтетические имена, email типа
  `*@example.invalid`, пути типа `/Users/IvanPetrov/`.

- **Логи** должны проходить через `app/log_sanitizer.py`. Реальные пути и ПДн не должны
  попадать в логи.

- **Шифрование** контекста проверки — через `app/review_context.py` (Fernet + системный
  Keychain/Credential Manager). Не хранить сессионные данные в plaintext.

- При работе с Python-зависимостями — только pip, строго `python==3.11.x`. Не обновлять
  pip/setuptools/wheel без явного запроса (версии закреплены в CI: pip==26.2.1,
  setuptools==80.9.0, wheel==0.45.1).

---

## Запуск и тесты

```bash
# Запуск тестов (основной suite, без GUI и моделей)
python -m pytest -q

# С verbose и подробным статусом
python -m pytest --verbose -ra

# Запуск с поддержкой keyring (нужен для тестов шифрования)
DOCXDODYR_TEST_KEYRING=1 python -m pytest -q       # Linux/macOS
$env:DOCXDODYR_TEST_KEYRING=1; python -m pytest -q  # Windows PowerShell

# Запуск конкретного теста
python -m pytest tests/test_detection_engine.py -v

# Проверка дерева перед публикацией
python scripts/check_public_tree.py

# Сканирование секретов
python scripts/scan_release_sources.py --output /tmp/scan.json

# Сканирование включая Git-историю
python scripts/scan_release_sources.py --output /tmp/scan.json --history
```

---

## Архитектурные паттерны

### Обнаружение ПДн
1. `detection_engine.py` — регулярные выражения + контекстные правила
2. `legal_pullenti.py` — Pullenti NER для рос. юр. контекста
3. `placeholders.py` — замена обнаруженных сущностей на токены [ФИО], [PASSPORT] и т.д.

### Восстановление
- `document_restorer.py` — восстанавливает оригинал по дешифратору (JSON с маппингом)
- Дешифратор шифруется Fernet-ключом из Keychain

### Обработка скрытых данных
- `hidden_data.py` — комментарии, треки изменений, метаданные, вложения, VBA, AcroForm

### Тестирование
- Синтетические документы создаются в `tmp_path` (pytest tmpdir)
- Тесты с реальным GUI/OCR/Qwen требуют специальных окружений и пропускаются через
  `pytest.mark.skipif`
- Регрессионные тесты: `tests/test_claims_real_cases_regression.py`, `tests/test_luna_*`

---

## Работа с Git

```bash
# Проверить, что будет опубликовано
git ls-files

# Проверить авторство коммитов
git log --format="%an <%ae>" | sort -u
```

### Обязательный контроль выгрузки (Post-Push CI Monitoring)
- После любого `git push` агент обязан отслеживать статус запущенного пайплайна GitHub Actions до завершения.
- Выгрузка считается успешной только при статусе `success` на всех целевых платформах (Ubuntu, macOS, Windows).
- Если хотя бы один джоб падает, агент должен получить логи через API, устранить причину сбоя и повторить цикл проверки.
- При создании тестов помнить об ограничениях файловой системы Windows: запрещены символы `"`, `*`, `:`, `<`, `>`, `?`, `/`, `\`, `|`. Тесты с такими символами должны содержать `if sys.platform == "win32": pytest.skip(...)`.

CAUTION: Если в репозитории есть старая Git-история с реальными документами пользователей —
нельзя публиковать этот репозиторий. Нужно создать новый git init из чистой копии.
git filter-branch / git-filter-repo не очищают binary-blobs полностью в зеркалах.

---

## Контактные данные в UI (намеренные)

В `app/backend_api.py` и `main.py` вшиты публичные контакты автора:
- https://t.me/pro_servitude — Telegram-канал
- https://t.me/aebeloglazov — личный Telegram автора
- mailto:pro.servitude@gmail.com — email поддержки
- https://pay.cloudtips.ru/p/3bde1c71 — CloudTips (пожертвования)

Это намеренные публичные контакты. Не удалять без явного запроса владельца.

---

## Сборка (только по запросу)

```bash
# macOS (arm64), без подписи
python scripts/build_macos.py --arch arm64 --skip-sign

# Windows, без подписи
python scripts/build_windows.py --skip-sign
```

Артефакты сборки (dist/, build/) в gitignore.
