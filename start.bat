@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo.
echo ========================================
echo   Redbook Helper one-click launcher
echo ========================================
echo.

set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%ProgramFiles%\nodejs;%PATH%"

set "CHROME_FOUND="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if not defined CHROME_FOUND (
  where chrome >nul 2>nul
  if not errorlevel 1 set "CHROME_FOUND=1"
)
if not defined CHROME_FOUND (
  echo [Notice] Chrome was not detected. The app can still start, but Xiaohongshu Cookie login is easiest with Chrome.
  echo.
)

where uv >nul 2>nul
if errorlevel 1 (
  echo [1/4] uv is not installed. Installing uv for this Windows user...
  powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  where uv >nul 2>nul
  if errorlevel 1 (
    echo [Error] uv installation did not finish correctly. Please close this window and run start.bat again.
    pause
    exit /b 1
  )
) else (
  echo [1/4] uv is ready.
)

echo [2/4] Checking crawler vendor...
if not exist "vendor\Spider_XHS" (
  where git >nul 2>nul
  if errorlevel 1 (
    echo [Notice] Git was not found, so crawler vendor cannot be cloned automatically.
    echo          The app will still start. Install Git and rerun this file if crawling is needed.
  ) else (
    echo Installing Spider_XHS crawler vendor...
    powershell -ExecutionPolicy ByPass -NoProfile -File "scripts\setup_vendor.ps1"
  )
)

echo [3/4] Checking Node.js dependencies...
if exist "vendor\Spider_XHS\package.json" (
  call :ensure_npm
  if not errorlevel 1 (
    if not exist "vendor\Spider_XHS\node_modules" (
      echo Installing Spider_XHS Node dependencies...
      pushd "vendor\Spider_XHS"
      npm install
      if errorlevel 1 (
        popd
        echo [Notice] npm install failed. The app will start, but Xiaohongshu crawling may fail.
      ) else (
        popd
      )
    ) else (
      echo Spider_XHS Node dependencies are ready.
    )
  ) else (
    echo [Notice] Node.js/npm is not available. The app will start, but Xiaohongshu crawling may fail.
  )
) else (
  echo Spider_XHS vendor is not installed. Skipping Node dependency check.
)

echo.
echo [4/4] Starting Redbook Helper at http://127.0.0.1:8765
echo      Keep this window open while using the app.
echo.
uv run --python 3.11 uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
pause
exit /b

:ensure_npm
where npm >nul 2>nul
if not errorlevel 1 exit /b 0

where winget >nul 2>nul
if errorlevel 1 exit /b 1

echo Node.js/npm was not found. Trying to install Node.js LTS with winget...
winget install --id OpenJS.NodeJS.LTS -e --source winget --accept-package-agreements --accept-source-agreements
set "PATH=%ProgramFiles%\nodejs;%PATH%"
where npm >nul 2>nul
if errorlevel 1 exit /b 1
exit /b 0
