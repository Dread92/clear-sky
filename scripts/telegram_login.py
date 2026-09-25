"""One-time sign-in that lets the server read chyste_nebo (and any other channel in telegram_api_channels)
through the Telegram API. Run it with scripts\\telegram-login.bat.

What it does, all on this PC:
  1. asks for the api_id and api_hash you created on my.telegram.org, and the phone number of the account
     that will read the channel (a dedicated account is best);
  2. signs in — Telegram sends a code to that account, you type it here (and the 2-step password if set);
  3. joins the channel, so new posts reach the server the moment they are published, and reads a few posts
     to show that it works;
  4. stores TG_API_ID, TG_API_HASH and TG_SESSION as Fly secrets with `fly secrets import` (read from stdin,
     never on a command line, never printed, never written to a file). Fly restarts the app with them.

The session string is a key to that Telegram account. It exists only in Fly's secret store afterwards.
To revoke it: Telegram > Settings > Devices > "Clear Sky server" > Terminate.
"""
import asyncio
import getpass
import os
import re
import shutil
import subprocess
import sys

CHANNELS = ["chyste_nebo"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def telethon():
    try:
        import telethon  # noqa: F401
    except ImportError:
        print("Installing the Telegram library (telethon)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", "--quiet", "telethon"])
        # `pip install --user` on a PC that never had a user package creates the user site folder now — but
        # Python only puts that folder on its path when it starts, so this very run could not import what it had
        # just installed ("No module named 'telethon'"). Add it by hand.
        import importlib
        import site
        site.addsitedir(site.getusersitepackages())
        importlib.invalidate_caches()
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.functions.channels import JoinChannelRequest
    return TelegramClient, StringSession, JoinChannelRequest


def app_name():
    p = os.path.join(ROOT, ".flyapp")
    return open(p, encoding="utf-8").read().strip() if os.path.exists(p) else "kyiv-air-watch-gb"


def ask_app():
    """api_id and api_hash, checked for their shape before Telegram sees them. The hash is shown as it is typed:
    a hidden prompt swallowed a Ctrl+V paste in some Windows consoles, and nobody could see why it failed."""
    while True:
        api_id = input("api_id (the number from my.telegram.org): ").strip()
        if api_id.isdigit():
            break
        print("  api_id is a number only, like 1234567 - try again.")
    while True:
        raw = input("api_hash (32 letters and digits from my.telegram.org): ")
        api_hash = "".join(c for c in raw if c.isalnum()).lower()
        if re.fullmatch(r"[0-9a-f]{32}", api_hash):
            return api_id, api_hash
        print(f"  That is {len(api_hash)} characters, an api_hash has 32 (0-9, a-f). Paste it again"
              " (right-click pastes in this window).")


def ask_code():
    return input("Code Telegram just sent to that account (in the Telegram app, or by SMS): ").strip()


def ask_password():
    """The account's two-step verification password. Hidden, like any password — and said so, because a prompt
    where nothing appears looks like a prompt that does not work. Nothing received: asked again, visibly."""
    print("\nThis account has two-step verification: its password is needed.")
    print("Nothing appears while you type it - not even stars. That is normal: type it, then press Enter.")
    print("(To paste it, right-click in this window.)")
    pw = getpass.getpass("Two-step password: ")
    if not pw:
        print("Nothing was received. Type it once more - this time it WILL be visible on the screen.")
        pw = input("Two-step password (visible): ")
    return pw


def main():
    TelegramClient, StringSession, JoinChannelRequest = telethon()
    print()
    print("=== Clear Sky - Telegram sign-in for the server ===")
    print("The values you type here stay on this PC and go straight to Fly's secret store.")
    print()
    api_id, api_hash = ask_app()
    phone = input("Phone number of the account that will read the channel (+380..., +33...): ").strip()

    async def sign_in():
        nonlocal api_id, api_hash
        while True:
            client = TelegramClient(StringSession(), int(api_id), api_hash, device_model="Clear Sky server")
            try:
                await client.start(phone=phone, code_callback=ask_code, password=ask_password)
                break
            except Exception as e:
                await client.disconnect()
                if type(e).__name__ != "ApiIdInvalidError":
                    raise
                print("\nTelegram refused this api_id / api_hash pair. On https://my.telegram.org > API development")
                print("tools, copy 'App api_id' and 'App api_hash' again (the hash is 32 letters and digits).\n")
                api_id, api_hash = ask_app()
        me = await client.get_me()
        print(f"\nSigned in as {me.first_name or ''} {me.last_name or ''}".rstrip())
        for ch in CHANNELS:
            ent = await client.get_entity(ch)
            try:
                await client(JoinChannelRequest(ent))
                print(f"Joined @{ch} (new posts will reach the server as they are published)")
            except Exception as e:
                print(f"Could not join @{ch} ({e}) - the server will still read it every 30 s")
            msgs = await client.get_messages(ent, limit=5)
            print(f"@{ch}: read {len(msgs)} recent posts. The latest:")
            for m in msgs[:3]:
                print("   ", (m.message or "").replace("\n", " / ")[:110])
        session = client.session.save()
        await client.disconnect()
        return session

    session = asyncio.run(sign_in())
    fly = shutil.which("fly") or shutil.which("flyctl") or os.path.join(os.path.expanduser("~"), ".fly", "bin", "fly.exe")
    app = app_name()
    print(f"\nStoring TG_API_ID, TG_API_HASH and TG_SESSION as secrets of {app} (not shown)...")
    secrets = f"TG_API_ID={api_id}\nTG_API_HASH={api_hash}\nTG_SESSION={session}\n"
    r = subprocess.run([fly, "secrets", "import", "-a", app], input=secrets, text=True)
    if r.returncode != 0:
        sys.exit("fly could not store the secrets - run deploy-fly.bat once (it signs you in to Fly), then this again.")
    print("\nDone. Fly restarts the app with the new secrets; within a minute the sources panel shows")
    print("tga:chyste_nebo as OK. If the server still runs a version without the Telegram reader, run")
    print("scripts\\release.bat.")


if __name__ == "__main__":
    main()
