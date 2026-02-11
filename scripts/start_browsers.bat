@echo off
REM ============================================================
REM Arena2API - Firefox Multi-Instance Launcher
REM ============================================================
REM
REM 批量启动 Firefox 实例，每个使用独立 Profile
REM
REM 使用方式：
REM   start_browsers.bat [数量] [服器URL]
REM
REM 示例：
REM   start_browsers.bat 8 http://10.0.0.1:9090
REM   start_browsers.bat 5
REM ============================================================

setlocal enabledelayedexpansion

REM ===== 配置 =====
set "PROFILE_COUNT=%1"
if "%PROFILE_COUNT%"=="" set "PROFILE_COUNT=8"

set "SERVER_URL=%2"
if "%SERVER_URL%"=="" set "SERVER_URL=http://127.0.0.1:9090"

set "PROFILE_BASE=C:\arena2api-profiles"
set "STARTUP_DELAY=8"

REM Firefox 路径
set "FIREFOX_PATH="
if exist "C:\Program Files\Mozilla Firefox\firefox.exe" (
    set "FIREFOX_PATH=C:\Program Files\Mozilla Firefox\firefox.exe"
) else if exist "C:\Program Files (x86)\Mozilla Firefox\firefox.exe" (
    set "FIREFOX_PATH=C:\Program Files (x86)\Mozilla Firefox\firefox.exe"
) else (
    echo [ERROR] Firefox not found.
    exit /b 1
)

echo ============================================================
echo  Arena2API - Launching %PROFILE_COUNT% Firefox Instances
echo ============================================================
echo  Server URL:  %SERVER_URL%
echo  Profiles:    %PROFILE_BASE%
echo  Delay:       %STARTUP_DELAY%s between launches
echo ============================================================
echo.

REM ===== 检查 Profile 目录 =====
if not exist "%PROFILE_BASE%" (
    echo [ERROR] Profile directory not found. Run setup_profiles.bat first.
    exit /b 1
)

REM ===== 启动 Firefox 实例 =====
for /L %%i in (1,1,%PROFILE_COUNT%) do (
    set "PDIR=%PROFILE_BASE%\profile_%%i"
    
    if not exist "!PDIR!" (
        echo [WARN] Profile directory missing: !PDIR! - skipping
    ) else (
        echo [%%i/%PROFILE_COUNT%] Starting Firefox with profile_%%i ...
        
        start "" "%FIREFOX_PATH%" ^
            -profile "!PDIR!" ^
            -no-remote ^
            -new-instance ^
            "https://arena.ai/?mode=direct"
        
        REM 等待一段时间再启动下一个，避免内存峰值
        if %%i LSS %PROFILE_COUNT% (
            echo     Waiting %STARTUP_DELAY%s before next launch...
            timeout /t %STARTUP_DELAY% /nobreak >nul
        )
    )
)

echo.
echo ============================================================
echo  All %PROFILE_COUNT% Firefox instances launched!
echo ============================================================
echo.
echo  Memory tip: Monitor with Task Manager
echo  Expected usage: ~250-350MB per instance
echo.
echo  If extension not installed yet:
echo    1. In each Firefox: about:debugging#/runtime/this-firefox
echo    2. Load Temporary Add-on - select extension-firefox\manifest.json
echo    3. Click extension icon - set Server URL to: %SERVER_URL%
echo.
echo  Verify: curl %SERVER_URL%/v1/extension/status
echo ============================================================

endlocal