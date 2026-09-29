#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "========================================"
echo "  DOCXдодыр — Запуск приложения (macOS)"
echo "========================================"

# Use an explicit interpreter: Finder may have a minimal PATH.
if [ ! -x "venv/bin/python" ]; then
    PYTHON311=""
    for candidate in "$(command -v python3.11 || true)" /opt/homebrew/bin/python3.11 /usr/local/bin/python3.11 "$(command -v python3 || true)"; do
        if [ -n "$candidate" ] && [ -x "$candidate" ] && "$candidate" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))'; then
            PYTHON311="$candidate"
            break
        fi
    done
    if [ -z "$PYTHON311" ]; then
        echo "Ошибка: требуется Python 3.11. Установите его: brew install python@3.11"
        exit 1
    fi
    echo "[1/3] Создание окружения Python 3.11..."
    "$PYTHON311" -m venv venv
fi
if ! "venv/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))'; then
    echo "Ошибка: venv должен использовать Python 3.11. Переименуйте старый venv и повторите запуск."
    exit 1
fi
# Retry setup after interrupted installation; mark ready only on success.
if [ ! -f "venv/.docxdodyr-ready" ] || [ requirements.txt -nt "venv/.docxdodyr-ready" ] || [ -n "$(find requirements -type f -newer venv/.docxdodyr-ready 2>/dev/null)" ]; then
    echo "[2/3] Установка библиотек..."
    "venv/bin/python" -m pip install --upgrade pip
    "venv/bin/python" -m pip install -r requirements.txt
    touch "venv/.docxdodyr-ready"
fi

echo "[3/3] Запуск DOCXдодыр..."
exec "venv/bin/python" main.py "$@"
