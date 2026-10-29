@echo off
setlocal
set "desktopApp=%~dp0desktop-client\ThesisReviewWorkbench.exe"
if exist "%desktopApp%" (
  start "" "%desktopApp%"
  exit /b 0
)
set "desktopApp=%~dp0output\desktop_20261011\release\win-unpacked\ThesisReviewWorkbench.exe"
if exist "%desktopApp%" (
  start "" "%desktopApp%"
  exit /b 0
)
echo Desktop application not found. Please install ThesisReviewWorkbench-Setup first.
pause
exit /b 1
