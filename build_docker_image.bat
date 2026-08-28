@echo off
REM Redirector - Docker image build for Windows
REM Usage: build_docker_image.bat [--prod] [--yes] [custom-tag]
echo ===========================================
echo   Redirector - Docker Image Build (Windows)
echo ===========================================
echo.
where docker >nul 2>&1
if %errorlevel% neq 0 (
  echo [FAIL] Docker not found.
  exit /b 1
)
REM Call bash script via Git Bash or WSL
where bash >nul 2>&1
if %errorlevel% equ 0 (
  bash build_docker_image.sh %*
  exit /b %errorlevel%
)
wsl bash build_docker_image.sh %*
if %errorlevel% equ 0 exit /b 0
echo [FAIL] Need Git Bash or WSL.
exit /b 1
