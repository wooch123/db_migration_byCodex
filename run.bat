@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0" || exit /b 1
set "PYTHONUTF8=1"
set "CLAIM_SYNC_BOOTSTRAP_PYTHON="

if defined CLAIM_SYNC_PYTHON goto custom_python
call :find_python
if defined CLAIM_SYNC_BOOTSTRAP_PYTHON goto setup
if "%CLAIM_SYNC_NO_SYSTEM_INSTALL%"=="1" goto missing_python
where winget >nul 2>&1
if errorlevel 1 goto missing_python
echo [setup] Python 3.11+ was not found. Installing Python 3.12 with winget...
winget install --id Python.Python.3.12 --exact --source winget --scope user --silent --accept-source-agreements --accept-package-agreements
if errorlevel 1 goto failed
call :find_python
if not defined CLAIM_SYNC_BOOTSTRAP_PYTHON goto missing_python
goto setup

:custom_python
call :try_python "%CLAIM_SYNC_PYTHON%"
if defined CLAIM_SYNC_BOOTSTRAP_PYTHON goto setup
echo [error] CLAIM_SYNC_PYTHON must point to a working Python 3.11+ executable.
goto failed

:setup
"%CLAIM_SYNC_BOOTSTRAP_PYTHON%" "%~dp0scripts\bootstrap.py"
if errorlevel 1 goto failed
if "%~1"=="--setup-only" goto setup_only
if "%~1"=="" goto default_web
"%~dp0.venv\Scripts\python.exe" -m claim_sync.cli %*
set "CLAIM_SYNC_EXIT_CODE=%errorlevel%"
goto finish

:default_web
echo [run] Starting the web console. Press Ctrl+C to stop.
"%~dp0.venv\Scripts\python.exe" -m claim_sync.cli web
set "CLAIM_SYNC_EXIT_CODE=%errorlevel%"
goto finish

:setup_only
if not "%~2"=="" goto bad_setup_args
set "CLAIM_SYNC_EXIT_CODE=0"
goto finish

:bad_setup_args
echo [error] --setup-only does not accept additional arguments.
goto failed

:missing_python
echo [error] Python 3.11+ is required. Automatic installation needs winget.
echo Install Python from https://www.python.org/downloads/windows/ and run this file again.
echo You can also set CLAIM_SYNC_PYTHON to the full path of an existing Python executable.
goto failed

:failed
set "CLAIM_SYNC_EXIT_CODE=1"

:finish
popd
if "%CLAIM_SYNC_EXIT_CODE%"=="0" goto exit_script
if not "%~1"=="" goto exit_script
if "%CLAIM_SYNC_NO_PAUSE%"=="1" goto exit_script
echo.
echo Startup failed. Review the error above.
pause
:exit_script
exit /b %CLAIM_SYNC_EXIT_CODE%

:find_python
call :try_python "%~dp0.venv\Scripts\python.exe"
if defined CLAIM_SYNC_BOOTSTRAP_PYTHON exit /b 0
call :try_python python.exe
if defined CLAIM_SYNC_BOOTSTRAP_PYTHON exit /b 0
call :try_python python3.exe
if defined CLAIM_SYNC_BOOTSTRAP_PYTHON exit /b 0
for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do call :try_python "%%P"
if defined CLAIM_SYNC_BOOTSTRAP_PYTHON exit /b 0
for %%V in (314 313 312 311) do call :try_python "%LocalAppData%\Programs\Python\Python%%V\python.exe"
if defined CLAIM_SYNC_BOOTSTRAP_PYTHON exit /b 0
for %%V in (314 313 312 311) do call :try_python "%ProgramFiles%\Python%%V\python.exe"
exit /b 0

:try_python
if defined CLAIM_SYNC_BOOTSTRAP_PYTHON exit /b 0
"%~1" -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>&1
if errorlevel 1 exit /b 0
set "CLAIM_SYNC_BOOTSTRAP_PYTHON=%~1"
exit /b 0
