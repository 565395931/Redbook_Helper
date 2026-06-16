@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo uv is not installed. Installing uv for this Windows user...
  powershell -ExecutionPolicy ByPass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

if exist "vendor\Spider_XHS\package.json" (
  where npm >nul 2>nul
  if not errorlevel 1 (
    if not exist "vendor\Spider_XHS\node_modules" (
      echo Installing Spider_XHS Node dependencies...
      pushd vendor\Spider_XHS
      npm install
      popd
    )
  ) else (
    echo Node.js/npm was not found. Spider_XHS signing may fail until Node.js 18+ is installed.
  )
)

echo Starting Redbook Analisyze at http://127.0.0.1:8765
uv run --python 3.11 uvicorn app.main:app --host 127.0.0.1 --port 8765
pause
