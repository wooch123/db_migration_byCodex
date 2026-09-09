@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "PYTHONUTF8=1"
set "CLAIM_SYNC_UPDATE_PYTHON="
if defined CLAIM_SYNC_PYTHON goto custom_python
call :try_python "%~dp0.venv\Scripts\python.exe"
call :try_python python.exe
call :try_python python3.exe
if defined CLAIM_SYNC_UPDATE_PYTHON goto update
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do call :try_python "%%P"
if defined CLAIM_SYNC_UPDATE_PYTHON goto update
goto missing_python

:custom_python
call :try_python "%CLAIM_SYNC_PYTHON%"
if not defined CLAIM_SYNC_UPDATE_PYTHON goto missing_python

:update
rem Parse the entire final block before Git replaces this batch file during the update.
(
    "%CLAIM_SYNC_UPDATE_PYTHON%" "%~dp0scripts\update.py" %*
    if errorlevel 1 (
        if "%~1"=="" if not "%CLAIM_SYNC_NO_PAUSE%"=="1" pause
        exit /b 1
    )
    if "%~1"=="" if not "%CLAIM_SYNC_NO_PAUSE%"=="1" pause
    exit /b 0
)

:missing_python
echo [error] Python 3.11+ is required. Run run.bat first or set CLAIM_SYNC_PYTHON.
if "%~1"=="" if not "%CLAIM_SYNC_NO_PAUSE%"=="1" pause
exit /b 1

:try_python
if defined CLAIM_SYNC_UPDATE_PYTHON exit /b 0
"%~1" -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>&1
if errorlevel 1 exit /b 0
set "CLAIM_SYNC_UPDATE_PYTHON=%~1"
exit /b 0
