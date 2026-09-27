@echo off
cd /d "%~dp0"
call :stop_old
where pyw >nul 2>&1
if errorlevel 1 (
  echo Не найден Python. Установите с https://www.python.org/downloads/
  echo При установке отметьте "Add python.exe to PATH".
  pause
  exit /b 1
)
start "" pyw -3 app.py
exit /b 0

:stop_old
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8765 .*LISTENING"') do (
  if not "%%P"=="0" taskkill /F /PID %%P >nul 2>&1
)
timeout /t 1 /nobreak >nul
exit /b
