@echo off
cd /d "%~dp0"
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
call :stop_old
start "" pyw -3 app.py
exit /b 0

:stop_old
curl.exe -s -o NUL -X POST "http://127.0.0.1:8765/api/quit" -H "Content-Type: application/json" --data-binary "{\"action\":\"close\"}" --connect-timeout 1 --max-time 3 >nul 2>&1
set "_n=0"
:wait_free
netstat -ano | findstr /R /C:":8765 .*LISTENING" >nul 2>&1
if errorlevel 1 goto :eof
set /a _n+=1
if %_n% geq 16 goto :eof
timeout /t 1 /nobreak >nul
goto wait_free
