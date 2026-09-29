@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"
echo ========================================
echo   DOCXдодыр — Запуск приложения (Windows)
echo ========================================

if exist "venv\Scripts\python.exe" goto validate
py -3.11 -c "import sys; sys.exit(sys.version_info[:2] != (3, 11))" >nul 2>nul
if not errorlevel 1 goto create_with_launcher
python -c "import sys; sys.exit(sys.version_info[:2] != (3, 11))" >nul 2>nul
if errorlevel 1 goto missing_python
python -m venv venv
if errorlevel 1 goto setup_failed
goto validate
:create_with_launcher
py -3.11 -m venv venv
if errorlevel 1 goto setup_failed

:validate
"venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info[:2] != (3, 11))"
if errorlevel 1 (
    echo Ошибка: venv должен использовать Python 3.11. Переименуйте старый venv и повторите запуск.
    exit /b 1
)
REM Skip installation when all requirements predate the last successful setup.
"venv\Scripts\python.exe" -c "from pathlib import Path; import sys; marker=Path('venv/.docxdodyr-ready'); sources=[Path('requirements.txt'), *Path('requirements').rglob('*')]; sys.exit(0 if marker.is_file() and all(p.stat().st_mtime_ns <= marker.stat().st_mtime_ns for p in sources if p.is_file()) else 1)"
if not errorlevel 1 goto launch
echo [2/3] Проверка и установка библиотек...
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto setup_failed
"venv\Scripts\python.exe" -c "from pathlib import Path; Path('venv/.docxdodyr-ready').touch()"
if errorlevel 1 goto setup_failed

:launch
echo [3/3] Запуск DOCXдодыр...
"venv\Scripts\python.exe" main.py %*
exit /b %errorlevel%

:missing_python
echo Ошибка: установите Python 3.11 с https://www.python.org и Python Launcher.
pause
exit /b 1

:setup_failed
echo Ошибка при подготовке окружения. Повторите запуск после устранения причины.
pause
exit /b 1
