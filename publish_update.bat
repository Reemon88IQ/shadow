@echo off
rem Double-click: asks the new version and what changed, runs the tests, builds the exe + setup,
rem uploads the code and publishes the GitHub release (all steps in tools\publish_update.py).
cd /d "%~dp0"
python tools\publish_update.py %*
echo.
pause
