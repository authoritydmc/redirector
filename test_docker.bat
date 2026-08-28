@echo off
REM Redirector - Docker smoke test for Windows (calls bash script via WSL or Git Bash)
REM Requires Docker Desktop and Git Bash (or WSL)
echo ===========================================
echo   Redirector - Docker Smoke Test (Windows)
echo ===========================================
echo.
where docker >nul 2>&1
if %errorlevel% neq 0 (
  echo [FAIL] Docker not found. Install Docker Desktop.
  exit /b 1
)
echo [ OK ] Docker found
echo.

REM Try Git Bash first, then WSL
where bash >nul 2>&1
if %errorlevel% equ 0 (
  bash test_docker.sh
  exit /b %errorlevel%
)
wsl bash test_docker.sh
if %errorlevel% equ 0 (
  exit /b 0
)
echo [FAIL] Could not run test_docker.sh. Install Git Bash or WSL.
exit /b 1
