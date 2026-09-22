@echo off
setlocal DisableDelayedExpansion
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" ^
  -NoLogo -NoProfile -ExecutionPolicy Bypass ^
  -File "%~dp0scripts\run_recognition_overlay.ps1" ^
  -VisionExe "%~dp0outputs\tmp\build\bin\lol_augment_assistant.exe" ^
  -Mode KIWI -DebugSubmode speech
set "launchExitCode=%errorlevel%"
if not "%launchExitCode%"=="0" pause
exit /b %launchExitCode%
