@echo off
setlocal DisableDelayedExpansion
py -3 "%~dp0install.py" --wizard
pause
