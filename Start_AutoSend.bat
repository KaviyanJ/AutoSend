@echo off
title AutoSend - EE Outreach
cd /d "%~dp0"

echo.
echo  ==========================================
echo    AutoSend - EE Internship Outreach Tool
echo  ==========================================
echo.

:: ── Virtual environment setup ────────────────────────────────────────────────
if exist ".venv\Scripts\activate.bat" (
    echo  [OK] Activating virtual environment...
    call .venv\Scripts\activate.bat
) else (
    echo  [Setup] Creating virtual environment for the first time...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo  [ERROR] Could not create virtual environment.
        echo  Make sure Python is installed: https://python.org
        pause
        exit /b 1
    )
    call .venv\Scripts\activate.bat
    echo  [Setup] Installing dependencies (one-time only)...
    pip install -r requirements.txt --quiet
    echo  [OK] Dependencies installed.
)

:: ── Check .env file ───────────────────────────────────────────────────────────
if not exist ".env" (
    echo.
    echo  [Notice] No .env file found.
    echo  Copy the example below into a file named .env in this folder:
    echo.
    echo    FLASK_SECRET_KEY=change-me-to-something-random
    echo    GMAIL_USER=your_gmail@gmail.com
    echo    GMAIL_APP_PASSWORD=your_app_password
    echo    RESUME_PATH=Resume - Jeyakumar Kaviyan.pdf
    echo    DAILY_EMAIL_LIMIT=20
    echo    EMAIL_LOG_PATH=email_log.csv
    echo.
    echo  The app will still open but emails won't send until .env is configured.
    echo.
    timeout /t 4 /nobreak >nul
)

:: ── Open browser after a short delay (runs in background) ───────────────────
start "" /b cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:5000"

echo  [OK] Starting server at http://127.0.0.1:5000
echo  [OK] Browser will open automatically...
echo.
echo  To stop the server, close this window or press Ctrl+C.
echo.

:: ── Launch Flask ─────────────────────────────────────────────────────────────
python app.py

echo.
echo  Server stopped.
pause
