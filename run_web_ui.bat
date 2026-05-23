@echo off
REM ============================================================
REM  EV Lab Dashboard - Web UI launcher (Windows)
REM  Double-click this file to start the FastAPI server and
REM  open the dashboard in your default browser.
REM ============================================================

setlocal
cd /d "%~dp0"

title EV Lab Dashboard - server.py

echo.
echo === EV Lab Dashboard ======================================
echo  Folder : %CD%
echo  URL    : http://127.0.0.1:8000
echo ===========================================================
echo.

REM --- 1. Sanity check: Python on PATH? ----------------------
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found on PATH.
    echo         Install Python 3 and re-run this script.
    echo.
    pause
    exit /b 1
)

REM --- 2. Sanity check: Ollama reachable? --------------------
REM     Non-fatal: the server still boots, but chat will fail
REM     until Ollama is up and qwen2.5:1.5b is pulled.
curl -s -o nul -w "%%{http_code}" http://127.0.0.1:11434/api/tags > "%TEMP%\ollama_check.txt" 2>nul
set /p OLLAMA_CODE=<"%TEMP%\ollama_check.txt"
del "%TEMP%\ollama_check.txt" >nul 2>&1
if not "%OLLAMA_CODE%"=="200" (
    echo [WARN]  Ollama not reachable at 127.0.0.1:11434
    echo         Start Ollama and run:  ollama pull qwen2.5:1.5b
    echo         The dashboard will still load; only chat will fail.
    echo.
)

REM --- 3. Open the browser after the server has time to bind -
start "" /b cmd /c "timeout /t 4 /nobreak >nul & start http://127.0.0.1:8000"

REM --- 4. Launch the server (foreground so logs are visible) -
python server.py

REM --- 5. Keep the window open if the server crashes ---------
echo.
echo [server exited with code %ERRORLEVEL%]
pause
endlocal
