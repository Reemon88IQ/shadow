@echo off
cd /d "%~dp0"
where python >nul 2>nul || (echo Python not found. Installing...& winget install -e --id Python.Python.3.12 & echo Close this window, open it again and re-run install.bat & pause & exit /b)
taskkill /f /im Seekframe.exe >nul 2>nul
if exist dist\Seekframe.exe del /f /q dist\Seekframe.exe
if exist dist\Seekframe.exe (echo Close Seekframe first, then run install.bat again & pause & exit /b 1)
python -m pip install --upgrade pyinstaller playwright tkinterdnd2 sv-ttk darkdetect
python -m PyInstaller --onefile --noconsole --collect-all tkinterdnd2 --collect-all playwright --collect-all sv_ttk --hidden-import darkdetect --icon assets\shadow.ico --add-data "assets\shadow.ico;assets" --add-data "tools\narrate\captions.js;tools\narrate" --add-data "tools\narrate\wrap_tail.js;tools\narrate" --paths tools\narrate --hidden-import make_narrated_scene --name Seekframe app.py
if not exist dist\Seekframe.exe (echo BUILD FAILED & pause & exit /b 1)
set "DEST=%LOCALAPPDATA%\Seekframe"
mkdir "%DEST%" 2>nul
copy /y dist\Seekframe.exe "%DEST%\" >nul || (echo COPY FAILED - is Seekframe still open? & pause & exit /b 1)
copy /y assets\shadow.ico "%DEST%\shadow.ico" >nul || (echo ICON COPY FAILED & pause & exit /b 1)
powershell -NoProfile -Command "$s=New-Object -ComObject WScript.Shell; foreach($p in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))){ $l=$s.CreateShortcut((Join-Path $p 'Seekframe.lnk')); $l.TargetPath='%DEST%\Seekframe.exe'; $l.WorkingDirectory='%DEST%'; $l.IconLocation='%DEST%\shadow.ico,0'; $l.Save() }"
echo.
echo Installed to %DEST%
echo A shortcut was added to your Desktop and Start menu.
pause
