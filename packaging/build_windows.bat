@echo off
rem Builds dist\wormscore\wormscore.exe and dist\wormscore-windows.zip.
rem Needs: pip install -e .[dev]
cd /d "%~dp0\.."
python -m PyInstaller packaging\wormscore.spec --noconfirm --distpath dist --workpath build || exit /b 1
powershell -NoProfile -Command "Compress-Archive -Path dist\wormscore -DestinationPath dist\wormscore-windows.zip -Force"
echo Built dist\wormscore-windows.zip
