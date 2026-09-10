@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem Use Windows PowerShell with the existing execution policy; no downloads or policy changes.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -File "%~dp0scripts\export_ca.ps1" %*
set "CLAIM_SYNC_CA_EXIT=%ERRORLEVEL%"
if "%~1"=="" if not "%CLAIM_SYNC_NO_PAUSE%"=="1" pause
exit /b %CLAIM_SYNC_CA_EXIT%
