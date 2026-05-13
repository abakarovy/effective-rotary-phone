@echo off
REM Сборка dist\testscript_3_3.exe (только сценарий 3.3 + test3.xlsx). Запускать из корня репозитория.
REM На Windows часто доступен только лаунчер "py", а "python" не в PATH — сначала пробуем py -3.

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
if not exist test3.xlsx echo WARNING: test3.xlsx not found - copy before build or PyInstaller may fail.
echo Building testscript_3_3.exe from testscript_3_3.spec ...
%_PY% -m PyInstaller --noconfirm testscript_3_3.spec
echo.
if exist dist\testscript_3_3.exe (
  echo Done. Executable: dist\testscript_3_3.exe
) else (
  echo Build failed - see PyInstaller output above.
  exit /b 1
)
