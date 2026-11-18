@echo off
rem Publishes dist\Shadow-<VERSION>-Setup.exe as GitHub release v<VERSION> (needs: gh auth login once).
rem Every installed Shadow sees it on its next start and offers "Update to v<VERSION>".
rem Order after a code change: bump VERSION, build_exe.bat, build_installer.bat, python tools\publish_public.py --push, release.bat
cd /d "%~dp0"
for /f "tokens=2 delims== " %%v in ('findstr /b "VERSION" render_core.py') do set "VER=%%~v"
for /f "delims=" %%r in ('python -c "import updater; print(updater.REPO)"') do set "REPO=%%r"
if not defined VER (echo Could not read VERSION from render_core.py & pause & exit /b 1)
if "%REPO:~0,6%"=="OWNER/" (echo Set REPO in updater.py first & pause & exit /b 1)
if not exist "dist\Shadow-%VER%-Setup.exe" (echo dist\Shadow-%VER%-Setup.exe not found - run build_exe.bat and build_installer.bat & pause & exit /b 1)
where gh >nul 2>nul || (echo GitHub CLI not found: winget install -e --id GitHub.cli & pause & exit /b 1)
gh release view v%VER% --repo %REPO% >nul 2>nul && (echo Release v%VER% already exists on %REPO%. Bump VERSION for a new one. & pause & exit /b 1)
if exist release_notes.txt (
  gh release create v%VER% "dist\Shadow-%VER%-Setup.exe" --repo %REPO% --title "Shadow %VER%" --notes-file release_notes.txt || (echo RELEASE FAILED & pause & exit /b 1)
) else (
  gh release create v%VER% "dist\Shadow-%VER%-Setup.exe" --repo %REPO% --title "Shadow %VER%" --notes "Shadow %VER%" || (echo RELEASE FAILED & pause & exit /b 1)
)
echo.
echo Released v%VER% on https://github.com/%REPO%/releases
pause
