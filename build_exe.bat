@echo off
cd /d "%~dp0"
if exist dist\Shadow.exe del /f /q dist\Shadow.exe
python -m pip install --upgrade pyinstaller playwright tkinterdnd2 sv-ttk darkdetect
python -m PyInstaller --onefile --noconsole --collect-all tkinterdnd2 --collect-all playwright --collect-all sv_ttk --hidden-import darkdetect --icon assets\shadow.ico --add-data "assets\shadow.ico;assets" --add-data "tools\narrate\captions.js;tools\narrate" --add-data "tools\narrate\wrap_tail.js;tools\narrate" --paths tools\narrate --hidden-import make_narrated_scene --name Shadow app.py
echo.
if exist dist\Shadow.exe (echo Built: dist\Shadow.exe) else (echo BUILD FAILED)
pause
