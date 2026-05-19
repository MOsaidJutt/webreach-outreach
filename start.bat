@echo off
echo ================================================
echo   WebReach - Outreach Automation System
echo ================================================
echo.

REM Activate virtual environment
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo WARNING: Virtual environment not found.
    echo Run: python -m venv venv ^&^& venv\Scripts\activate ^&^& pip install -r requirements.txt
    echo.
)

REM Check for .env file
if not exist ".env" (
    echo ERROR: .env file not found!
    echo Copy .env.example to .env and fill in your API keys.
    echo.
    pause
    exit /b 1
)

echo Starting WebReach server...
echo Dashboard: http://localhost:5000
echo.
python app.py
pause
