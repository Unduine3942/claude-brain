@echo off
REM Claude Brain dashboard. The package must be installed first:
REM     pip install -e .
REM (the old run_app.py sys.path hack is gone - src/ layout requires a real install)
cd /d "%~dp0"
python -m claude_brain
if errorlevel 1 (
    echo.
    echo Failed to start. If this is a fresh clone, install it first:
    echo     pip install -e .
    echo.
    pause
)
