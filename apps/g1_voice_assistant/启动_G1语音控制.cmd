@echo off
setlocal
set "G1_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "G1_PYTHONW=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if not exist "%G1_PYTHON%" (
  echo Python runtime was not found. See the usage guide.
  pause
  exit /b 1
)
"%G1_PYTHON%" "%~dp0g1_remote_voice_gui.pyw" --check
if errorlevel 1 (
  echo GUI dependencies are not ready. See the usage guide.
  pause
  exit /b 1
)
if exist "%G1_PYTHONW%" (
  start "" "%G1_PYTHONW%" "%~dp0g1_remote_voice_gui.pyw"
) else (
  "%G1_PYTHON%" "%~dp0g1_remote_voice_gui.pyw"
)
endlocal
