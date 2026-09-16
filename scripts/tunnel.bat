@echo off
rem Instant public HTTPS link to the service running on this PC (Cloudflare quick tunnel, no account).
rem 1) download cloudflared.exe once: https://github.com/cloudflare/cloudflared/releases/latest (cloudflared-windows-amd64.exe, rename to cloudflared.exe, put it next to this file)
rem 2) run start.bat, then this file. The https://xxxx.trycloudflare.com URL printed below works from anywhere (phone on 4G too).
rem    Set "access_key" in config.json first so only you can open it.
cd /d "%~dp0.."
cloudflared.exe tunnel --url http://localhost:8642
pause
