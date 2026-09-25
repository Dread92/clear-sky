"""chyste_nebo, through the Telegram API.

Its owner switched the web preview off, so the preview reader sees a landing page and nothing else — and it is the
channel that states drone heights most often. The API reader signs in once (scripts/telegram-login.bat) and feeds
every post into exactly the same path as the preview reader. These tests pin that it is the same path, that the
preview reader stands aside for a channel the API is reading, and that the session never leaves the secret store."""
import os
import re
from datetime import datetime, timezone
from types import SimpleNamespace

import server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = open(os.path.join(ROOT, "app", "server.py"), encoding="utf-8").read()
LOGIN = open(os.path.join(ROOT, "scripts", "telegram_login.py"), encoding="utf-8").read()


def _tg(**cfg):
    st = server.State(server.Store(":memory:"), cfg)
    return st, server.Telegram(st, cfg)


def test_it_only_runs_with_all_three_secrets():
    assert not server.TelegramAPI.configured({"telegram_api_channels": ["chyste_nebo"]})
    assert not server.TelegramAPI.configured({"tg_api_id": "1", "tg_api_hash": "h", "telegram_api_channels": ["chyste_nebo"]})
    assert server.TelegramAPI.configured({"tg_api_id": "1", "tg_api_hash": "h", "tg_session": "s", "telegram_api_channels": ["chyste_nebo"]})


def test_the_secrets_come_from_the_environment():
    i = SRC.index("def load_config(")
    body = SRC[i:SRC.index("return cfg", i)]
    for env in ("TG_API_ID", "TG_API_HASH", "TG_SESSION"):
        assert f'"{env}"' in body
    assert "chyste_nebo" in server.DEFAULT_CONFIG["telegram_api_channels"]


def test_a_message_becomes_the_same_post_the_preview_reader_would_make():
    msg = SimpleNamespace(id=4321, date=datetime(2026, 9, 25, 20, 1, 5, tzinfo=timezone.utc), message="Шахед на Вишгород, висота 600")
    post_id, ts, text = server.TelegramAPI.raw_of("chyste_nebo", msg)
    assert post_id == "chyste_nebo/4321" and ts.startswith("2026-09-25T20:01:05") and "висота 600" in text


def test_the_api_posts_go_through_the_same_ingest():
    st, tg = _tg()
    new = tg.ingest("chyste_nebo", [("chyste_nebo/1", "2026-09-25T20:01:05+00:00", "Шахед на Вишгород, висота 600")], "tga:chyste_nebo")
    assert len(new) == 1 and st.store.has_post("chyste_nebo/1")
    assert st.sources["tga:chyste_nebo"]["ok"]
    # read again (catch-up after an update already delivered it): stored once
    assert tg.ingest("chyste_nebo", [("chyste_nebo/1", "2026-09-25T20:01:05+00:00", "Шахед на Вишгород, висота 600")], "tga:chyste_nebo") == []


def test_its_heights_are_read():
    import geo
    ms = geo.parse_for_channel("chyste_nebo", "Шахед на Вишгород, висота 600")
    assert ms and (ms[0].get("alt") or {}).get("m") == 600


def test_the_preview_reader_stands_aside(monkeypatch):
    st, tg = _tg()
    tg.api_channels.add("chyste_nebo")
    def boom(*a, **k):
        raise AssertionError("fetched t.me/s/ for a channel the API is reading")
    monkeypatch.setattr(server, "http_get", boom)
    tg.poll("chyste_nebo")        # no request, no "failed source"
    assert "tg:chyste_nebo" not in st.sources


def test_the_session_is_never_printed_logged_or_written():
    for line in LOGIN.splitlines():
        if "print(" in line:
            assert not re.search(r"\{(session|api_hash|secrets)\}", line), line
    assert "open(" not in LOGIN.split("def app_name")[1].split("def main")[1], "the login script writes a file"
    assert "fly\", \"secrets\", \"import\"" in LOGIN.replace("'", '"') or '"secrets", "import"' in LOGIN
    i = SRC.index("class TelegramAPI(")
    body = SRC[i:SRC.index("\nclass ", i + 10)]
    assert not re.search(r"log\([^)]*tg_session", body)
    assert "tg_session" not in SRC[SRC.index('"config": {'):SRC.index('"config": {') + 300] if '"config": {' in SRC else True


def test_it_reads_the_channel_and_never_the_accounts_update_stream():
    """25 Sep 2026: with updates on, a personal account's whole update stream took the server down within minutes
    of the first sign-in. The reader polls the channel it needs, nothing else."""
    body = SRC[SRC.index("class TelegramAPI"):SRC.index("class Watchdog")]
    assert "receive_updates=False" in body and "receive_updates=True" not in body
    assert "events.NewMessage" not in body and "run_until_disconnected" not in body
    assert "asyncio.to_thread(self.tg.ingest" in body       # parsing and the database off the event loop
