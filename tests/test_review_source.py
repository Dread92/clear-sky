"""A reading cannot be reviewed without the words it was read from.

The review panel asks one question: does what the app made of this post match what the post says. Answering
it needs the post in a language the reviewer reads, and a way back to the original — otherwise the reviewer
is judging the app against a transliteration, which is how you end up enforcing a wrong reading forever.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import server  # noqa: E402

POST = {"post_id": "war_monitor/4242", "channel": "war_monitor",
        "ts": "2026-09-17T18:00:00+00:00", "text": "Бандероль курсом на Конотоп",
        "tags": ["banderol_missiles"], "text_en": "Banderol heading for Konotop"}


def _store():
    st = server.Store(":memory:")
    st.add_feed([POST])
    return st


def test_a_case_finds_the_post_it_came_from():
    st = _store()
    post_id, text_en = st.feed_find(POST["channel"], POST["text"])
    assert post_id == "war_monitor/4242"        # https://t.me/<post_id> is the link in the panel
    assert text_en == "Banderol heading for Konotop"


def test_a_case_with_no_stored_post_says_so_rather_than_guessing():
    st = _store()
    assert st.feed_find("war_monitor", "щось чого ніколи не було") == (None, None)
    assert st.feed_find("another_channel", POST["text"]) == (None, None)


def test_the_channel_has_to_match():
    """Two channels relay the same text constantly. Linking a case to the wrong channel's copy would send a
    reviewer to a post that is not the one the case was built from."""
    st = _store()
    assert st.feed_find("eRadarrua", POST["text"])[0] is None


def test_the_translation_endpoint_is_behind_the_dashboard_key():
    """It spends a network call per press. Anonymous visitors do not get to spend them."""
    import re
    src = open(os.path.join(ROOT, "app", "server.py"), encoding="utf-8").read()
    body = src[src.index('if u.path == "/api/corpus/translate":'):]
    body = body[:body.index("\n            if u.path") if "\n            if u.path" in body else 1200]
    assert "_admin_ok" in body, "the translate endpoint no longer checks the dashboard key"
    assert re.search(r"force", body), "there is no way to ask for a fresh translation"


def test_a_failed_translation_is_never_cached_as_a_good_one():
    """translate() falls back to the glossary when the service is unreachable. Storing that under the same key
    as a real translation would freeze a transliteration in place forever."""
    src = open(os.path.join(ROOT, "app", "server.py"), encoding="utf-8").read()
    body = src[src.index('if u.path == "/api/corpus/translate":'):]
    body = body[:body.index("def ", 10)] if "def " in body[10:] else body
    i = body.index("kv_set")
    guard = body[max(0, i - 260):i]
    assert "LAST_OK" in guard, "the cache write is not guarded by whether the service actually answered"
