@echo off
setlocal

where py >nul 2>&1
if errorlevel 1 goto run_python
"%ComSpec%" /d /c py -3 "%~dp0click_windows.py" %*
set "CLICK_EXIT=%errorlevel%"
if not "%CLICK_EXIT%"=="103" exit /b %CLICK_EXIT%

:run_python
where python >nul 2>&1
if errorlevel 1 goto run_python3
"%ComSpec%" /d /c python "%~dp0click_windows.py" %*
set "CLICK_EXIT=%errorlevel%"
if not "%CLICK_EXIT%"=="9009" exit /b %CLICK_EXIT%

:run_python3
where python3 >nul 2>&1
if errorlevel 1 goto no_python
"%ComSpec%" /d /c python3 "%~dp0click_windows.py" %*
exit /b %errorlevel%

:no_python
>&2 echo Click requires Python 3.10 or newer; the Windows launcher found none of py, python, or python3.
>&2 echo Install it from https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH") or run: winget install Python.Python.3.12
exit /b 9009
