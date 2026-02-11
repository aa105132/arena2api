@echo off
REM ============================================================
REM Arena2API - Stop All Firefox Instances
REM ============================================================

echo Stopping all Firefox instances...
taskkill /F /IM firefox.exe >nul 2>&1

if %ERRORLEVEL% EQU 0 (
    echo [OK] All Firefox instances stopped.
) else (
    echo [INFO] No Firefox instances running.
)