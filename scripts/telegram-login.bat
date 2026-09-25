@echo off
rem One-time: lets the server read chyste_nebo through the Telegram API. See scripts\telegram_login.py.
cd /d "%~dp0.."
set "PATH=%USERPROFILE%\.fly\bin;%PATH%"
where python >nul 2>&1 || (echo Python is not installed - install it from python.org, then run this again. & pause & exit /b 1)
python scripts\telegram_login.py
pause
