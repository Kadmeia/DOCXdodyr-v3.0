# Функциональные тесты DOCXдодыр

Исходные документы в Git не хранятся. Все тестовые файлы ниже создаются из
синтетических данных в локальных каталогах, исключённых через `.gitignore`.

## Запуск

```bash
# Создание тестовых файлов (Word, Excel, PDF)
python tests/create_test_files.py
python tests/generate_mock_data.py

# Запуск всех тестов
python tests/run_functional_tests.py

# Дополнительный прогон после создания синтетических файлов
python tests/run_auto_tests.py
```

Или через venv:
```bash
./venv/bin/python tests/create_test_files.py
./venv/bin/python tests/run_functional_tests.py
```

## Antigravity regression kit

После правок запускайте локальный человекоподобный UI/service-контур и
прямые backend-контракты единым runner-ом:

```bash
./venv/bin/python -m tests.antigravity_regression.runner \
  --json-report artifacts/antigravity-report.json \
  --junit-report artifacts/antigravity-junit.xml
```

Он работает offline, не загружает реальные веса Qwen, поддерживает `--list`,
`--kind ui|backend`, `--scenario ID`, `--soak N` и timeout. Подробные сценарии,
коды завершения и формат отчётов описаны в
`docs/ANTIGRAVITY_REGRESSION.md`.

## Тестовые данные

Скрипт `create_test_files.py` создаёт в `tests/test_data/`:

- **test_obfuscation.docx** — договор с ФИО, организациями, ИНН, ОГРН, телефонами, email, адресами, паспортными данными
- **test_obfuscation.xlsx** — таблица с персональными данными
- **test_obfuscation.pdf** — при наличии LibreOffice или docx2pdf

## Покрытие тестов

| Тест | Описание |
|------|----------|
| DOCX обезличивание | Обработка Word-документа, замена персональных данных на плейсхолдеры |
| Excel обезличивание | Обработка Excel-файла по ячейкам |
| PDF обезличивание | OCR PDF → обезличивание (Apple Vision на macOS, Tesseract/PaddleOCR на Windows) |
| Конвертация в PDF | DOCX → PDF (требует LibreOffice или docx2pdf) |
| Дешифратор + восстановление | Создание JSON-дешифратора и восстановление исходных данных |

## Зависимости

- **Обязательно:** python-docx, PyQt5, PullentiPython, openpyxl
- **Для PDF:** LibreOffice или docx2pdf (конвертация), PyMuPDF и системный/нейросетевой OCR backend
