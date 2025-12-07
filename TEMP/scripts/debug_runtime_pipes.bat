@echo off
REM ---------------------------------------------------------
REM  debug_runtime_pipes.bat (version simple)
REM  Utilise 'python' global, pas le venv
REM ---------------------------------------------------------

cd /d "%~dp0"
cd ..
set ROOT=%CD%

REM On force l'URL du dashboard (port 8001)
set GODMODE_DASHBOARD_URL=http://127.0.0.1:8001/godmode

set PYTHON=python

echo === GODMODE runtime pipes debug ===
echo Dossier racine : %ROOT%
echo Python utilise : %PYTHON%
echo.

%PYTHON% "%ROOT%\scripts\debug_runtime_pipes.py" --interval 5

echo.
echo Terminé. Appuie sur une touche pour fermer...
pause >nul
