@echo off
setlocal enableextensions
title Images to PDF

rem ===============================================================
rem  Images in one folder  ==  one PDF
rem
rem  * Drag an image folder onto this file, or
rem  * double-click it and type/paste the folder when asked, or
rem  * run it from a terminal with any script option, e.g.
rem        "%~nx0" "D:\shots" --sort name -n review
rem
rem  Needs:  py -3 -m pip install pillow reportlab
rem  Writing into a protected folder (some D: folders) needs an
rem  elevated window: right-click this file, Run as administrator.
rem
rem  This launcher is intentionally plain ASCII.  cmd.exe reads batch
rem  files through the console code page and loses track of the next
rem  line when a file mixes code pages, which silently swallows lines.
rem  All Chinese text lives in images_to_pdf_v2.py and README_v2.md,
rem  where output goes through Python's Unicode-safe console layer.
rem ===============================================================

set "HERE=%~dp0"
set "SCRIPT=%HERE%images_to_pdf_v2.py"

if not exist "%SCRIPT%" (
    echo ERROR: images_to_pdf_v2.py is not next to this launcher.
    echo        expected: %SCRIPT%
    goto :fail
)

rem --- find a Python that already has the required packages -------
set "PY="
for %%C in ("py -3" "python" "python3") do (
    if not defined PY (
        %%~C -c "import PIL, reportlab" >nul 2>nul && set "PY=%%~C"
    )
)
if not defined PY (
    echo ERROR: no Python with Pillow and reportlab was found on PATH.
    echo        install them with:  py -3 -m pip install pillow reportlab
    goto :fail
)

rem --- forward the arguments; ask for a folder if none was dropped -
set "ARGS=%*"
if "%~1"=="" set "ARGS=--ask"

rem Run from the dropped folder so relative option paths follow it.
if exist "%~1\" cd /d "%~1"

%PY% "%SCRIPT%" %ARGS%
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo FAILED, exit code %RC%.  The input folder was not modified.
    echo.
    pause
    endlocal & exit /b %RC%
)

if "%~1"=="" (
    echo.
    echo Press any key to close this window . . .
    pause
)
endlocal & exit /b 0

:fail
echo.
pause
endlocal & exit /b 1
