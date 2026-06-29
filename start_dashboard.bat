@echo off
REM Local GUI dashboard -> Oracle cloud algo (update IP if needed)
cd /d "%~dp0"
python dashboard_gui.py --remote 92.4.86.179
pause
