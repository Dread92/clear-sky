@echo off
cd /d "%~dp0.."
REM Publishes this folder to GitHub as Dread92/clear-sky (private), or pushes the changes if it is already there.
REM Needs GitHub CLI: winget install GitHub.cli   (then run this script; it logs you in via the browser)
where gh >nul 2>nul || (echo GitHub CLI not found. Run:  winget install GitHub.cli  and re-run this script. & pause & exit /b 1)
gh auth status >nul 2>nul || gh auth login --web
if not exist .git (
  git init -b main
  git add -A
  git commit -m "Clear Sky — air-raid tracker for Kyiv and Ukraine"
  gh repo create clear-sky --private --source . --push --description "Clear Sky: unofficial air-raid / drone tracker for Kyiv and all of Ukraine (Python stdlib + one HTML page)"
) else (
  git add -A
  git commit -m "update %date% %time%" || echo nothing to commit
  git push
)
pause
