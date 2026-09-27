@echo off
cd /d "%~dp0"
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8765 .*LISTENING"') do (
  if not "%%P"=="0" taskkill /F /PID %%P >nul 2>&1
)
timeout /t 1 /nobreak >nul
start "" pyw -3 app.py
exit /b 0
