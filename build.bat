@echo off
REM Build executable with PyInstaller. Run from project root.
REM Installs runtime + build deps, then bundles data files into the .exe.
REM Prefer Windows launcher "py -3" when "python" is not on PATH.

where py >nul 2>nul
if not errorlevel 1 (
  echo Using Python launcher: py -3
  set "_PY=py -3"
) else (
  echo Using: python
  set "_PY=python"
)

echo Installing dependencies...
%_PY% -m pip install -r requirements.txt
%_PY% -m pip install -r requirements-build.txt
echo.
if not exist test1.xlsx echo WARNING: test1.xlsx missing - create or copy before building.
if not exist test2.xlsx echo WARNING: test2.xlsx missing - create or copy before building.
if not exist test3.xlsx echo WARNING: test3.xlsx missing - create or copy before building.
if not exist videos.txt echo WARNING: videos.txt missing - create or copy before building.
echo Building testscript.exe (with data files bundled)...
%_PY% -m PyInstaller --onefile --name testscript --console ^
  --add-data "test1.xlsx;." ^
  --add-data "test2.xlsx;." ^
  --add-data "test3.xlsx;." ^
  --add-data "videos.txt;." ^
  run.py
echo.
echo Done. Executable: dist\testscript.exe
echo Data files (test1.xlsx, test2.xlsx, test3.xlsx, videos.txt) are inside the exe.
