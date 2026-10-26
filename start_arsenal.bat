@echo off
rem Arsenal pipeline launcher.
rem The exit code of this script IS the verdict:
rem   0 pass / 1 fail / 2 setup error / 3 blocked (partial suite, nothing failed)
rem
rem Layout it expects:
rem   <ARSENAL_HOME>\config.json          machine wiring (project, report dir)
rem   <ARSENAL_HOME>\workspaces\<repo>\   project under test
rem   <ARSENAL_HOME>\reports\arsenal\     one directory per run
rem   <ARSENAL_HOME>\skills\arsenal-builder\templates\*.py   this code
rem Or drop the skill anywhere and run it standalone: then ARSENAL_HOME falls
rem back to this script's own directory.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PYDIR=%~dp0templates"

set "AHOME=%ARSENAL_HOME%"
if not defined AHOME set "AHOME=%~dp0"
if "%AHOME:~-1%"=="\" set "AHOME=%AHOME:~0,-1%"

set "CONFIG=%AHOME%\config.json"
if not exist "%CONFIG%" set "CONFIG=%~dp0config.json"

echo ==================================================
echo  AI Arsenal - sandbox test pipeline
echo  home: %AHOME%
echo ==================================================

echo.
echo [1/2] driving the sandbox and validating the result...
python "%PYDIR%\runner.py" --config "%CONFIG%" %*
set "RC=%errorlevel%"

if "%RC%"=="0" goto :passed
if "%RC%"=="3" goto :blocked
goto :failed

:passed
echo.
echo [2/2] advisory review (does not affect the verdict)...
set "RUNDIR="
if exist "%AHOME%\last-run.txt" set /p RUNDIR=<"%AHOME%\last-run.txt"
if defined RUNDIR (
    python "%PYDIR%\review.py" "%RUNDIR%"
) else (
    echo review skipped: no run directory recorded
)

echo.
echo ==================================================
echo  RESULT: PASS
echo ==================================================
set "RC=0"
goto :done

:blocked
echo.
echo ==================================================
echo  RESULT: BLOCKED - part of the suite never ran
echo  Nothing failed, so this is not a pass either.
echo  See the reasons above, then check %AHOME%\last-run.txt
echo ==================================================
goto :done

:failed
echo.
echo ==================================================
echo  RESULT: FAIL  ^(exit code %RC%^)
echo  Agent log and artifacts are in the run directory
echo  printed above; %AHOME%\last-run.txt points at it.
echo ==================================================

:done
if not defined ARSENAL_NO_PAUSE pause
endlocal & exit /b %RC%
