@echo off
REM Clear Sky - start the service on this PC and open the app in the browser.
cd /d "%~dp0"
python -c "import cryptography" 2>nul || pip install --quiet cryptography
start "" http://localhost:8642/m
python app\server.py %*
pause
