@echo off
rem Builds dist\Shadow-<VERSION>-Setup.exe with Inno Setup 6 from the CURRENT dist\Shadow.exe (run build_exe.bat first after code changes).
cd /d "%~dp0"
if not exist dist\Shadow.exe (echo dist\Shadow.exe not found - run build_exe.bat first & pause & exit /b 1)
for /f "tokens=2 delims== " %%v in ('findstr /b "VERSION" render_core.py') do set "VER=%%~v"
if not defined VER (echo Could not read VERSION from render_core.py & pause & exit /b 1)
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (echo Inno Setup 6 not found. Install it once: winget install -e --id JRSoftware.InnoSetup & pause & exit /b 1)
if exist "dist\Shadow-%VER%-Setup.exe" del /f /q "dist\Shadow-%VER%-Setup.exe"
"%ISCC%" /Q /DAppVer=%VER% installer\shadow.iss || (echo INSTALLER BUILD FAILED & pause & exit /b 1)
echo.
echo Built: dist\Shadow-%VER%-Setup.exe  (copy this one file to the other PC and run it)
pause
