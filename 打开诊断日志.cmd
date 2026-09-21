@echo off
chcp 65001 >nul
setlocal DisableDelayedExpansion
set "hexHelperLogDir=%LOCALAPPDATA%\LoLRecognitionOverlay"
if not exist "%hexHelperLogDir%\" (
    echo 尚未生成日志，请先启动一次小猫。
    pause >nul
    exit /b 1
)
start "" "%SystemRoot%\explorer.exe" "%hexHelperLogDir%"
