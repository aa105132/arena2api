@echo off
REM ============================================================
REM Arena2API - Firefox Multi-Profile Setup Script
REM ============================================================
REM 
REM 用途：在 Windows VPS 上批量创 Firefox Profile 并安装扩展
REM
REM 前置条件：
REM   1. 已安装 Firefox（默认路径或自定义 FIREFOX_PATH）
REM   2. 已下载 arena2api 项目到 EXTENSION_DIR
REM
REM 使用方式：
REM   setup_profiles.bat [数量] [服务器URL]
REM
REM 示例：
REM   setup_profiles.bat 8 http://10.0.0.1:9090
REM ============================================================

setlocal enabledelayedexpansion

REM ===== 配置 =====
set "PROFILE_COUNT=%1"
if "%PROFILE_COUNT%"=="" set "PROFILE_COUNT=8"

set "SERVER_URL=%2"
if "%SERVER_URL%"=="" set "SERVER_URL=http://127.0.0.1:9090"

set "PROFILE_BASE=C:\arena2api-profiles"
set "EXTENSION_DIR=%~dp0..\extension-firefox"

REM Firefox 路径（自动查找）
set "FIREFOX_PATH="
if exist "C:\Program Files\Mozilla Firefox\firefox.exe" (
    set "FIREFOX_PATH=C:\Program Files\Mozilla Firefox\firefox.exe"
) else if exist "C:\Program Files (x86)\Mozilla Firefox\firefox.exe" (
    set "FIREFOX_PATH=C:\Program Files (x86)\Mozilla Firefox\firefox.exe"
) else (
    echo [ERROR] Firefox not found. Please install Firefox or set FIREFOX_PATH manually.
    exit /b 1
)

echo ============================================================
echo  Arena2API Firefox Profile Setup
echo ============================================================
echo  Profiles:     %PROFILE_COUNT%
echo  Server:       %SERVER_URL%
echo  Profile Dir:  %PROFILE_BASE%
echo  Extension:    %EXTENSION_DIR%
echo  Firefox:      %FIREFOX_PATH%
echo ============================================================
echo.

REM ===== 创建 Profile 目录 =====
if not exist "%PROFILE_BASE%" mkdir "%PROFILE_BASE%"

for /L %%i in (1,1,%PROFILE_COUNT%) do (
    set "PDIR=%PROFILE_BASE%\profile_%%i"
    if not exist "!PDIR!" (
        mkdir "!PDIR!"
        echo [OK] Created profile directory: !PDIR!
    ) else (
        echo [SKIP] Profile directory exists: !PDIR!
    )
    
    REM 创建 user.js 预配置文件（Firefox about:config 设置）
    REM 这些设置在 Firefox 启动时自动应用
    (
        echo // Arena2API auto-generated profile config
        echo // Memory optimization for VPS
        echo user_pref("dom.ipc.processCount", 1^);
        echo user_pref("browser.cache.memory.capacity", 16384^);
        echo user_pref("browser.sessionhistory.max_entries", 3^);
        echo user_pref("javascript.options.mem.gc_high_frequency_time_limit_ms", 1000^);
        echo user_pref("media.peerconnection.enabled", false^);
        echo user_pref("geo.enabled", false^);
        echo user_pref("browser.shell.checkDefaultBrowser", false^);
        echo user_pref("browser.startup.homepage_override.mstone", "ignore"^);
        echo user_pref("datareporting.policy.dataSubmissionEnabled", false^);
        echo user_pref("toolkit.telemetry.enabled", false^);
        echo user_pref("browser.newtabpage.enabled", false^);
        echo user_pref("browser.aboutHomeSnippets.updateUrl", ""^);
        echo user_pref("browser.startup.homepage", "https://arena.ai/?mode=direct"^);
        echo user_pref("browser.startup.page", 1^);
        echo // Disable updates
        echo user_pref("app.update.enabled", false^);
        echo user_pref("extensions.update.enabled", false^);
        echo // Disable animations for performance
        echo user_pref("toolkit.cosmeticAnimations.enabled", false^);
        echo user_pref("ui.prefersReducedMotion", 1^);
    ) > "!PDIR!\user.js"
    echo [OK] Created user.js for profile %%i
)

echo.
echo ============================================================
echo  Profile setup complete!
echo ============================================================
echo.
echo  Next steps:
echo  1. Run: start_browsers.bat %PROFILE_COUNT% %SERVER_URL%
echo  2. In EACH Firefox window:
echo     a. Go to about:debugging#/runtime/this-firefox
echo     b. Click "Load Temporary Add-on"
echo     c. Select: %EXTENSION_DIR%\manifest.json
echo     d. Click the extension icon to verify connection
echo  3. After first setup, extensions persist across restarts
echo.
echo  TIP: Use start_browsers.bat to launch all instances at once
echo ============================================================

endlocal