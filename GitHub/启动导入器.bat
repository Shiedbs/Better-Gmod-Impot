@echo off
setlocal
cd /d "%~dp0"

set "PYW="
if exist "%~dp0runtime\python\pythonw.exe" set "PYW=%~dp0runtime\python\pythonw.exe"
if not defined PYW if exist "%~dp0..\_runtime\python\pythonw.exe" set "PYW=%~dp0..\_runtime\python\pythonw.exe"

rem ??????????????? _runtime\python ??????
if not defined PYW for /d %%D in ("%~dp0..\*") do (
    if exist "%%~fD\_runtime\python\pythonw.exe" set "PYW=%%~fD\_runtime\python\pythonw.exe"
)

if not defined PYW (
    where pythonw >nul 2>nul
    if not errorlevel 1 set "PYW=pythonw"
)
if not defined PYW (
    where python >nul 2>nul
    if not errorlevel 1 set "PYW=python"
)

if not defined PYW (
    echo.
    echo Python 3 not found.
    echo Install Python 3.12+ or put a portable Python in runtime\python\.
    echo.
    pause
    exit /b 1
)

start "" "%PYW%" "%~dp0GmodModelImporter.py"
exit /b 0