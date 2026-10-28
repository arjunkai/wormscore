@echo off
rem Builds dist\wormscore\wormscore.exe and dist\wormscore-windows.zip.
rem Needs: pip install -e .[dev]
cd /d "%~dp0\.."
python -m PyInstaller packaging\wormscore.spec --noconfirm --distpath dist --workpath build || exit /b 1
rem Python's zipfile, not Compress-Archive: Windows PowerShell 5.1 writes backslashes into the
rem zip's paths, which other unzip tools turn into file names instead of folders.
python -c "import shutil; shutil.make_archive('dist/wormscore-windows', 'zip', 'dist', 'wormscore')" || exit /b 1
echo Built dist\wormscore-windows.zip
