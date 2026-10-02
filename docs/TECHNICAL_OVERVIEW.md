# Техническое описание DOCXдодыр v3.0.2

Архитектурный обзор ядра, конвейера обработки документов и мер безопасности.

## Архитектура и публичные границы

`main.py` — GUI/CLI entry point. Основные модули, включая `backend_api.py` и `ui_bridge.py`, находятся в `app/`. `web/index.html` и `web/script.js` вызывают `window.pywebview.api`, связанный с `ApiWrapper`. Wrapper открывает системные диалоги, передаёт команды `BackendApi`, управляет окнами и shutdown. `ui_bridge.py` сериализует события Python → JavaScript и предотвращает вызовы в закрывающееся окно.

BackendApi загружает настройки и лениво инициирует Pullenti. Одиночная обработка проходит через `process_single_file`; пакетная — через `folder_pipeline.py` с общим `entity_registry.py`. DOCX использует python-docx и Office XML; XLSX — openpyxl и `xlsx_semantic.py`; PDF — `pdf_convert.py`, PyMuPDF/pypdf; сканы — `ocr_backend.py`. Pullenti/`legal_pullenti.py`/`placeholders.py` находят и заменяют сущности. `hidden_data.py` и `privacy_audit.py` обрабатывают скрытые части и формируют отчёты. `review_queue` и `review_context.py` обслуживают ручные решения; `document_restorer.py` выполняет восстановление. Выходы создаются в рабочем каталоге результата.

## Trust boundaries

| Граница | Недоверенный ввод | Обязательная защита / оставшаяся проверка |
|---|---|---|
| Документ → парсер | ZIP/Office XML, PDF, изображения | Лимиты размера/распаковки, повреждённые/зашифрованные входы; полная защита Office от zip bombs ещё требует отдельного аудита |
| UI → bridge | строки, пути, IDs, действия | Валидация в backend; HTML не является разрешением выполнять команды |
| Worker → UI/логи | текст документа, исключения | ui_bridge и log_sanitizer; preview по запросу содержит исходный контекст и считается чувствительным |
| Модель → загрузчик | redirects, Range, архив, manifest | HTTPS allowlist, pinned revision, размер, SHA-256 весов, ограничение распаковки; все файлы модели pinned по размеру/SHA-256 на указанной ревизии |
| Vault → keyring | зашифрованный контекст | Fernet, schema_version=1; отказ при недоступном keyring |
| Восстановление → decoder | JSON, binding, manifest | docxdodyr.decoder-binding/v1 и docxdodyr.batch-manifest/v1, hashes, проверка структуры; автопоиск ограничен родительскими уровнями |
| Выход → файловая система | пользовательский каталог, симлинки, нехватка места | атомарная замена для настроек/vault/checkpoints; race/symlink атаки требуют проверки на каждой целевой ОС |
| Сборка → публикация | бинарники, dependency wheels, evidence | фактическая архитектура, подпись, timestamp, нотарификация, checksums, защищённый production environment |

## Состояние и совместимость

`app_paths.atomic_write_text/json` записывает временный файл в том же каталоге, fsync и os.replace. Не переносите временную запись на другой том. `state_migration.py` отвечает за перенос настроек; `crash_recovery.py` — checkpoint и список оставшихся файлов. `SecureContextVault` использует envelope schema_version=1 и algorithm=fernet. Формат настроек следует читать через API; новое поле должно иметь безопасное значение по умолчанию. Дешифратор — чувствительный JSON, не следует считать его зашифрованным только потому, что vault зашифрован.

Автопоиск дешифратора проверяет binding/manifest и ограничивает размер чтения 10 MiB и подъём четырьмя уровнями. Восстановление не возвращает удалённые macros/attachments/revisions и исходные подписи. Для проверки добавления сущности используйте синтетические минимальные пары и тесты падежей/коллизий. Новый формат требует parser, hidden-data policy, backend route, capability contract, UI и round-trip/negative fixtures.

## Платформы и сборка

Python 3.11.15 — выбранный release runtime. macOS: две нативные thin-сборки, `--arch arm64` / `--arch x86_64`; EXE получает DOCXDODYR_TARGET_ARCH. Каждый Mach-O сверяется с целью. Windows: PE32+ x64, GUI subsystem. Минимальная целевая версия macOS — 14.0 (Sonoma). Deployment target 14.0 задаётся сборке, но не понижает минимальную ОС уже готовых wheel; проверяйте load commands всех библиотек. Apple Vision используется на macOS, Tesseract — при наличии локальной установки. Development-сборка на macOS 26.2 не подтверждает запуск на macOS 14; проверка установленного приложения на минимальной целевой ОС обязательна.

`verify_packaged_app.py` запускает frozen CLI в временных каталогах и сравнивает install directory. Это не clean-machine GUI/installer verification. `check_test_results.py` запрещает skips в обязательном отчёте. `release_gate.py` проверяет все шесть имён дистрибутивов и привязанное к commit/hash свидетельство каждой платформы; evidence должен поступать из доверенной проверки в защищённом workflow, а не от стороннего PR. Development build не является production release.

## Тестовая пирамида

Unit и синтетические integration: `python -m pytest -q`. Нативные GUI/OCR требуют интерактивной сессии. Packaging выполняется после PyInstaller. Installed tests — после установки на чистые Windows 10/11, macOS Intel/Apple Silicon; на чужой ОС не считаются пройденными. Qwen real smoke отдельно требует локальную модель. Не запускайте тесты на реальных документах и не храните вывод в fixture-каталогах. Планы изменения архитектуры фиксируйте в docs/adr с отличием принятого решения от доказанной реализации.
