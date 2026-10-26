@echo off
chcp 65001 >nul
echo ==================================================
echo 🏭 AI Arsenal Automation Pipeline
echo ==================================================

cd /d "%~dp0"
echo.
echo [1/2] Running tests in sandbox...
python runner.py
if %errorlevel% neq 0 (
    echo ❌ Test execution failed.
    pause
    exit /b
)

echo.
echo [2/2] Running AI Quality Gate Review...
python assert_eval.py

echo.
echo 🎉 Pipeline complete!
pause