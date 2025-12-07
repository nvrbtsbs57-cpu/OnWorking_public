@echo off
REM Démarre le bot GODMODE (start_bot.py) depuis la racine du projet

cd /d "%~dp0"
cd ..
set ROOT=%CD%

set VENV_PY=%ROOT%\venv\Scripts\python.exe

if exist "%VENV_PY%" (
    set PYTHON="%VENV_PY%"
) else (
    echo [WARN] venv\Scripts\python.exe introuvable, on tente python global...
    set PYTHON=python
)

echo === START BOT GODMODE ===
echo Dossier racine : %ROOT%
echo Python : %PYTHON%
echo.

%PYTHON% "%ROOT%\scripts\start_bot.py"

echo.
echo Bot arrêté. Appuie sur une touche pour fermer...
pause >nul
