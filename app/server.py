#!/usr/bin/env python3
"""
UA Alerts — local air-raid alert tracker for Ukraine.

Polls official / semi-official sources, keeps a local history (SQLite),
serves a live dashboard on http://localhost:8642 and pushes events to it (SSE).

Sources (all optional except at least one alert source):
  * alerts.in.ua API           — primary, needs token   (config.alerts_in_ua_token)
  * api.ukrainealarm.com API   — official app backend, needs key (config.ukrainealarm_key)
  * ubilling.net.ua mirror     — keyless fallback, oblast-level only
  * Telegram public mirrors    — t.me/s/<channel>, e.g. Air Force (kpszsu) for threat details

Standard library only. Optional: `pip install plyer` for OS desktop notifications.
"""
import argparse
import bisect
import collections
import gzip
import hashlib
import html
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # sibling modules, however the file is launched

try:
    import geo
except Exception:  # pragma: no cover
    geo = None
try:
    import push as _push
except Exception:
    _push = None
try:
    import translate as _tr
except Exception:  # pragma: no cover
    _tr = None


def to_en(text):
    if not _tr or not text:
        return text
    try:
        return _tr.translate(text)
    except Exception:
        return text

BASE = os.path.dirname(os.path.abspath(__file__))
# the code lives in app/, everything else (config, database, static assets) at the repository root
ROOT = os.path.dirname(BASE) if os.path.basename(BASE) == "app" else BASE
CONFIG_PATH = os.environ.get("CONFIG_PATH") or os.path.join(ROOT, "config.json")
DB_PATH = os.environ.get("DB_PATH") or os.path.join(ROOT, "alerts.sqlite")
STATIC = os.path.join(ROOT, "static")

DEFAULT_CONFIG = {
    "port": 8642,
    "bind": "0.0.0.0",
    "marker_ttl_minutes": 45,
    "track_stale_minutes": 5,
    "alerts_in_ua_token": "",
    "ukrainealarm_key": "",
    "use_ubilling_fallback": True,
    "use_siren_proxy": True,
    "telegram_api_channels": ["chyste_nebo"],   # read through the Telegram API (web preview off); needs TG_* secrets
    "deepl_key": "",                  # machine translation of the feed (EN/FR); or env DEEPL_KEY
    "google_translate_key": "",       # alternative: Google Cloud Translation; or env GOOGLE_TRANSLATE_KEY          # keyless proxy of the official API (siren.pp.ua) — raion-level alerts without a key
    "poll_ukrainealarm_seconds": 10,
    "telegram_channels": ["kyiv_airdef", "chyste_nebo", "kievinfo_kyiv", "war_monitor", "eRadarrua", "kpszsu"],   # informational: AUTHORITATIVE_CHANNELS is what is read
    "favourites": ["31", "14"],
    "poll_alerts_seconds": 15,
    "poll_ubilling_seconds": 30,
    "poll_telegram_seconds": 30,
    "poll_telegram_missile_seconds": 10,
    "history_backfill_hours": 6,
    "desktop_notifications": True,
    "access_key": "",
    "demo": False,
}

# ---------------------------------------------------------------------------
# Oblast table (alerts.in.ua UIDs) — order = /iot/active_air_raid_alerts_by_oblast.json
# ---------------------------------------------------------------------------
OBLASTS = [
    ("29", "Автономна Республіка Крим", "Crimea"),
    ("8", "Волинська область", "Volyn"),
    ("4", "Вінницька область", "Vinnytsia"),
    ("9", "Дніпропетровська область", "Dnipropetrovsk"),
    ("28", "Донецька область", "Donetsk"),
    ("10", "Житомирська область", "Zhytomyr"),
    ("11", "Закарпатська область", "Zakarpattia"),
    ("12", "Запорізька область", "Zaporizhzhia"),
    ("13", "Івано-Франківська область", "Ivano-Frankivsk"),
    ("31", "м. Київ", "Kyiv city"),
    ("14", "Київська область", "Kyiv oblast"),
    ("15", "Кіровоградська область", "Kirovohrad"),
    ("16", "Луганська область", "Luhansk"),
    ("27", "Львівська область", "Lviv"),
    ("17", "Миколаївська область", "Mykolaiv"),
    ("18", "Одеська область", "Odesa"),
    ("19", "Полтавська область", "Poltava"),
    ("5", "Рівненська область", "Rivne"),
    ("30", "м. Севастополь", "Sevastopol"),
    ("20", "Сумська область", "Sumy"),
    ("21", "Тернопільська область", "Ternopil"),
    ("22", "Харківська область", "Kharkiv"),
    ("23", "Херсонська область", "Kherson"),
    ("3", "Хмельницька область", "Khmelnytskyi"),
    ("24", "Черкаська область", "Cherkasy"),
    ("26", "Чернівецька область", "Chernivtsi"),
    ("25", "Чернігівська область", "Chernihiv"),
]
UID_BY_UK = {uk: uid for uid, uk, _ in OBLASTS}
NAME_BY_UID = {uid: (uk, en) for uid, uk, en in OBLASTS}


def norm_uk(s):
    """Loose match of an oblast name in Ukrainian (handles 'Київська', 'м. Київ', 'Крим'...)."""
    s = (s or "").strip().lower()
    for uid, uk, _ in OBLASTS:
        if s == uk.lower():
            return uid
    stem = re.sub(r"\s*(область|обл\.?)\s*$", "", s)
    for uid, uk, _ in OBLASTS:
        ukl = uk.lower()
        if stem and (ukl.startswith(stem) or stem.startswith(ukl.replace(" область", ""))):
            return uid
    if "крим" in s:
        return "29"
    if s in ("київ", "м. київ", "м.київ", "kyiv"):
        return "31"
    if "севастополь" in s:
        return "30"
    return None


# ---------------------------------------------------------------------------
# Threat keyword tagging for Telegram feed
# ---------------------------------------------------------------------------
FEED_TAGS = [
    ("drones", re.compile(r"бпла|шахед|дрон|безпілотн|мопед|реактив|\bбп\b", re.I)),
    ("ballistic_missiles", re.compile(r"баліст|іскандер|кінжал|kn-?23|балістич", re.I)),
    ("banderol_missiles", re.compile(r"бандерол", re.I)),
    ("cruise_missiles", re.compile(r"крилат|калібр|калиб|х-?101|х-?555|х-?59|х-?69|х-?22|х-?32|ракет", re.I)),
    # the channels decline it and drop the hyphen ("Мігну31к в небе"), so міг/миг/mig then 31 within three letters
    ("mig31k_departure", re.compile(r"м[іи]г\w{0,3}\s?-?\s?31|mig\w{0,3}\s?-?\s?31", re.I)),
    ("strategic_aircraft_activity", re.compile(r"ту-?95|ту-?160|ту-?22|стратегічн", re.I)),
    ("tactic_aircraft_activity", re.compile(r"тактичн|су-?34|су-?35|су-?25|су-?24", re.I)),
    ("guided_aerial_bombs", re.compile(r"каб|керован.*бомб", re.I)),
    ("air_defense", re.compile(r"ппо|протиповітрян|збит|знищен", re.I)),
    ("clear", re.compile(r"відбій|загроза минула|небезпека минула|\bчисто\b|посадк|приземл", re.I)),
    ("impact", re.compile(r"вибух|приліт|влучанн|уражен", re.I)),
    ("alert", re.compile(r"тривог|укритт|небезпек|загроз|сирен", re.I)),
]
THREAT_TAGS = {n for n, _ in FEED_TAGS}


# The only Telegram channels this app reads, for Kyiv and the oblasts around it.
#
# This is deliberately in code and not in config.json. The config on each machine already lists thirty-odd
# channels, and a config-level whitelist would change nothing until someone edited every deployed config by
# hand — the old list would keep running silently. Here, the list IS the behaviour: anything else in the
# config is ignored and logged once at start.
#
# kyiv_airdef, chyste_nebo and kievinfo_kyiv are the fast live trackers; war_monitor and eRadarrua the wider
# picture; kpszsu is the Air Force itself. None of them sets an alert's colour — see OFFICIAL_ALERTS_FROM_TELEGRAM.
AUTHORITATIVE_CHANNELS = ["kyiv_airdef", "chyste_nebo", "kievinfo_kyiv", "war_monitor", "eRadarrua", "kpszsu"]

# Whether a Telegram post may start or end an alert. It may not. The colour of the alert — red, yellow,
# green — comes only from the official «Повітряна тривога» data (alerts.in.ua / ukrainealarm, or the ubilling
# mirror of it). A channel writing "відбій" or "чисто" turned the band green while the government app was still
# red; the one thing this app must never do is tell somebody it is over before the state does.
OFFICIAL_ALERTS_FROM_TELEGRAM = False

LIVE_TAGS = {"drones", "ballistic_missiles", "cruise_missiles", "banderol_missiles", "unspecified_missiles",
             "mig31k_departure", "strategic_aircraft_activity", "tactic_aircraft_activity", "guided_aerial_bombs", "clear"}
# Mixed channels: live tracking at night, city news in the day. Only short posts that read as a threat are kept.
NEWS_CHANNELS = {"kyiv_times_official", "eRadarrua", "kievinfo_kyiv"}


def is_relevant(text, tags, channel=None):
    """Keep only posts about the air situation: a threat / alert / outcome keyword, a parsed position, or an
    official alert message. Everything else (news, fundraising, culture, ads) is dropped before storage."""
    if channel in NEWS_CHANNELS:
        if channel == "eRadarrua" and "◦" in text:
            return True
        return bool(LIVE_TAGS & set(tags) or "geo" in tags) and len(text) <= 350
    if geo and channel in geo.OFFICIAL_PARSERS and geo.OFFICIAL_PARSERS[channel][0] == "etryvoga":
        return not text.lower().startswith("мапа тривог")    # nationwide raion alerts: everything except the hourly summary
    if THREAT_TAGS & set(tags) or "geo" in tags:
        return True
    if geo and channel in geo.OFFICIAL_PARSERS:
        kind = geo.OFFICIAL_PARSERS[channel][0]
        try:
            r = {"koda": geo.parse_koda, "kmda": geo.parse_kmda, "etryvoga": geo.parse_etryvoga, "eradar_alert": geo.parse_eradar_alert}.get(kind, geo.parse_city_siren)(text)
        except Exception:
            r = None
        return bool(r)
    return False
KYIV_RE = re.compile(r"київ|столиц|бровар|бориспіл|бучан|фастів|вишгород|обух|біла церкв", re.I)
# oblasts around Kyiv: Zhytomyr 10, Chernihiv 25, Sumy 20, Poltava 19, Cherkasy 24, Vinnytsia 4
REGION_UIDS = {"14", "31", "10", "25", "20", "19", "24", "4"}
# channels that speak for one surrounding oblast (their posts are "region", not "kyiv", unless Kyiv is named)
REGION_CHANNELS = {"cherkasy_alerts", "cherkasy_monitor", "poltavskaODA", "zhytomyrskaODA", "chernihiv_alert", "Zhytomyr_alert", "sumy_alert", "sumy_alerts"}
REGION_RE = re.compile(r"житомир|чернігів|чернигов|сум(и|ськ|щин)|полтав|черкас|вінниц|ніжин|прилук|конотоп|шостк|охтир|ромн|кременчу|лубн|миргород|умань|уман[сь]|золотонош|бердич|коростен|звягел|гайсин|жмерин|тульчин", re.I)


SCOPE_KM = 330          # Kyiv to the far edge of Sumy or Vinnytsia oblast


def in_scope(m):
    uid = m.get("oblast_uid")
    if uid is not None:
        return str(uid) in REGION_UIDS
    if m.get("lat") is None or m.get("lon") is None:
        return True          # an outcome with no place of its own is resolved against its track later
    return haversine_km(50.45, 30.52, m["lat"], m["lon"]) <= SCOPE_KM


# nightly / daily *assessments* ("Загальна оцінка загроз на ніч…", "#обстановка", "може відбутись у будь-який момент") describe
# what MIGHT happen — they must never light the ballistic / MiG strip or place a marker. They keep only 'forecast' + 'alert'.
FORECAST_RX = re.compile(r"оцінка\s+загроз|загальна\s+оцінка|прогноз\s+(?:на|загроз)|#обстановка|на\s+ніч\s+\d|може\s+відбутись\s+у\s+будь-який\s+момент|впродовж\s+ночі\s+(?:ймовірн|можлив)|(?:ймовірн|можлив)\w*\s+(?:додаткові\s+)?запуск", re.I)


def tag_feed_text(text, channel=None):
    tags = [name for name, rx in FEED_TAGS if rx.search(text)]
    if FORECAST_RX.search(text) and not re.search(r"\bпуск\b|зафіксовано\s+пуск|швидкісн\w*\s+ціл|зліт\s+міг|злетів|у\s+повітрі\s+міг", text, re.I):
        return [t for t in tags if t == "alert"] + ["forecast"]
    # An article is tagged as one and nothing else. Its threat words used to become threat tags, and a
    # ballistic_missiles tag on a news item is what lit "БАЛІСТИЧНА ЗАГРОЗА — НЕГАЙНО В УКРИТТЯ" on the page.
    # After the forecast check on purpose: a nightly assessment is a forecast, which says more than "news".
    if geo and geo.looks_like_news(geo._norm(text)):
        return ["news"]
    if KYIV_RE.search(text):
        tags.append("kyiv")
    if REGION_RE.search(text):
        tags.append("region")
    if geo:
        try:
            ms = geo.parse_for_channel(channel, text) if channel else geo.parse_post(text)
        except Exception:
            ms = []
        for m in ms:
            if m["type"] not in tags:
                tags.append(m["type"])
            if m.get("oblast_uid") in ("14", "31") and "kyiv" not in tags:
                tags.append("kyiv")
            if m.get("oblast_uid") in REGION_UIDS and "region" not in tags:
                tags.append("region")
        if ms and "geo" not in tags:
            tags.append("geo")
    # A Banderol post says "ракета" too, and the keyword pass tags that as a cruise missile. Once the post has been
    # read as a Banderol, the vaguer tag goes — otherwise the feed shows a "cruise missiles" chip on it.
    if "banderol_missiles" in tags:
        tags = [t for t in tags if t not in ("cruise_missiles", "unspecified_missiles")]
    if OFFICIAL_ALERTS_FROM_TELEGRAM and channel and geo and channel in geo.OFFICIAL_PARSERS:
        tags.append("official")
    if geo and channel in geo.OFFICIAL_PARSERS and geo.OFFICIAL_PARSERS[channel][0] == "city" and geo.OFFICIAL_PARSERS[channel][1] not in REGION_UIDS:
        tags.append("ua")
        if "kyiv" in tags and not KYIV_RE.search(text):
            tags.remove("kyiv")
    if geo and channel in geo.OFFICIAL_PARSERS and geo.OFFICIAL_PARSERS[channel][0] in ("etryvoga", "eradar_alert") and "region" not in tags and "kyiv" not in tags:
        tags.append("ua")
    if channel in REGION_CHANNELS:
        if "region" not in tags:
            tags.append("region")
        if "kyiv" in tags and not KYIV_RE.search(text):
            tags.remove("kyiv")
    return tags


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


BUILD_FILES = ("static/kyiv.html", "static/light.html", "static/light-map.json", "static/glyphs.js", "static/i18n.js", "static/sw.js", "app/server.py", "app/geo.py")


def build_id():
    """A short id that changes whenever the front end or the service changes. An open page compares it with
    the one it loaded and offers a reload — that is how someone running for three days on an old build finds out.

    It hashes the CONTENT of those files, not their timestamps. Timestamps change on every container build even
    when nothing did, and are different on two machines holding identical code — so the same id here and in
    production means the same code here and in production, and that is a question worth being able to answer
    without guessing at a screenshot."""
    h = hashlib.sha1()
    for rel in BUILD_FILES:
        try:
            with open(os.path.join(ROOT, rel), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"?")
        h.update(b"\0")
    return h.hexdigest()[:8]


# The version the front end shows in its footer, kept here too so /api/version can answer "what is actually
# running" without anybody reading it off a screenshot. tests/test_version.py pins the two to each other.
APP_VERSION = "1.24"
BUILD = None    # filled at startup


# ---------------------------------------------------------------------------
# Usage counters — aggregates only.
#
# The people using this app include police and soldiers during attacks. Nothing here identifies anybody:
# no IP is stored, no path history, no location, no per-device timeline. A device is counted once per day
# through a hash of (IP + user-agent + a salt that is thrown away and regenerated every day), so the same
# person cannot be followed from one day to the next, and the hash cannot be turned back into an address.
# Kept: a daily row of totals. That is enough to see whether the app is being used, and nothing more.
class Usage:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.day = None
        self.salt = None
        self.seen = set()          # hashes seen today, in memory only, dropped at midnight
        # Pings are counted in memory and written in batches. During a ballistic alert every client pings once
        # a second: one committed transaction per ping would put thousands of writes a second through a single
        # SQLite connection and stall the alert path itself — a counter is never allowed to do that.
        self.pending = 0
        self.flushed = 0.0
        with store.lock:
            store.conn.execute("""CREATE TABLE IF NOT EXISTS usage(
                day TEXT PRIMARY KEY, devices INTEGER, loads INTEGER, pings INTEGER,
                lang TEXT, pwa INTEGER, lite INTEGER)""")
            # The day's salt and the hashes seen under it have to OUTLIVE THE PROCESS. They used to be a
            # fresh random salt and an empty set at every start, so every deploy, every crash and every
            # auto-stop/start counted the same three people again — 62 "devices" out of a handful of readers.
            # A counter that inflates itself is a counter that says nothing, and this one sits on a dashboard
            # next to figures that are carefully honest about what they count.
            store.conn.execute("CREATE TABLE IF NOT EXISTS usage_seen(day TEXT, h TEXT, PRIMARY KEY(day,h))")
            store.conn.commit()

    def _roll(self):
        d = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d")
        if d != self.day:
            if self.day is not None and self.pending:
                self._flush(self.day, self.pending)          # yesterday's tail is not thrown away
                self.pending = 0
            self.day = d
            self.salt = self._salt_for(d)
            self.seen = self._seen_for(d)
        return d

    def _salt_for(self, day):
        """One salt per day, kept only for that day. New day, new salt: nobody can be followed across days."""
        import binascii
        k = f"usage_salt:{day}"
        cur = self.store.kv_get(k)
        if cur:
            try:
                return binascii.unhexlify(cur)
            except Exception:
                pass
        salt = os.urandom(16)
        self.store.kv_set(k, binascii.hexlify(salt).decode())
        with self.store.lock:
            # yesterday's salt and hashes are destroyed, which is what makes the daily hash un-followable
            self.store.conn.execute("DELETE FROM kv WHERE k LIKE 'usage_salt:%' AND k<>?", (k,))
            self.store.conn.execute("DELETE FROM usage_seen WHERE day<>?", (day,))
            self.store.conn.commit()
        return salt

    def _seen_for(self, day):
        with self.store.lock:
            try:
                rows = self.store.conn.execute("SELECT h FROM usage_seen WHERE day=?", (day,)).fetchall()
            except sqlite3.OperationalError:
                return set()
        return {r[0] for r in rows}

    def _flush(self, day, n):
        with self.store.lock:
            c = self.store.conn
            c.execute("INSERT OR IGNORE INTO usage(day,devices,loads,pings,lang,pwa,lite) VALUES(?,0,0,0,'{}',0,0)", (day,))
            c.execute("UPDATE usage SET pings=pings+? WHERE day=?", (n, day))
            c.commit()

    def hit(self, ip, ua, kind, lang=None, pwa=False, lite=False):
        try:
            import hashlib
            due = 0
            with self.lock:
                day = self._roll()
                h = hashlib.blake2s(self.salt + (ip or "").encode() + (ua or "")[:120].encode(), digest_size=8).hexdigest()
                new = h not in self.seen
                if new:
                    self.seen.add(h)
                    try:
                        with self.store.lock:
                            self.store.conn.execute("INSERT OR IGNORE INTO usage_seen(day,h) VALUES(?,?)", (day, h))
                            self.store.conn.commit()
                    except Exception:
                        pass
                if kind != "load":
                    self.pending += 1
                    now = time.time()
                    due = self.pending if (self.pending >= 200 or now - self.flushed > 20) else 0
                    if due:
                        self.pending, self.flushed = 0, now
            if kind != "load" and not new:
                if due:
                    self._flush(day, due)
                return                    # the common case writes nothing at all
            with self.store.lock:
                c = self.store.conn
                c.execute("INSERT OR IGNORE INTO usage(day,devices,loads,pings,lang,pwa,lite) VALUES(?,0,0,0,'{}',0,0)", (day,))
                if new:
                    c.execute("UPDATE usage SET devices=devices+1 WHERE day=?", (day,))
                if kind == "load":
                    c.execute("UPDATE usage SET loads=loads+1 WHERE day=?", (day,))
                    if pwa:
                        c.execute("UPDATE usage SET pwa=pwa+1 WHERE day=?", (day,))
                    if lite:
                        c.execute("UPDATE usage SET lite=lite+1 WHERE day=?", (day,))
                    if lang in ("en", "uk", "fr"):
                        row = c.execute("SELECT lang FROM usage WHERE day=?", (day,)).fetchone()
                        d = json.loads(row[0] or "{}") if row else {}
                        d[lang] = d.get(lang, 0) + 1
                        c.execute("UPDATE usage SET lang=? WHERE day=?", (json.dumps(d), day))
                elif due:
                    c.execute("UPDATE usage SET pings=pings+? WHERE day=?", (due, day))   # `due` already counts this one
                c.commit()
        except Exception:
            pass       # counting must never get in the way of an alert

    def report(self, days=30):
        with self.store.lock:
            rows = self.store.conn.execute(
                "SELECT day,devices,loads,pings,lang,pwa,lite FROM usage ORDER BY day DESC LIMIT ?", (days,)).fetchall()
        out = [{"day": r[0], "devices": r[1], "loads": r[2], "pings": r[3],
                "lang": json.loads(r[4] or "{}"), "pwa": r[5], "lite": r[6]} for r in rows]
        with self.lock:
            live, day, tail = len(self.seen), self.day, self.pending
        if tail:                                   # pings counted but not yet written
            for r in out:
                if r["day"] == day:
                    r["pings"] += tail
                    break
        return {"days": out, "today_devices": live,
                "push_subs": len(self.store.push_all()),
                "generated": now_iso()}


def _clean_post(text):
    """What the reader sees: the warning without the channel's ad tail ("Купуємо контент | ❤️")."""
    if not _tr:
        return text
    try:
        return _tr.strip_promo(text)
    except Exception:
        return text


# How long a missile report stays on the map at all. Past this the last position tells you nothing useful even
# as an uncertainty circle: a cruise missile has covered 150 km, a ballistic one has already arrived.
MISSILE_TTL_MIN = 12

AF_SUMMARY_CHANNELS = {"kpszsu", "war_monitor", "monitor_ukr"}   # the Air Force summary and the channels that re-post it verbatim
_AF_NUM = r"(\d{1,3})(?:-?[а-яіїєґ']{1,3})?"
_AF_ATTACK_RX = re.compile(r"(?:противник|ворог|росі\w+|рф)\s+(?:масовано\s+|знову\s+)?атакув\w+", re.I)
_AF_DRONES_RX = re.compile(_AF_NUM + r"\s+(?:ударн\w+\s+|розвідувальн\w+\s+)?(?:БпЛА|безпілотник\w*|дрон\w*)", re.I)
_AF_MISSILE_RX = re.compile(_AF_NUM + r"\s+(?:[а-яіїєґ'\-/]+\s+){0,4}?ракет\w*", re.I)
_AF_DOWN_RX = re.compile(r"(?:збито|знищено|подавлен\w+|збито/подавлено|збито\s*/\s*подавлено)\s+" + _AF_NUM, re.I)


def parse_af_summary(text):
    """Air Force morning summary → {'drones','missiles','down'} (what was launched over Ukraine, per the Air Force), or None.
    Only posts that explicitly say the enemy *attacked with N* something count; live "N shaheds over X" posts do not match."""
    if not text or "атакув" not in text.lower():
        return None
    m = _AF_ATTACK_RX.search(text)
    if not m:
        return None
    seg = text[m.end():m.end() + 900]
    seg = re.split(r"\n\s*\n|Станом на|Основний напрямок", seg, maxsplit=1)[0]
    seg = re.sub(r"\([^)]*\)", " ", seg)                      # "(9 крилатих ... та 4 балістичні)" itemises the total → drop it
    drones = max([int(x) for x in _AF_DRONES_RX.findall(seg)] or [0])
    mseg = seg
    fm = _AF_MISSILE_RX.search(seg)
    if fm:
        colon = seg.find(":", fm.end())
        if 0 <= colon - fm.end() <= 3:                        # "35 ракетами: 6 балістичних, 29 крилатих" → the total, not the list
            mseg = seg[:fm.end()]
    missiles = sum(int(x) for x in _AF_MISSILE_RX.findall(mseg))
    if not drones and not missiles:
        return None
    dm = _AF_DOWN_RX.search(text)
    return {"drones": drones, "missiles": min(missiles, 400), "down": int(dm.group(1)) if dm else 0}


_GZ = {}


def _gzipped(path, body):
    """gzip of a static file, kept until the file changes."""
    k = (path, len(body), hashlib.md5(body).digest())
    if k not in _GZ:
        if len(_GZ) > 64:
            _GZ.clear()
        _GZ[k] = gzip.compress(body, 6)
    return _GZ[k]


def http_get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "ua-alerts-local/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


# A subscription used to carry the exact coordinates of somebody's village, next to the address their phone
# can be reached at. That is the only row in this database that would say where a person sleeps, and the app
# does not need it: proximity alerts ask "is anything within N km", and N is never smaller than 10. The point
# is snapped to a ~10 km grid and the name is dropped — the alerts behave the same, and the row no longer
# points at a house.
HOME_CELL_KM = 10.0
HOME_CELL_LAT = HOME_CELL_KM / 111.0
HOME_CELL_LON = HOME_CELL_KM / 71.0          # at ~50°N, the latitudes this app covers


def coarse_home(home):
    """{lat, lon, radius} snapped to the grid, and nothing else. Anything unrecognised is dropped."""
    if not isinstance(home, dict):
        return {}
    try:
        lat, lon = float(home["lat"]), float(home["lon"])
    except (KeyError, TypeError, ValueError):
        return {}
    out = {"lat": round(round(lat / HOME_CELL_LAT) * HOME_CELL_LAT, 4),
           "lon": round(round(lon / HOME_CELL_LON) * HOME_CELL_LON, 4),
           "cell_km": HOME_CELL_KM}
    try:
        r = float(home.get("radius") or 0)
        if r > 0:
            out["radius"] = min(100.0, max(HOME_CELL_KM, r))
    except (TypeError, ValueError):
        pass
    return out


# ---------------------------------------------------------------------------
# State + storage
# ---------------------------------------------------------------------------
class Store:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        c = self.conn
        c.execute("""CREATE TABLE IF NOT EXISTS alerts(
            key TEXT PRIMARY KEY, source TEXT, location_uid TEXT, location_title TEXT, location_title_en TEXT,
            location_type TEXT, oblast_uid TEXT, alert_type TEXT, alert_level TEXT,
            started_at TEXT, finished_at TEXT, notes TEXT, threats TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT, key TEXT, location_uid TEXT,
            location_title TEXT, alert_type TEXT, oblast_uid TEXT, detail TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS feed(
            post_id TEXT PRIMARY KEY, channel TEXT, ts TEXT, text TEXT, tags TEXT)""")
        cols = [r[1] for r in c.execute("PRAGMA table_info(feed)").fetchall()]
        if "text_en" not in cols:
            c.execute("ALTER TABLE feed ADD COLUMN text_en TEXT")
        if "text_fr" not in cols:
            c.execute("ALTER TABLE feed ADD COLUMN text_fr TEXT")
        if "en_mt" not in cols:     # 1 = the English came from a machine translator, not the offline glossary
            c.execute("ALTER TABLE feed ADD COLUMN en_mt INTEGER")
        c.execute("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS push_subs(endpoint TEXT PRIMARY KEY, sub TEXT, home TEXT, created TEXT, last_ok TEXT)")
        # ---- the record of what the app itself said -------------------------------------------------
        # Markers were recomputed from the feed on every request, and the feed prunes. So "what did the map
        # show at 02:14 last Tuesday" had no answer, the lead time over the official siren could not be
        # measured, and a wrong reading could only be studied while the post that caused it was still there.
        # Each marker is written once, as computed, with the evidence that produced it.
        c.execute("""CREATE TABLE IF NOT EXISTS marker_log(
            id TEXT PRIMARY KEY, ts TEXT, post_id TEXT, channel TEXT, type TEXT, status TEXT,
            place TEXT, oblast_uid TEXT, lon REAL, lat REAL, heading REAL, count INTEGER,
            jet INTEGER, pos_conf TEXT, evidence TEXT)""")
        # Outcomes were derived from the feed every time they were asked for, which made them as short-lived
        # as the posts. They are the app's most-cited numbers; they get a table.
        c.execute("""CREATE TABLE IF NOT EXISTS outcomes(
            id TEXT PRIMARY KEY, ts TEXT, post_id TEXT, channel TEXT, status TEXT, type TEXT,
            place TEXT, oblast_uid TEXT, lon REAL, lat REAL, count INTEGER, pos_conf TEXT)""")
        # Which channels earn being believed first: posts seen, how many the app could read, how many were
        # flagged as wrong afterwards. Counters only — no post text, nothing about any reader.
        c.execute("""CREATE TABLE IF NOT EXISTS channel_stats(
            day TEXT, channel TEXT, posts INTEGER DEFAULT 0, with_marker INTEGER DEFAULT 0,
            outcomes INTEGER DEFAULT 0, flagged INTEGER DEFAULT 0, PRIMARY KEY(day, channel))""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_alerts_started ON alerts(started_at)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_feed_ts ON feed(ts)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_mlog_ts ON marker_log(ts)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_mlog_obl ON marker_log(oblast_uid, ts)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_out_ts ON outcomes(ts)")
        c.commit()

    def kv_get(self, k):
        with self.lock:
            r = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r[0] if r else None

    def kv_set(self, k, v):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO kv(k,v) VALUES(?,?)", (k, v))
            self.conn.commit()

    def push_add(self, sub, home):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO push_subs(endpoint,sub,home,created,last_ok) VALUES(?,?,?,?,?)",
                              (sub["endpoint"], json.dumps(sub), json.dumps(coarse_home(home)), now_iso(), now_iso()))
            self.conn.commit()

    def coarsen_homes(self):
        """Rewrite every stored home through the grid, once, at startup. Rows written before this existed
        hold an exact village; leaving them would mean the change only protected new subscribers."""
        with self.lock:
            rows = self.conn.execute("SELECT endpoint,home FROM push_subs").fetchall()
        n = 0
        for ep, raw in rows:
            try:
                h = json.loads(raw or "{}")
            except Exception:
                h = {}
            c = coarse_home(h)
            if c != h:
                with self.lock:
                    self.conn.execute("UPDATE push_subs SET home=? WHERE endpoint=?",
                                      (json.dumps(c), ep))
                    self.conn.commit()
                n += 1
        return n

    def push_remove(self, endpoint):
        with self.lock:
            self.conn.execute("DELETE FROM push_subs WHERE endpoint=?", (endpoint,))
            self.conn.commit()

    def push_all(self):
        with self.lock:
            rows = self.conn.execute("SELECT endpoint,sub,home FROM push_subs").fetchall()
        return [{"endpoint": r[0], "sub": json.loads(r[1]), "home": json.loads(r[2] or "{}")} for r in rows]

    # -- flagged readings ---------------------------------------------------
    # The map is read by people who know the ground far better than any parser does. When one of them sees a
    # marker that is wrong, the cheapest possible way to capture it is a button on the marker itself — a
    # workflow that needs a terminal is a workflow that never happens during a raid. What is stored is the
    # post, the reading, and what the person said was wrong with it. Never who they are: no address, no
    # location, and the daily hash that rate-limits them is the same throwaway one the usage counter uses.
    def flag_add(self, row):
        with self.lock:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS flags(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, post_id TEXT, channel TEXT, marker_id TEXT,
                place TEXT, kind TEXT, reason TEXT, note TEXT, day_hash TEXT, text TEXT, done INTEGER DEFAULT 0)""")
            n_today = self.conn.execute(
                "SELECT COUNT(*) FROM flags WHERE day_hash=? AND ts>=?",
                (row.get("day_hash") or "", row["ts"][:10])).fetchone()[0]
            if n_today >= 40:                      # one device cannot flood it; 40 a day is far past honest use
                return False
            self.conn.execute(
                "INSERT INTO flags(ts,post_id,channel,marker_id,place,kind,reason,note,day_hash,text) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (row["ts"], row.get("post_id"), row.get("channel"), row.get("marker_id"), row.get("place"),
                 row.get("kind"), row.get("reason"), (row.get("note") or "")[:400], row.get("day_hash"),
                 (row.get("text") or "")[:1500]))
            self.conn.commit()
        # a reader saying "this reading is wrong" is the strongest signal a channel has about itself
        try:
            self.bump_channel(row["ts"][:10], row.get("channel"), flagged=1)
        except Exception:
            pass
        return True

    def flags(self, limit=200, include_done=False):
        with self.lock:
            try:
                q = ("SELECT id,ts,post_id,channel,marker_id,place,kind,reason,note,text,done FROM flags "
                     + ("" if include_done else "WHERE done=0 ") + "ORDER BY id DESC LIMIT ?")
                rows = self.conn.execute(q, (limit,)).fetchall()
            except sqlite3.OperationalError:
                return []                          # nobody has flagged anything yet
        keys = ("id", "ts", "post_id", "channel", "marker_id", "place", "kind", "reason", "note", "text", "done")
        return [dict(zip(keys, r)) for r in rows]

    # -- corpus review verdicts ---------------------------------------------
    # The corpus itself is a repo file, shipped inside the image and read-only here: anything written to it on
    # a deployed machine would vanish on the next release. So the verdicts live on the volume instead, and get
    # merged back into the repo by `corpus.py pull`. That keeps the reviewing where the reviewer actually is —
    # a phone, in a browser — without pretending the container is the source of truth.
    def corpus_review(self, case_id, state, note=""):
        with self.lock:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS corpus_reviews(
                case_id TEXT PRIMARY KEY, state TEXT, note TEXT, ts TEXT)""")
            self.conn.execute("INSERT OR REPLACE INTO corpus_reviews(case_id,state,note,ts) VALUES(?,?,?,?)",
                              (case_id, state, (note or "")[:300], now_iso()))
            self.conn.commit()

    def corpus_reviews(self):
        with self.lock:
            try:
                rows = self.conn.execute("SELECT case_id,state,note,ts FROM corpus_reviews").fetchall()
            except sqlite3.OperationalError:
                return {}
        return {r[0]: {"state": r[1], "note": r[2], "ts": r[3]} for r in rows}

    def flag_done(self, ids):
        with self.lock:
            self.conn.executemany("UPDATE flags SET done=1 WHERE id=?", [(int(i),) for i in ids])
            self.conn.commit()

    def upsert_alert(self, a):
        with self.lock:
            self.conn.execute("""INSERT INTO alerts(key,source,location_uid,location_title,location_title_en,location_type,
                oblast_uid,alert_type,alert_level,started_at,finished_at,notes,threats)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(key) DO UPDATE SET finished_at=excluded.finished_at, alert_level=excluded.alert_level,
                threats=excluded.threats, notes=excluded.notes, source=excluded.source""",
                (a["key"], a["source"], a["location_uid"], a["location_title"], a.get("location_title_en"),
                 a["location_type"], a["oblast_uid"], a["alert_type"], a.get("alert_level"),
                 a["started_at"], a.get("finished_at"), a.get("notes"), json.dumps(a.get("threats") or [], ensure_ascii=False)))
            self.conn.commit()

    def finish_alert(self, key, finished_at):
        with self.lock:
            self.conn.execute("UPDATE alerts SET finished_at=? WHERE key=? AND finished_at IS NULL", (finished_at, key))
            self.conn.commit()

    def add_event(self, ev):
        with self.lock:
            self.conn.execute("INSERT INTO events(ts,kind,key,location_uid,location_title,alert_type,oblast_uid,detail) VALUES(?,?,?,?,?,?,?,?)",
                              (ev["ts"], ev["kind"], ev.get("key"), ev.get("location_uid"), ev.get("location_title"),
                               ev.get("alert_type"), ev.get("oblast_uid"), json.dumps(ev.get("detail") or {}, ensure_ascii=False)))
            self.conn.commit()


    _seen_ids = None

    def has_post(self, post_id):
        with self.lock:
            if self._seen_ids is None:
                self._seen_ids = {r[0] for r in self.conn.execute("SELECT post_id FROM feed ORDER BY ts DESC LIMIT 5000")}
            return post_id in self._seen_ids

    def mark_seen(self, post_id):
        with self.lock:
            if self._seen_ids is None:
                self._seen_ids = set()
            self._seen_ids.add(post_id)
            if len(self._seen_ids) > 20000:
                self._seen_ids = set(list(self._seen_ids)[-8000:])

    def add_feed(self, posts):
        new = []
        with self.lock:
            if self._seen_ids is None:
                self._seen_ids = set()
            for p in posts:
                self._seen_ids.add(p["post_id"])
                cur = self.conn.execute("INSERT OR IGNORE INTO feed(post_id,channel,ts,text,tags,text_en,en_mt) VALUES(?,?,?,?,?,?,?)",
                                        (p["post_id"], p["channel"], p["ts"], p["text"], json.dumps(p["tags"]), p.get("text_en"),
                                         0 if p.get("en_fallback") else 1))
                if cur.rowcount:
                    new.append(p)
            self.conn.commit()
        # Which channel earns being read first. Counted here because this is the one place a post is known to
        # be NEW — counting in markers() would count the same post again every time the window is recomputed.
        for p in new:
            try:
                day = (p.get("ts") or now_iso())[:10]
                got = 0
                if geo:
                    try:
                        got = 1 if geo.parse_for_channel(p["channel"], _clean_post(p["text"])) else 0
                    except Exception:
                        got = 0
                self.bump_channel(day, p["channel"], posts=1, with_marker=got)
            except Exception:
                pass
        return new

    _fc = (0.0, 0)

    def feed_count(self):
        """Asked by every page's ping, every 1–15 s: counted at most every 3 s."""
        if time.time() - self._fc[0] < 3:
            return self._fc[1]
        with self.lock:
            n = self.conn.execute("SELECT COUNT(*) FROM feed").fetchone()[0]
        self._fc = (time.time(), n)
        return n

    # -- the record of what the app said ------------------------------------------------------------
    def log_markers(self, markers):
        """Write each marker once, the way it was computed. INSERT OR IGNORE on the marker id: markers() is
        called many times a minute and recomputes the same ids, so only the first sighting is kept."""
        if not markers:
            return 0
        rows = []
        for m in markers:
            ev = m.get("evidence") or {}
            rows.append((m.get("id"), m.get("ts"), str(m.get("id", "")).split("#")[0], m.get("channel"),
                         m.get("type"), m.get("status"), m.get("place"), m.get("oblast_uid"),
                         m.get("lon"), m.get("lat"), m.get("heading"), m.get("count"),
                         1 if m.get("jet") else 0,
                         ((ev.get("position") or {}).get("confidence")),
                         json.dumps(ev, ensure_ascii=False)[:4000]))
        with self.lock:
            cur = self.conn.executemany(
                "INSERT OR IGNORE INTO marker_log(id,ts,post_id,channel,type,status,place,oblast_uid,"
                "lon,lat,heading,count,jet,pos_conf,evidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            n = cur.rowcount
            out = [r for r in rows if r[5] in ("impact", "down", "fire", "damage")]
            if out:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO outcomes(id,ts,post_id,channel,status,type,place,oblast_uid,"
                    "lon,lat,count,pos_conf) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(r[0], r[1], r[2], r[3], r[5], r[4], r[6], r[7], r[8], r[9], r[11], r[13]) for r in out])
            self.conn.commit()
        return max(0, n)

    def bump_channel(self, day, channel, **counts):
        """Counters per channel per day. Nothing about any reader, nothing about any post's content."""
        if not channel:
            return
        fields = [k for k in ("posts", "with_marker", "outcomes", "flagged") if counts.get(k)]
        if not fields:
            return
        with self.lock:
            self.conn.execute("INSERT OR IGNORE INTO channel_stats(day,channel) VALUES(?,?)", (day, channel))
            self.conn.execute("UPDATE channel_stats SET " + ", ".join(f"{f}={f}+?" for f in fields)
                              + " WHERE day=? AND channel=?", [int(counts[f]) for f in fields] + [day, channel])
            self.conn.commit()

    def channel_report(self, days=30):
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        with self.lock:
            rows = self.conn.execute(
                "SELECT channel, SUM(posts), SUM(with_marker), SUM(outcomes), SUM(flagged) "
                "FROM channel_stats WHERE day>=? GROUP BY channel ORDER BY SUM(posts) DESC", (since,)).fetchall()
        return [{"channel": r[0], "posts": r[1] or 0, "with_marker": r[2] or 0,
                 "outcomes": r[3] or 0, "flagged": r[4] or 0} for r in rows]

    def markers_between(self, start, end, oblast_uid=None, only_live=True):
        """What the app had on the map in a window — used to measure how far ahead of the siren it was."""
        q = "SELECT ts,type,status,place,oblast_uid,channel FROM marker_log WHERE ts>=? AND ts<=?"
        a = [start, end]
        if oblast_uid:
            q += " AND oblast_uid=?"
            a.append(oblast_uid)
        if only_live:
            q += " AND status IS NULL"
        with self.lock:
            rows = self.conn.execute(q + " ORDER BY ts", a).fetchall()
        return [{"ts": r[0], "type": r[1], "status": r[2], "place": r[3], "oblast_uid": r[4], "channel": r[5]}
                for r in rows]

    def outcomes_between(self, start, end, oblast_uid=None):
        q = "SELECT ts,status,type,place,oblast_uid,count FROM outcomes WHERE ts>=? AND ts<=?"
        a = [start, end]
        if oblast_uid:
            q += " AND oblast_uid=?"
            a.append(oblast_uid)
        with self.lock:
            rows = self.conn.execute(q + " ORDER BY ts", a).fetchall()
        return [{"ts": r[0], "status": r[1], "type": r[2], "place": r[3], "oblast_uid": r[4], "count": r[5]}
                for r in rows]

    def prune_log(self, days=120):
        cut = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self.lock:
            self.conn.execute("DELETE FROM marker_log WHERE ts<?", (cut,))
            self.conn.commit()

    def feed_find(self, channel, text):
        """The stored post a corpus case was harvested from: its id (so the review panel can link to it on
        Telegram) and the English text that was translated once, when it arrived. Matched on the exact text,
        because that is the only thing a corpus case keeps. Returns (post_id, text_en) or (None, None)."""
        text = (text or "").strip()
        if not text:
            return (None, None)
        with self.lock:
            row = self.conn.execute(
                "SELECT post_id,text_en FROM feed WHERE channel=? AND text=? ORDER BY ts DESC LIMIT 1",
                (channel, text)).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def feed_since(self, minutes, limit=200, translate=True):
        since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        with self.lock:
            rows = self.conn.execute("SELECT post_id,channel,ts,text,tags,text_en FROM feed WHERE ts>=? ORDER BY ts DESC LIMIT ?", (since, limit)).fetchall()
        # Never a network call here: this runs inside the marks' computation. It used to call the machine
        # translator for any post without an English text — one call per post, each up to 6 s, on every request
        # for the marks — and during an attack the pages' requests piled up behind it until the server froze.
        return [{"post_id": r[0], "channel": r[1], "ts": r[2], "text": _clean_post(r[3]), "tags": json.loads(r[4]),
                 "text_en": r[5] or (_tr.translate_offline(r[3]) if (translate and _tr) else None)} for r in rows]

    def feed(self, limit=80):
        with self.lock:
            rows = self.conn.execute("SELECT post_id,channel,ts,text,tags,text_en,text_fr,en_mt FROM feed ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"post_id": r[0], "channel": r[1], "ts": r[2], "text": _clean_post(r[3]), "tags": json.loads(r[4]),
                 "text_en": r[5] or (_tr.translate_offline(r[3]) if _tr else r[3]), "text_fr": r[6], "en_mt": bool(r[7])} for r in rows]

    def set_translation(self, post_id, lang, text):
        with self.lock:
            if lang == "en":
                self.conn.execute("UPDATE feed SET text_en=?, en_mt=1 WHERE post_id=?", (text, post_id))
            elif lang == "fr":
                self.conn.execute("UPDATE feed SET text_fr=? WHERE post_id=?", (text, post_id))
            self.conn.commit()

    def history(self, hours=24, oblast_uids=None):
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        q = "SELECT key,source,location_uid,location_title,location_title_en,location_type,oblast_uid,alert_type,alert_level,started_at,finished_at,notes,threats FROM alerts WHERE (finished_at IS NULL OR finished_at>=?)"
        args = [since]
        if oblast_uids:
            q += " AND oblast_uid IN (%s)" % ",".join("?" * len(oblast_uids))
            args += list(oblast_uids)
        q += " ORDER BY started_at DESC LIMIT 2000"
        with self.lock:
            rows = self.conn.execute(q, args).fetchall()
        return [self._row(r) for r in rows]

    def events(self, limit=200):
        with self.lock:
            rows = self.conn.execute("SELECT ts,kind,key,location_uid,location_title,alert_type,oblast_uid,detail FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r[0], "kind": r[1], "key": r[2], "location_uid": r[3], "location_title": r[4],
                 "alert_type": r[5], "oblast_uid": r[6], "detail": json.loads(r[7])} for r in rows]

    @staticmethod
    def _row(r):
        return {"key": r[0], "source": r[1], "location_uid": r[2], "location_title": r[3], "location_title_en": r[4],
                "location_type": r[5], "oblast_uid": r[6], "alert_type": r[7], "alert_level": r[8],
                "started_at": r[9], "finished_at": r[10], "notes": r[11], "threats": json.loads(r[12] or "[]")}


class State:
    """In-memory current picture + SSE fan-out."""

    def __init__(self, store, cfg):
        self.store = store
        self.cfg = cfg
        self.lock = threading.Lock()
        self.active = {}            # key -> alert dict
        self.sources = {}           # name -> {ok, last, error, ...}
        self.subscribers = []       # list of queues (lists + condition)
        self.cond = threading.Condition()
        self.seq = 0
        self.notifier = Notifier(cfg)

    # -- fan-out --------------------------------------------------------
    def publish(self, event):
        with self.cond:
            self.seq += 1
            event["seq"] = self.seq
            for q in self.subscribers:
                q.append(event)
            self.cond.notify_all()

    def subscribe(self):
        q = []
        with self.cond:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.cond:
            if q in self.subscribers:
                self.subscribers.remove(q)

    # -- source status --------------------------------------------------
    def set_source(self, name, ok, **kw):
        with self.lock:
            self.sources[name] = {"ok": ok, "last": now_iso(), **kw}

    # -- reconciling a full snapshot from a source ----------------------
    eradar = None   # latest @eRadarrua per-oblast group counts: {"ts":…, "counts": {uid: {drones, jet, missiles}}}

    def set_eradar(self, ts, counts):
        with self.lock:
            if self.eradar and self.eradar["ts"] >= ts:
                return
            self.eradar = {"ts": ts, "counts": counts}
        self.publish({"kind": "eradar", "ts": ts, "counts": counts})

    def apply_snapshot(self, source, alerts, authoritative_levels, scope=None):
        """alerts: list of normalised alerts currently active according to `source`.
        authoritative_levels: set of location_type values this source is authoritative for
        (a keyless oblast-only source must not close raion-level alerts it cannot see)."""
        ts = now_iso()
        events = []
        with self.lock:
            incoming = {a["key"]: a for a in alerts}
            # starts / updates
            for key, a in incoming.items():
                old = self.active.get(key)
                if old is None:
                    self.active[key] = a
                    events.append({"kind": "start", "ts": ts, "alert": a})
                else:
                    changed = False
                    if (old.get("threats") or []) != (a.get("threats") or []):
                        old_types = {t.get("threat_type") for t in old.get("threats") or []}
                        for t in a.get("threats") or []:
                            if t.get("threat_type") not in old_types:
                                events.append({"kind": "threat", "ts": ts, "alert": a, "threat": t})
                        changed = True
                    if old.get("alert_level") != a.get("alert_level"):
                        changed = True
                    if changed:
                        self.active[key] = a
                        events.append({"kind": "update", "ts": ts, "alert": a})
            # ends
            for key in list(self.active.keys()):
                a = self.active[key]
                if key in incoming:
                    continue
                # close only what this source is allowed to see: its own alerts, or
                # any alert at a level it is authoritative for
                if a["source"] != source and (a["location_type"] not in authoritative_levels or (scope and a["oblast_uid"] not in scope)):
                    continue
                a = dict(a, finished_at=ts)
                del self.active[key]
                events.append({"kind": "end", "ts": ts, "alert": a})
        # persist + notify
        for ev in events:
            a = ev["alert"]
            if ev["kind"] == "end":
                self.store.finish_alert(a["key"], ev["ts"])
            else:
                self.store.upsert_alert(a)
            self.store.add_event({"ts": ev["ts"], "kind": ev["kind"], "key": a["key"], "location_uid": a["location_uid"],
                                  "location_title": a["location_title"], "alert_type": a["alert_type"],
                                  "oblast_uid": a["oblast_uid"], "detail": ev.get("threat") or {}})
            self.publish(ev)
            self.notifier.maybe(ev, self.cfg.get("favourites") or [])
            if getattr(self, "pusher", None):
                try:
                    self.pusher.on_event(ev)
                except Exception as e:
                    log("push:", e)
        if events:
            log(f"[{source}] {len(events)} event(s):", ", ".join(f"{e['kind']}:{e['alert']['location_title']}" for e in events[:8]))

    _marker_cache = {}
    _ev_cache = {}
    _impact_cache = {}
    _impact_res = {}

    def markers(self):
        ttl = int(self.cfg.get("marker_ttl_minutes", 45))
        out = []
        if not geo:
            return out
        seen = []
        last_by_channel = {}   # channel -> (ts, marker) for live-tracking channels (bare place names)
        for p in sorted(self.store.feed_since(ttl), key=lambda p: p["ts"]):
            ms = self._marker_cache.get(p["post_id"])
            if ms is None:
                try:
                    ms = geo.parse_for_channel(p["channel"], p["text"])
                except Exception:
                    ms = []
                self._marker_cache[p["post_id"]] = ms
            pts = parse_iso(p["ts"])
            ctx = last_by_channel.get(p["channel"])
            fixed = []
            for m in ms:
                m = dict(m)
                if m.get("needs_context"):
                    if ctx and pts and (pts - ctx[0]).total_seconds() <= 8 * 60:
                        m.update(lon=ctx[1]["lon"], lat=ctx[1]["lat"], place=ctx[1]["place"], oblast_uid=ctx[1].get("oblast_uid"))
                        ev = dict(m.get("evidence") or {}); ev["position"] = dict(ev.get("position") or {}, matched=ctx[1].get("place"), place=ctx[1].get("place"))
                        m["evidence"] = ev
                    else:
                        continue
                elif m.get("dest_only"):
                    # "Два йдуть на Васильків": only where it is going. The target this channel reported last (within
                    # 8 min) is the one going there: it stays where that report put it, turned toward the destination.
                    # Without one it stays drawn at the destination, as an approach — never moved on past it.
                    if ctx and pts and (pts - ctx[0]).total_seconds() <= 8 * 60 and m.get("target") in geo.PLACES:
                        dlon, dlat, _ = geo.PLACES[m["target"]]
                        if math.hypot((dlon - ctx[1]["lon"]) * 70.7, (dlat - ctx[1]["lat"]) * 111) >= 0.4:
                            m.update(lon=ctx[1]["lon"], lat=ctx[1]["lat"], place=ctx[1]["place"], oblast_uid=ctx[1].get("oblast_uid"),
                                     approach=False, dest_only=False,
                                     heading=round(geo.bearing(ctx[1]["lon"], ctx[1]["lat"], dlon, dlat)))
                            ev = dict(m.get("evidence") or {})
                            ev["position"] = {"matched": ctx[1].get("place"), "place": ctx[1].get("place"), "confidence": "medium",
                                              "method": "this channel's previous report — the post only said where it is going"}
                            ev["heading"] = {"matched": f"{ctx[1].get('place')} → {m['target']}", "confidence": "medium",
                                             "method": "from the previous report toward the destination the post named"}
                            m["evidence"] = ev
                elif m.get("live"):
                    if ctx and pts and (pts - ctx[0]).total_seconds() <= 8 * 60 and m.get("heading") is None:
                        d = math.hypot((m["lon"] - ctx[1]["lon"]) * 70.7, (m["lat"] - ctx[1]["lat"]) * 111)
                        if d >= 0.4:
                            m["heading"] = round(geo.bearing(ctx[1]["lon"], ctx[1]["lat"], m["lon"], m["lat"]))
                            ev = dict(m.get("evidence") or {}); ev["heading"] = {"matched": f"{ctx[1].get('place')} → {m.get('place')}", "method": "bearing between the two latest reports of this live-tracking channel", "confidence": "medium"}
                            m["evidence"] = ev
                if m.get("lon") is not None and not m.get("status") and not m.get("approach"):
                    last_by_channel[p["channel"]] = (pts, m)
                fixed.append(m)
            ms = fixed
            # one post, one marker per place: identical (type, position, status) items collapse (counts add up);
            # official administrations never place a target at an oblast centre; a missile needs a named place or a heading
            official_kind = geo.OFFICIAL_PARSERS.get(p["channel"], (None,))[0]
            merged = []
            for m in ms:
                if m.get("lon") is None:
                    merged.append(m); continue
                if m.get("place") == "область":
                    if official_kind in ("koda", "kmda") or ("missile" in m["type"] and m.get("heading") is None and not m.get("status")):
                        continue
                same = next((x for x in merged if x.get("type") == m.get("type") and x.get("status") == m.get("status") and x.get("lon") is not None
                             and abs(x["lon"] - m["lon"]) < 0.005 and abs(x["lat"] - m["lat"]) < 0.005), None)
                if same:
                    if m.get("count") and same.get("count"):
                        same["count"] = same["count"] + m["count"]
                    continue
                merged.append(m)
            ms = merged
            for i, m in enumerate(ms):
                dup = False
                for (t2, ty2, st2, ch2, lo2, la2) in seen:
                    if ch2 != p["channel"] and ty2 == m["type"] and st2 == m.get("status") and abs(lo2 - m["lon"]) < 0.08 and abs(la2 - m["lat"]) < 0.06 and pts and abs((pts - t2).total_seconds()) < 300:
                        dup = True   # the same report relayed by another channel
                        break
                if dup:
                    continue
                seen.append((pts, m["type"], m.get("status"), p["channel"], m["lon"], m["lat"]))
                ev = m.get("evidence")
                if ev and _tr:
                    ck = (p["post_id"], i)
                    cached = self._ev_cache.get(ck)
                    if cached is None:
                        # evidence fragments use the fast offline glossary (no network per request)
                        ev = dict(ev, segment_en=_tr.translate_offline(ev.get("segment") or ""))
                        for k in ("type", "position", "heading", "count"):
                            v = ev.get(k)
                            if v:
                                v = dict(v, method=_tr.translate_offline(v.get("method") or ""))
                                if isinstance(v.get("matched"), str):
                                    v["matched_en"] = _tr.translate_offline(v["matched"])
                                ev[k] = v
                        self._ev_cache[ck] = ev
                    else:
                        ev = cached
                out.append(dict(m, id=f"{p['post_id']}#{i}", ts=p["ts"], text=p["text"], text_en=p.get("text_en"), channel=p["channel"], tags=p["tags"], evidence=ev))
        # Kyiv, its oblast, the five oblasts around it, and Sumy — the corridor most Shaheds into Kyiv arrive
        # through. Everything else is dropped here rather than hidden in the page, so a raid over Odesa never
        # reaches the Kyiv map, the counts, the proximity pushes or the light page. A mark with no oblast is kept
        # only if it sits close enough to still be about Kyiv.
        out = [m for m in out if in_scope(m)]
        out = self._chain_and_prune(out, ttl)
        out.sort(key=lambda m: m["ts"], reverse=True)
        # Keep what the app decided, not just what it was told. INSERT OR IGNORE keyed on the marker id, so
        # recomputing the same window many times a minute writes each marker exactly once.
        try:
            self.store.log_markers(out)
        except Exception as e:
            log(f"marker log failed: {e}")
        if len(self._marker_cache) > 2000:
            self._marker_cache.clear()
            self._ev_cache.clear()
        return out

    # -- one computation for everybody --------------------------------------------------------------------------
    # 25 Sep 2026, during an attack: every new post made EVERY open page ask for the marks, the feed and the
    # state at the same moment, and each request recomputed them from scratch — the marks from 45 minutes of
    # posts, the feed with its offline translation, each serialised on its own. With the pages that reconnect
    # after a restart on top, the one shared CPU never caught up and the server stopped answering. Now each
    # answer is built once per change (and at most every `ttl` seconds), by one thread while the others wait for
    # it, serialised and compressed once, and the same bytes go to every page.
    _resp = {}
    _resp_locks = {}
    _resp_guard = threading.Lock()

    def cached(self, name, ttl, build, ver=None):
        """(built_at, body, gzipped body or None, object) for `build()`; rebuilt when `ver` changes or `ttl`
        seconds have passed, never by two threads at once."""
        hit = self._resp.get(name)
        if hit and hit[4] == ver and time.time() - hit[0] < ttl:
            return hit
        with self._resp_guard:
            lk = self._resp_locks.setdefault(name, threading.Lock())
        with lk:
            hit = self._resp.get(name)
            if hit and hit[4] == ver and time.time() - hit[0] < ttl:
                return hit
            obj = build()
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            hit = (time.time(), body, gzip.compress(body, 5) if len(body) > 1400 else None, obj, ver)
            self._resp[name] = hit
            return hit

    def markers_now(self):
        """The marks as the pages see them — the cached computation, never a private recomputation."""
        return self.cached("markers", 10, lambda: {"now": now_iso(), "ttl_minutes": int(self.cfg.get("marker_ttl_minutes", 45)),
                                                     "stale_minutes": int(self.cfg.get("track_stale_minutes", 5)),
                                                     "markers": self.markers()}, ver=self.seq)[3]["markers"]

    # -- the dashboard: is every source alive, is every channel still posting, how early was the map ---------
    _health_res = None

    def health(self):
        if self._health_res and time.time() - self._health_res[0] < 60:
            return self._health_res[1]
        now = datetime.now(timezone.utc)
        with self.lock:
            sources = {k: dict(v) for k, v in self.sources.items()}
        since24 = (now - timedelta(hours=24)).isoformat()
        with self.store.lock:
            c = self.store.conn
            f24 = {r[0]: r[1] for r in c.execute("SELECT channel, COUNT(*) FROM feed WHERE ts>=? GROUP BY channel", (since24,))}
            m24 = {r[0]: r[1] for r in c.execute(
                "SELECT channel, COUNT(*) FROM marker_log WHERE ts>=? AND status IS NULL GROUP BY channel", (since24,))}
            last = {r[0]: r[1] for r in c.execute("SELECT channel, MAX(ts) FROM feed GROUP BY channel")}
        week = {r["channel"]: r for r in self.store.channel_report(7)}
        names = list(AUTHORITATIVE_CHANNELS) + [ch for ch in list(f24) + list(week) if ch not in AUTHORITATIVE_CHANNELS]
        channels = []
        for ch in dict.fromkeys(names):
            src = sources.get(f"tga:{ch}") or sources.get(f"tg:{ch}") or {}
            w = week.get(ch) or {}
            channels.append({"channel": ch, "posts_24h": f24.get(ch, 0), "marks_24h": m24.get(ch, 0), "last_post": last.get(ch),
                             "posts_7d": w.get("posts", 0), "read_7d": w.get("with_marker", 0), "outcomes_7d": w.get("outcomes", 0),
                             "flagged_7d": w.get("flagged", 0), "ok": src.get("ok"), "checked": src.get("last"),
                             "error": src.get("error"), "via": "api" if f"tga:{ch}" in sources else ("preview" if src else None)})
        official = [{"name": k, **v} for k, v in sorted(sources.items()) if not k.startswith(("tg:", "tga:"))]
        tr = {"available": [], "paused": {}}
        if _tr:
            try:
                tr["available"] = [n for n, _ in _tr.backends()]
                tr["paused"] = {n: int(u - time.time()) for n, u in _tr._DOWN.items() if u > time.time()}
            except Exception:
                pass
        out = {"generated": now_iso(), "official": official, "channels": channels, "translation": tr, "lead": self.lead_times(30)}
        self._health_res = (time.time(), out)
        return out

    def lead_times(self, days=30):
        """How long before each official air-raid alert over Kyiv or Kyiv oblast the map already showed a threat
        there (a live mark in the hour before it). Alerts that start within 30 min of another are one wave, counted
        once. A wave with nothing on the map in the hour before it counts as no warning — never as a zero lead."""
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=days)
        starts = sorted(st for st in (parse_iso(a["started_at"]) for a in self.store.history(days * 24, ["31", "14"])
                                      if (a.get("alert_type") or "air_raid") == "air_raid") if st and st >= since)
        marks = sorted(parse_iso(m["ts"]) for m in self.store.markers_between((since - timedelta(hours=1)).isoformat(), now.isoformat())
                       if m.get("oblast_uid") in ("31", "14"))
        marks = [m for m in marks if m]
        leads, waves, prev = [], 0, None
        for st in starts:
            if prev and (st - prev).total_seconds() < 1800:
                prev = st
                continue
            prev = st
            waves += 1
            i = bisect.bisect_left(marks, st - timedelta(hours=1))
            if i < len(marks) and marks[i] <= st:
                leads.append((st - marks[i]).total_seconds() / 60)
        leads.sort()

        def q(f):
            return round(leads[min(len(leads) - 1, int(f * len(leads)))]) if leads else None
        return {"days": days, "waves": waves, "warned": len(leads), "median_min": q(.5), "p25_min": q(.25), "p75_min": q(.75)}

    # -- statistics of past attacks (cached 5 min) ---------------------------
    _stats_res = {}

    def stats(self, days=14):
        days = max(1, min(int(days), 30))
        ck = self._stats_res.get(days)
        if ck and time.time() - ck[0] < 300:
            return ck[1]
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=days)
        kyiv_tz = timezone(timedelta(hours=3))
        # 1. alerts (Kyiv city + oblast-wide) → per day count / minutes, hour-of-day histogram, longest
        al = [a for a in self.store.history(days * 24, ["31", "14"]) if a["location_type"] in ("oblast", "city")]
        by_day = {}
        hours = [0] * 24
        longest = None
        tot_min = 0
        for a in al:
            st = parse_iso(a["started_at"]); en = parse_iso(a["finished_at"]) if a["finished_at"] else now
            if not st:
                continue
            st = max(st, since)
            mins = max(0, (en - st).total_seconds() / 60)
            d = st.astimezone(kyiv_tz).strftime("%Y-%m-%d")
            by_day.setdefault(d, {"alerts": 0, "minutes": 0, "impacts": 0, "down": 0, "drone_posts": 0, "missile_posts": 0})
            if a["oblast_uid"] == "31" or True:
                by_day[d]["alerts"] += 1
                by_day[d]["minutes"] += mins
            tot_min += mins if a["oblast_uid"] == "31" else 0
            if a["oblast_uid"] == "31" and (longest is None or mins > longest[0]):
                longest = (mins, a["started_at"], a["finished_at"])
            t = st
            while t < en and t < now:
                hours[t.astimezone(kyiv_tz).hour] += 1
                t += timedelta(hours=1)
        # 2. feed: impacts / shoot-downs (parsed, named towns only, region + Kyiv), drone / missile posts about Kyiv
        imp = self.impacts(min(days * 24, 96)) if geo else []
        by_obl = {}
        for m in imp:
            d = (parse_iso(m["ts"]) or now).astimezone(kyiv_tz).strftime("%Y-%m-%d")
            by_day.setdefault(d, {"alerts": 0, "minutes": 0, "impacts": 0, "down": 0, "fires": 0, "damage": 0, "drone_posts": 0, "missile_posts": 0})
            b = {"impact": "impacts", "down": "down", "fire": "fires", "damage": "damage"}.get(m["status"], "impacts")
            by_day[d].setdefault(b, 0)
            by_day[d][b] += 1
            ou = m.get("oblast_uid") or "?"
            by_obl.setdefault(ou, {"impacts": 0, "down": 0, "fires": 0, "damage": 0})
            by_obl[ou].setdefault(b, 0)
            by_obl[ou][b] += 1
        # 3. launches: Air Force morning summaries ("противник атакував N ударними БпЛА ... та M ракетами") give the
        #    official count of what was launched over Ukraine; per-day max (the summary is sometimes re-posted / corrected).
        #    Reports: every monitoring post tagged drones / missiles, all of Ukraine — a volume of reporting, not a count of targets.
        empty = {"alerts": 0, "minutes": 0, "impacts": 0, "down": 0, "fires": 0, "damage": 0, "drone_posts": 0, "missile_posts": 0,
                 "drone_reports": 0, "missile_reports": 0, "launched_drones": 0, "launched_missiles": 0, "af_down": 0}
        for k in by_day.values():
            for kk, v in empty.items():
                k.setdefault(kk, v)
        windows = {w: {"drone_reports": 0, "missile_reports": 0, "launched_drones": 0, "launched_missiles": 0,
                       "af_down": 0, "summaries": 0} for w in (1, 7, 30)}
        for p in self.store.feed_since(30 * 24 * 60, limit=60000, translate=False):
            tg = set(p.get("tags") or [])
            pts = parse_iso(p["ts"]) or now
            age_d = (now - pts).total_seconds() / 86400
            d = pts.astimezone(kyiv_tz).strftime("%Y-%m-%d")
            in_series = pts >= since
            if in_series:
                by_day.setdefault(d, dict(empty))
            summ = parse_af_summary(p["text"]) if p.get("channel") in AF_SUMMARY_CHANNELS else None
            if summ:
                for w, acc in windows.items():
                    if age_d <= w:
                        acc["summaries"] += 1
                if in_series:
                    row = by_day[d]
                    row["launched_drones"] = max(row["launched_drones"], summ["drones"])
                    row["launched_missiles"] = max(row["launched_missiles"], summ["missiles"])
                    row["af_down"] = max(row["af_down"], summ["down"])
            is_d = "drones" in tg
            is_m = bool(tg & {"ballistic_missiles", "cruise_missiles", "unspecified_missiles"})
            if not (is_d or is_m):
                continue
            for w, acc in windows.items():
                if age_d <= w:
                    if is_d: acc["drone_reports"] += 1
                    if is_m: acc["missile_reports"] += 1
            if not in_series:
                continue
            if is_d:
                by_day[d]["drone_reports"] += 1
            if is_m:
                by_day[d]["missile_reports"] += 1
            if "kyiv" in tg:
                if is_d:
                    by_day[d]["drone_posts"] += 1
                if is_m:
                    by_day[d]["missile_posts"] += 1
        # launched totals per window: a re-posted / corrected summary on the same day must not double-count → per-day maxima
        day_max = {}
        for p in self.store.feed_since(30 * 24 * 60, limit=60000, translate=False):
            if p.get("channel") not in AF_SUMMARY_CHANNELS:
                continue
            summ = parse_af_summary(p["text"])
            if not summ:
                continue
            pts = parse_iso(p["ts"]) or now
            d = pts.astimezone(kyiv_tz).strftime("%Y-%m-%d")
            cur = day_max.get(d, {"drones": 0, "missiles": 0, "down": 0, "age": 0, "post": None, "best": -1, "ch": ""})
            # the post shown for the day: the fullest summary, the Air Force's own channel over a re-post of it
            score = summ["drones"] + summ["missiles"]
            better = score > cur["best"] or (score == cur["best"] and p.get("channel") == "kpszsu" and cur["ch"] != "kpszsu")
            day_max[d] = {"drones": max(cur["drones"], summ["drones"]), "missiles": max(cur["missiles"], summ["missiles"]),
                          "down": max(cur["down"], summ["down"]), "age": (now - pts).total_seconds() / 86400,
                          "post": p.get("post_id") if better else cur["post"], "best": max(score, cur["best"]),
                          "ch": p.get("channel") if better else cur["ch"]}
        for d, v in day_max.items():
            for w, acc in windows.items():
                if v["age"] <= w:
                    acc["launched_drones"] += v["drones"]; acc["launched_missiles"] += v["missiles"]; acc["af_down"] += v["down"]
        # Explosions and confirmed shoot-downs come from parsed posts, and that parsing covers at most 96 h —
        # so they are reported for 24 h and 72 h only. Reporting them "per 30 days" would be a number that is
        # simply missing most of its days.
        # fires are counted apart and never folded into the explosion total: a warehouse burning after a strike
        # is not a second explosion, and a fire nobody tied to a strike is not an explosion at all
        imp_windows = {1: {"impacts": 0, "down": 0, "fires": 0, "damage": 0}, 3: {"impacts": 0, "down": 0, "fires": 0, "damage": 0}}
        _bucket = {"impact": "impacts", "down": "down", "fire": "fires", "damage": "damage"}
        for m in imp:
            age_d = (now - (parse_iso(m["ts"]) or now)).total_seconds() / 86400
            for w, acc in imp_windows.items():
                if age_d <= w:
                    acc[_bucket.get(m["status"], "impacts")] += 1
        daysl = [(since + timedelta(days=i)).astimezone(kyiv_tz).strftime("%Y-%m-%d") for i in range(days + 1)]
        series = [{"day": d, **by_day.get(d, empty)} for d in daysl]
        out = {"days": days, "since": since.isoformat(), "series": series, "hours": hours, "kyiv_minutes": round(tot_min),
               "kyiv_alerts": sum(1 for a in al if a["oblast_uid"] == "31"), "longest": longest, "by_oblast": by_obl,
               "impacts_total": sum(1 for m in imp if m["status"] == "impact"), "down_total": sum(1 for m in imp if m["status"] == "down"),
               "fires_total": sum(1 for m in imp if m["status"] == "fire"),
               "damage_total": sum(1 for m in imp if m["status"] == "damage"),
               "impact_window_h": min(days * 24, 96), "windows": {str(k): v for k, v in windows.items()},
               "imp_windows": {str(k): v for k, v in imp_windows.items()}, "imp_max_h": min(days * 24, 96),
               # the Stats tab shows only these: the Air Force's own summaries, one row a day, newest first
               "af_days": [{"day": d, "drones": v["drones"], "missiles": v["missiles"], "down": v["down"], "post": v["post"]}
                           for d, v in sorted(day_max.items(), reverse=True)]}
        self._stats_res[days] = (time.time(), out)
        return out


    # -- missile mode -----------------------------------------------------
    # A level, not a flag, because the two missile families leave you different amounts of time:
    #   0  nothing flying   → clients ping every 15 s, Telegram every 30 s
    #   1  cruise / MiG-31K → clients every 5 s, Telegram every 10 s
    #   2  ballistic open   → clients every second, Telegram and the alert APIs every 5 s
    # A ballistic missile covers ~35 km a minute: from the moment a post exists, every second the app
    # spends not knowing about it is roughly half a kilometre of someone's warning. Level 2 is rare and
    # short (the whole event is minutes), which is what makes a one-second cadence affordable at all.
    MSL_NONE, MSL_CRUISE, MSL_BALLISTIC = 0, 1, 2
    _missile_res = (0, 0)

    def missile_active(self):
        """0 / 1 / 2 — truthy exactly when something missile-shaped is up, so old call sites still work."""
        t0, v = self._missile_res
        if time.time() - t0 < 4:
            return v
        v = self._missile_active()
        self._missile_res = (time.time(), v)
        return v

    def _missile_active(self):
        fav = set(self.cfg.get("favourites") or [])
        bal = {"ballistic_missiles"}
        kinds = {"ballistic_missiles", "cruise_missiles", "banderol_missiles", "mig31k_departure"}
        level = self.MSL_NONE
        with self.lock:
            for a in self.active.values():
                if fav and a.get("oblast_uid") not in fav:
                    continue
                for t in a.get("threats") or []:
                    ty = t.get("threat_type") if isinstance(t, dict) else t
                    if ty in bal:
                        return self.MSL_BALLISTIC          # nothing outranks this, stop looking
                    if ty in kinds:
                        level = self.MSL_CRUISE
        for p in self.store.feed_since(10, limit=60, translate=False):
            tags = set(p.get("tags") or [])
            if bal & tags:
                return self.MSL_BALLISTIC
            if kinds & tags:
                level = self.MSL_CRUISE
        return level

    # -- impact / shoot-down history (24/48/72 h) ---------------------------
    # "impact / explosion" and confirmed "shot down" outcome markers, parsed from every post of the window (cached per post,
    # result cached 60 s per window). Same-place relays from other channels within 5 min are merged.
    def impacts(self, hours):
        hours = max(1, min(int(hours), 168))      # the report offers 7 days; the map never asks for more than 24 h
        if not geo:
            return []
        now = time.time()
        ck = self._impact_res.get(hours)
        if ck and now - ck[0] < 60:
            return ck[1]
        out, seen = [], []
        for p in sorted(self.store.feed_since(hours * 60, limit=6000, translate=False), key=lambda p: p["ts"]):
            ms = self._impact_cache.get(p["post_id"])
            if ms is None:
                try:
                    # only impacts pinned to a named town (no oblast-centre approximations in a history layer);
                    # long news / summary posts are not live impact reports
                    ms = [] if len(p["text"]) > 400 else [m for m in geo.parse_for_channel(p["channel"], p["text"])
                          if m.get("status") in ("impact", "down", "fire", "damage") and m.get("lon") is not None and m.get("place") != "область"
                          and ((m.get("evidence") or {}).get("position") or {}).get("confidence") == "high"]
                except Exception:
                    ms = []
                self._impact_cache[p["post_id"]] = ms
            if not ms:
                continue
            pts = parse_iso(p["ts"])
            for i, m in enumerate(ms):
                if any(st == m["status"] and abs(lo - m["lon"]) < 0.08 and abs(la - m["lat"]) < 0.06 and pts and abs((pts - t).total_seconds()) < 300 for (t, lo, la, st) in seen):
                    continue
                seen.append((pts, m["lon"], m["lat"], m["status"]))
                out.append({"id": f"{p['post_id']}#i{i}", "type": m["type"], "status": m["status"], "lon": m["lon"], "lat": m["lat"], "place": m.get("place"),
                            "count": m.get("count"), "ts": p["ts"], "text": p["text"], "text_en": p.get("text_en"), "channel": p["channel"], "oblast_uid": m.get("oblast_uid")})
        out.sort(key=lambda m: m["ts"], reverse=True)
        if len(self._impact_cache) > 20000:
            self._impact_cache.clear()
        self._impact_res[hours] = (now, out)
        return out

    # -- track lifecycle ------------------------------------------------
    # A target must be *re-reported* to stay on the map. Each new report is matched to the most plausible
    # earlier report of the same family (same channel preferred): dt ≤ 30 min, distance ≤ max_speed·dt + 25 km,
    # roughly ahead of the earlier heading. The earlier report is then superseded (kept only as history).
    # A report that is neither updated within `stale_minutes` nor closed by an outcome is dropped, and every
    # report in an oblast whose air-raid alert has ended is dropped once the alert has been over for 3 min.
    def _chain_and_prune(self, ms, ttl):
        stale = int(self.cfg.get("track_stale_minutes", 5))
        now = datetime.now(timezone.utc)
        obl_status = {}
        with self.lock:
            # any alert in the oblast keeps its reports alive: with alerts by raion, an oblast whose three raions
            # are under alert has no oblast-wide alert at all, and its drones must not vanish after 3 minutes
            for a in self.active.values():
                obl_status[a["oblast_uid"]] = "A"
        ms = sorted(ms, key=lambda m: m["ts"])
        tracks = [m for m in ms if not m.get("status")]
        for m in tracks:
            m["superseded_by"] = None
            m["history"] = []
        def km(a, b):
            return math.hypot((a["lon"] - b["lon"]) * 70.7, (a["lat"] - b["lat"]) * 111)
        def fam(m):
            # Banderol is its own family. It used to share "missile" with cruise missiles because its type name
            # contains the word, so a Banderol track could be continued by a later "ракета" report from another
            # channel — and the later, vaguer report is what named the track. A Banderol never becomes a cruise
            # missile by being chained to one.
            if m["type"] == "banderol_missiles":
                return "banderol"
            return "drone" if m["type"] == "drones" else ("missile" if "missile" in m["type"] else m["type"])
        for i, m in enumerate(tracks):
            t1 = parse_iso(m["ts"])
            best = None
            for pm in tracks[:i]:
                if pm["superseded_by"] or fam(pm) != fam(m):
                    continue
                dt = (t1 - parse_iso(pm["ts"])).total_seconds() / 60
                if dt <= 0 or dt > 30:
                    continue
                vmax = 7.0 if fam(m) == "drone" else 15.0
                d = km(pm, m)
                # an approach mark stands at the town the target is heading to, not where it is: it may be further
                if d > (80 if m.get("approach") else vmax * dt + 25):
                    continue
                if pm.get("heading") is not None and d > 12:
                    b = (math.degrees(math.atan2((m["lon"] - pm["lon"]) * 70.7, (m["lat"] - pm["lat"]) * 111)) + 360) % 360
                    diff = abs(((pm["heading"] - b) + 540) % 360 - 180)
                    if diff > 60:
                        continue
                score = d + dt * 0.8 + (0 if pm["channel"] == m["channel"] else 10) + (0 if (pm.get("count") or 1) == (m.get("count") or 1) else 5)
                if best is None or score < best[0]:
                    best = (score, pm)
            if best:
                pm = best[1]
                if m.get("approach") and not pm.get("approach"):
                    # "…на Васильків" about a target already on the map: where it is going, not where it now is.
                    # The track keeps its reported position and takes the destination; the approach mark goes.
                    m["absorbed"] = True
                    pm["target"] = pm.get("target") or m.get("target")
                    if pm.get("heading") is None and m.get("target") in geo.PLACES:
                        dlon, dlat, _ = geo.PLACES[m["target"]]
                        pm["heading"] = round(geo.bearing(pm["lon"], pm["lat"], dlon, dlat))
                        ev = dict(pm.get("evidence") or {})
                        ev["heading"] = {"matched": m.get("place"), "confidence": "medium",
                                         "method": f"toward {m['target']}, the destination a later post named"}
                        pm["evidence"] = ev
                    continue
                pm["superseded_by"] = m["id"]
                # each earlier report keeps the height ITS post stated (if any): two stated heights in a row are a
                # descent the posts reported, not one this app worked out — 2200 m, then 1600 m, then 800 m
                # an approach mark's point is a destination, not a place it was: it is no step of the track
                m["history"] = pm["history"] + ([] if pm.get("approach") else [{"id": pm["id"], "lon": pm["lon"], "lat": pm["lat"], "ts": pm["ts"], "channel": pm["channel"],
                                                 "place": pm.get("place"), "alt_m": (pm.get("alt") or {}).get("m"),
                                                 "alt_state": (pm.get("alt") or {}).get("state")}])
        keep = []
        for m in ms:
            if m.get("superseded_by") or m.get("absorbed"):
                continue
            age = (now - parse_iso(m["ts"])).total_seconds() / 60 if parse_iso(m["ts"]) else 0
            if m.get("status"):
                if age <= 25:
                    keep.append(m)
                continue
            # A missile is not a drone and must not be aged like one. A Shahed does ~3 km a minute, so a
            # five-minute-old dot is still worth something. A cruise missile does ~13, a ballistic one ~35: a
            # position two minutes old is already tens of kilometres wrong, and a grey dot left on the map would
            # be a lie with a precise pin in it. So missiles keep no "stale" flag at all — the client turns them
            # into a growing circle of where they could now be — and they are dropped sooner.
            if "missile" in (m.get("type") or ""):
                if age > MISSILE_TTL_MIN:
                    continue
                m["fast"] = True
                keep.append(m)
                continue
            if age > stale:
                # An unspecified threat ("Васильків увага") says where, not what. It is a loud red sign while it
                # is fresh, and then it is simply gone — no grey ghost fading for ten more minutes, because there
                # is nothing to keep half-alive: nobody ever said what it was.
                if m.get("type") == "unknown":
                    continue
                if age > max(stale * 3, 15):
                    continue                      # nothing for 15 min → gone
                m["stale"] = round(age)           # not re-reported for > stale min: kept as "? location not updated"
            ou = m.get("oblast_uid")
            if ou and obl_status and ou not in obl_status and age > 3:
                # its oblast has no active alert any more → the threat has left or was resolved
                continue
            keep.append(m)
        return keep

    def snapshot(self):
        with self.lock:
            merged = {}
            for a in self.active.values():
                k = (a["location_uid"], a["alert_type"])
                if k in merged:
                    b = merged[k]
                    m = dict(b if b["source"] in ("alerts_in_ua", "ukrainealarm", "ukrainealarm_proxy") else a)
                    m["started_at"] = min(a["started_at"] or "9", b["started_at"] or "9")
                    thr = list(b.get("threats") or [])
                    for t in a.get("threats") or []:
                        if t not in thr:
                            thr.append(t)
                    m["threats"] = thr
                    m["alert_level"] = "red" if "red" in (a.get("alert_level"), b.get("alert_level")) else (a.get("alert_level") or b.get("alert_level"))
                    merged[k] = m
                else:
                    merged[k] = dict(a)
            active = sorted(merged.values(), key=lambda a: a["started_at"], reverse=True)
            sources = dict(self.sources)
        by_oblast = {}
        for uid, uk, en in OBLASTS:
            by_oblast[uid] = {"uid": uid, "uk": uk, "en": en, "status": "N", "types": [], "level": None, "since": None, "threats": []}
        for a in active:
            o = by_oblast.get(a["oblast_uid"])
            if not o:
                continue
            if a["location_type"] == "oblast":
                o["status"] = "A"
                if a.get("since_known", True):   # a mirror alert with no start time has no "since"
                    o["since"] = a["started_at"] if not o["since"] or a["started_at"] < o["since"] else o["since"]
            elif o["status"] != "A":
                o["status"] = "P"
            if a["alert_type"] not in o["types"]:
                o["types"].append(a["alert_type"])
            if a.get("alert_level") == "red" or (a.get("alert_level") == "yellow" and o["level"] != "red"):
                o["level"] = a.get("alert_level")
            for t in a.get("threats") or []:
                if t not in o["threats"]:
                    o["threats"].append(t)
        return {"now": now_iso(), "active": active, "oblasts": by_oblast, "sources": sources, "eradar": self.eradar,
                "favourites": self.cfg.get("favourites") or [], "config": {"demo": bool(self.cfg.get("demo")),
                "has_token": bool(self.cfg.get("alerts_in_ua_token")), "channels": self.cfg.get("telegram_channels")}}


# ---------------------------------------------------------------------------
# Desktop notifications (optional plyer)
# ---------------------------------------------------------------------------
class Notifier:
    def __init__(self, cfg):
        self.enabled = bool(cfg.get("desktop_notifications", True))
        self.impl = None
        if self.enabled:
            try:
                from plyer import notification  # type: ignore
                self.impl = notification
            except Exception:
                self.impl = None

    def maybe(self, ev, favourites):
        if not self.impl:
            return
        a = ev["alert"]
        if a["oblast_uid"] not in favourites:
            return
        if ev["kind"] == "start":
            title, msg = f"🔴 Alert — {a.get('location_title_en') or a['location_title']}", a["alert_type"]
        elif ev["kind"] == "end":
            title, msg = f"🟢 All clear — {a.get('location_title_en') or a['location_title']}", "alert ended"
        elif ev["kind"] == "threat":
            t = ev.get("threat") or {}
            title, msg = f"⚠️ {t.get('threat_type')} — {a.get('location_title_en') or a['location_title']}", t.get("source_message") or ""
        else:
            return
        try:
            self.impl.notify(title=title, message=msg[:200], app_name="UA Alerts", timeout=8)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Source pollers
# ---------------------------------------------------------------------------
def poll_gap(state, interval, rush=5):
    """Seconds to wait before the next round.

    While a ballistic threat is open everything upstream is read at `rush` instead. Serving a client every
    second is theatre if the server itself last looked fifteen seconds ago — the app can only ever be as
    fresh as the slowest link, and that link is the source poll, not the browser. Ballistic windows are
    measured in minutes, so the extra requests cost little and stop the moment the alert clears.
    """
    try:
        return rush if state.missile_active() >= 2 else interval
    except Exception:
        return interval


class AlertsInUa(threading.Thread):
    NAME = "alerts_in_ua"

    def __init__(self, state, cfg):
        super().__init__(daemon=True)
        self.state, self.cfg = state, cfg
        self.token = cfg["alerts_in_ua_token"]
        self.last_modified = None
        self.backfilled = False

    @staticmethod
    def normalise(a):
        return {
            "key": f"aiu:{a['id']}",
            "source": "alerts_in_ua",
            "location_uid": str(a.get("location_uid")),
            "location_title": a.get("location_title"),
            "location_title_en": a.get("location_title_en"),
            "location_type": a.get("location_type") or "unknown",
            "oblast_uid": str(a.get("location_oblast_uid") or norm_uk(a.get("location_oblast")) or a.get("location_uid")),
            "alert_type": a.get("alert_type") or "air_raid",
            "alert_level": a.get("alert_level"),
            "started_at": a.get("started_at"),
            "finished_at": a.get("finished_at"),
            "notes": a.get("notes"),
            "threats": a.get("threats") or [],
        }

    def run(self):
        interval = max(10, int(self.cfg.get("poll_alerts_seconds", 15)))
        while True:
            try:
                self.poll()
                if not self.backfilled:
                    self.backfilled = True
                    threading.Thread(target=self.backfill, daemon=True).start()
            except urllib.error.HTTPError as e:
                if e.code == 304:
                    self.state.set_source(self.NAME, True, note="304 not modified")
                elif e.code == 429:
                    self.state.set_source(self.NAME, False, error="429 rate limited — slowing down")
                    time.sleep(60)
                else:
                    self.state.set_source(self.NAME, False, error=f"HTTP {e.code}")
                    log("alerts.in.ua HTTP", e.code)
            except Exception as e:
                self.state.set_source(self.NAME, False, error=str(e)[:200])
                log("alerts.in.ua error:", e)
            time.sleep(poll_gap(self.state, interval))

    def poll(self):
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if self.last_modified:
            headers["If-Modified-Since"] = self.last_modified
        status, hdrs, body = http_get("https://api.alerts.in.ua/v1/alerts/active.json", headers)
        self.last_modified = hdrs.get("Last-Modified")
        data = json.loads(body.decode("utf-8"))
        alerts = [self.normalise(a) for a in data.get("alerts", []) if a.get("finished_at") is None]
        self.state.set_source(self.NAME, True, count=len(alerts), disclaimer=data.get("disclaimer"))
        self.state.apply_snapshot(self.NAME, alerts, {"oblast", "raion", "city", "hromada", "unknown"})

    def backfill(self):
        """Month history for favourite regions (rate limit 2/min on this endpoint)."""
        for uid in self.cfg.get("favourites") or []:
            try:
                status, hdrs, body = http_get(f"https://api.alerts.in.ua/v1/regions/{uid}/alerts/month_ago.json",
                                              {"Authorization": f"Bearer {self.token}"})
                data = json.loads(body.decode("utf-8"))
                n = 0
                for a in data.get("alerts", []):
                    self.state.store.upsert_alert(self.normalise(a))
                    n += 1
                log(f"backfilled {n} alerts for {NAME_BY_UID.get(uid, (uid,))[0]}")
            except Exception as e:
                log("backfill error", uid, e)
            time.sleep(35)


# The raion table: official region id → oblast, parent raion, and the raion's shape on the map.
# Built by scripts/build_geo.py from the ukrainealarm region list; read once.
REGIONS_PATH = os.path.join(ROOT, "data", "ua_regions.json")
_UAR = None


def ua_regions():
    global _UAR
    if _UAR is None:
        try:
            with open(REGIONS_PATH, encoding="utf-8") as f:
                _UAR = json.load(f)
        except Exception as e:
            log("ua_regions.json:", e)
            _UAR = {"states": {}, "districts": {}, "communities": {}}
    return _UAR


def _iso_s(t):
    """'2026-09-24T14:11:44.399859Z' (any number of decimals) → '2026-09-24T14:11:44Z', the form used everywhere else."""
    if not t:
        return None
    d = parse_iso(re.sub(r"\.\d+", "", t))
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


class UkraineAlarm(threading.Thread):
    """The official «Повітряна тривога» data, down to the raion and the hromada.

    api.ukrainealarm.com is the backend of the government app. With a key (config ukrainealarm_key, or env
    UKRAINEALARM_KEY) this reads it directly. Without one it reads siren.pp.ua, a keyless read-only proxy that
    serves the same API responses — the source the Home Assistant integration falls back to.

    This is the source that knows WHICH raion. The ubilling mirror only says "something in Kyiv oblast is under
    alert": it turned the whole oblast red, and a person in Boryspil raion saw red ten minutes after the
    government app had lifted their alert. A raion here comes as a bare numeric id; data/ua_regions.json turns
    it into its oblast and its shape on the map.

    Each alert also carries the government app's own danger level (since 11 Sep 2026): red — massed drone,
    missile or drone-and-missile threat; yellow — drone threat. Both are air-raid alerts. The official reason
    text ("Дронова загроза (жовтий рівень)") is kept as the alert's note, word for word.
    """
    TYPE_MAP = {"AIR": "air_raid", "ARTILLERY": "artillery_shelling", "URBAN_FIGHTS": "urban_fights",
                "CHEMICAL": "chemical", "NUCLEAR": "nuclear", "INFO": "info"}
    LEVEL_MAP = {"State": "oblast", "District": "raion", "Community": "hromada"}

    def __init__(self, state, cfg):
        super().__init__(daemon=True)
        self.state, self.cfg = state, cfg
        self.key = cfg.get("ukrainealarm_key") or ""
        if self.key:
            self.NAME, self.base = "ukrainealarm", "https://api.ukrainealarm.com/api/v3"
        else:
            self.NAME, self.base = "ukrainealarm_proxy", (cfg.get("siren_proxy_url") or "https://siren.pp.ua/api/v3").rstrip("/")
        self.primary = not cfg.get("alerts_in_ua_token")
        self.last_action = None
        self.last_full = 0.0
        self.last_ok = 0.0
        self.born = time.time()
        self.unknown = set()

    def healthy(self, within=90):
        """Answered in the last `within` seconds — or still starting up (the mirror must not jump in during
        the first seconds of a boot and paint whole oblasts that this source then has to take back)."""
        now = time.time()
        return now - self.last_ok < within or (not self.last_ok and now - self.born < within)

    def run(self):
        interval = max(8, int(self.cfg.get("poll_ukrainealarm_seconds", 10)))
        while True:
            try:
                self.poll()
                self.last_ok = time.time()
            except Exception as e:
                self.state.set_source(self.NAME, False, error=str(e)[:200])
                log(f"{self.NAME} error:", e)
            time.sleep(poll_gap(self.state, interval))

    def poll(self):
        h = {"Accept": "application/json"}
        if self.key:
            h["Authorization"] = self.key
        _, _, body = http_get(self.base + "/alerts/status", h)
        action = json.loads(body.decode()).get("lastActionIndex")
        # Unchanged index: skip the full list — but never for long. A change of level on an alert that is already
        # on (yellow → red) must not wait on the index moving, so the full list is read at least every 30 s.
        if action is not None and action == self.last_action and time.time() - self.last_full < 30:
            self.state.set_source(self.NAME, True, note="unchanged")
            return
        _, _, body = http_get(self.base + "/alerts", h)
        alerts = self.normalise(json.loads(body.decode()))
        self.last_action, self.last_full = action, time.time()
        self.state.set_source(self.NAME, True, count=len(alerts))
        if self.primary:
            self.state.apply_snapshot(self.NAME, alerts, {"oblast", "raion", "hromada", "city", "unknown"})

    def place(self, rid, rtype, r):
        """Where an official region id is. None only for the API's own test region."""
        uar = ua_regions()
        if rid in (uar.get("ignore") or []):
            return None
        if rtype == "State":
            uid = uar["states"].get(rid)
            if uid:
                return {"type": "oblast", "obl": uid, "name": NAME_BY_UID.get(uid, (r.get("regionName"),))[0]}
        if rtype == "District":
            d = uar["districts"].get(rid)
            if d:
                return {"type": "raion", "obl": d["obl"], "name": d["name"], "raion_uid": rid, "raion_key": d["key"], "raion_title": d["name"]}
        c = uar["communities"].get(rid)
        if c:   # a hromada — including a city the API lists at the top level ("м. Харків та … громада")
            d = uar["districts"].get(c.get("d") or "") or {}
            return {"type": "hromada", "obl": c.get("obl") or d.get("obl") or "?", "name": c["name"],
                    "raion_uid": c.get("d"), "raion_key": d.get("key"), "raion_title": d.get("name")}
        # An id the table does not know yet (a new hromada, a renamed raion). The alert is kept — an alert is
        # never dropped for being hard to place — as precisely as its name allows, and logged once.
        if rid not in self.unknown:
            self.unknown.add(rid)
            log(f"{self.NAME}: region id {rid} ({rtype}) not in ua_regions.json — rebuild it with scripts/build_geo.py")
        name = r.get("regionName") if str(r.get("regionId")) == rid else f"#{rid}"
        return {"type": self.LEVEL_MAP.get(rtype, "unknown"), "obl": norm_uk(name) or "?", "name": name}

    def normalise(self, regions):
        out = {}
        for r in regions or []:
            for a in r.get("activeAlerts") or []:
                # an entry can carry its parent's alert too (a hromada listing its raion's): each alert names its own region
                rid = str(a.get("regionId") or r.get("regionId") or "")
                rtype = a.get("regionType") or r.get("regionType")
                p = self.place(rid, rtype, r)
                if not p:
                    continue
                atype = self.TYPE_MAP.get(a.get("type"), str(a.get("type") or "air_raid").lower())
                levels = a.get("activeAlertLevels") or []
                names = [x.get("alertLevel") for x in levels]
                level = "red" if "Red" in names else ("yellow" if "Yellow" in names else None)
                times = [s for s in [_iso_s(a.get("lastUpdate"))] + [_iso_s(x.get("createdAt")) for x in levels] if s]
                reason = next((x.get("reason") for x in sorted(levels, key=lambda x: x.get("alertLevel") != "Red") if x.get("reason")), None)
                key = f"ua:{rid}:{atype}"
                out[key] = {"key": key, "source": self.NAME, "location_uid": f"ua-{rid}", "location_title": p["name"],
                            "location_title_en": r.get("regionEngName") if str(r.get("regionId")) == rid else None,
                            "location_type": p["type"], "oblast_uid": p["obl"], "alert_type": atype, "alert_level": level,
                            "started_at": min(times) if times else now_iso(), "finished_at": None, "notes": reason, "threats": [],
                            "raion_uid": p.get("raion_uid"), "raion_key": p.get("raion_key"), "raion_title": p.get("raion_title")}
        return list(out.values())


class Ubilling(threading.Thread):
    """Keyless mirror: one on/off per OBLAST, nothing finer, and usually no start time.

    It lights an oblast as soon as any raion in it is under alert, so it can only be a fallback: it stands in
    when no raion-level source answers (`detail` unhealthy), and otherwise just reports its own health.
    """
    NAME = "ubilling_mirror"

    def __init__(self, state, cfg, primary, detail=None):
        super().__init__(daemon=True)
        self.state, self.cfg, self.primary, self.detail = state, cfg, primary, detail

    def run(self):
        interval = max(20, int(self.cfg.get("poll_ubilling_seconds", 30)))
        while True:
            try:
                st, _, body = http_get("https://ubilling.net.ua/aerialalerts/?json=true")
                data = json.loads(body.decode("utf-8"))
                alerts = []
                for name, v in (data.get("states") or {}).items():
                    uid = norm_uk(name)
                    if not uid:
                        continue
                    if v.get("alertnow"):
                        changed = v.get("changed")
                        started = changed.replace(" ", "T") if changed else now_iso()
                        if "+" not in started and not started.endswith("Z"):
                            started += "+03:00"  # mirror reports Kyiv local time
                        d = parse_iso(started)
                        known = bool(d and d.year >= 2022)
                        if not known:
                            # the mirror reports epoch 0: the start time is unknown. The time it is first seen is kept
                            # for ordering only — shown as "since", it was the moment the server rebooted.
                            started = now_iso()
                        uk, en = NAME_BY_UID[uid]
                        alerts.append({"key": f"ub:{uid}", "source": self.NAME, "location_uid": uid,
                                       "location_title": uk, "location_title_en": en, "location_type": "oblast",
                                       "oblast_uid": uid, "alert_type": "air_raid", "alert_level": None,
                                       "started_at": started, "finished_at": None, "notes": "keyless mirror — oblast level only",
                                       "threats": [], "since_known": known})
                standing_in = self.primary and not (self.detail and self.detail.healthy())
                self.state.set_source(self.NAME, True, count=len(alerts), cached_at=data.get("cachedat"),
                                      note="standing in: oblast level only" if standing_in and self.detail else None)
                if standing_in:
                    self.state.apply_snapshot(self.NAME, alerts, {"oblast"})
            except Exception as e:
                self.state.set_source(self.NAME, False, error=str(e)[:200])
                log("ubilling error:", e)
            time.sleep(interval)


class Translations(threading.Thread):
    """Machine translation of feed posts, off the alert path.

    A post is stored and shown the moment it is read, with the offline English glossary. A machine translation
    (DeepL, Google — see translate.py) replaces it afterwards, from here, so a slow or refusing translator can
    never hold a drone report back. Only for a language somebody is reading: a page in English or French asks
    for the feed with ?lang=, and for an hour after that, new posts are translated into that language too.
    Nobody reading French, no French translated — a key's monthly allowance goes to what is read."""
    DEMAND_S = 3600

    def __init__(self, state):
        super().__init__(daemon=True, name="translations")
        self.state = state
        self.q = collections.deque()
        self.queued = set()
        self.cond = threading.Condition()
        self.seen = {}

    def want(self, lang):
        if lang in ("en", "fr"):
            self.seen[lang] = time.time()

    def wanted(self, lang):
        return time.time() - self.seen.get(lang, 0) < self.DEMAND_S

    def add(self, posts, lang, front=False):
        if not (_tr and _tr.backends()):
            return            # nothing here can do better than what is stored
        with self.cond:
            items = [p for p in posts if (p["post_id"], lang) not in self.queued]
            for p in (reversed(items) if front else items):
                self.queued.add((p["post_id"], lang))
                (self.q.appendleft if front else self.q.append)((p["post_id"], p["text"], lang))
            self.cond.notify()

    def run(self):
        while True:
            with self.cond:
                while not self.q:
                    self.cond.wait()
                pid, text, lang = self.q.popleft()
            try:
                out, ok = _tr.translate_to(text, lang)
                if ok and out:
                    self.state.store.set_translation(pid, lang, out)
                    self.state.publish({"kind": "tr", "ts": now_iso(), "post_id": pid, "lang": lang})
                elif not _tr.backends():
                    with self.cond:           # every translator is resting: drop the queue, the next page view refills it
                        self.q.clear()
                        self.queued.clear()
            except Exception as e:
                log("translate:", e)
            finally:
                with self.cond:
                    self.queued.discard((pid, lang))


class Telegram(threading.Thread):
    """Reads public channel previews at t.me/s/<channel> (no API key)."""
    NAME = "telegram"
    MSG_RE = re.compile(r'<div class="tgme_widget_message_wrap.*?data-post="([^"]+)".*?'
                        r'(?:<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>)?.*?'
                        r'<time[^>]*datetime="([^"]+)"', re.S)
    TAG_RE = re.compile(r"<br\s*/?>", re.I)
    STRIP_RE = re.compile(r"<[^>]+>")

    def __init__(self, state, cfg):
        super().__init__(daemon=True)
        self.state, self.cfg = state, cfg
        self.channels = list(AUTHORITATIVE_CHANNELS)
        extra = [c for c in (cfg.get("telegram_channels") or []) if c not in AUTHORITATIVE_CHANNELS]
        if extra:
            log(f"telegram: {len(extra)} channel(s) in config ignored — only {', '.join(AUTHORITATIVE_CHANNELS)} are read")
        self.official = OfficialAlerts(state)
        self.seen_channels = set()
        self.api_channels = set()   # channels TelegramAPI is reading right now — this poller leaves them alone

    def run(self):
        interval = max(30, int(self.cfg.get("poll_telegram_seconds", 45)))
        fast = max(8, int(self.cfg.get("poll_telegram_missile_seconds", 10)))
        # Ballistic only. A one-second client is pointless if the server itself last looked ten seconds ago:
        # the client can only be as fresh as the source behind it.
        rush = max(5, int(self.cfg.get("poll_telegram_ballistic_seconds", 5)))
        import concurrent.futures as _cf
        pool = _cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="tg")
        def one(ch):
            try:
                self.poll(ch)
            except Exception as e:
                self.state.set_source(f"tg:{ch}", False, error=str(e)[:200])
                log(f"telegram {ch} error:", e)
        while True:
            lvl = self.state.missile_active()
            list(pool.map(one, self.channels))   # 4 channels at a time, ~1 round trip each
            time.sleep(rush if lvl >= 2 else fast if lvl else interval)

    def poll(self, ch):
        if ch in self.api_channels:
            return            # read through the Telegram API (TelegramAPI) — its preview is switched off
        st, _, body = http_get(f"https://t.me/s/{ch}", {"Accept-Language": "uk,en"})
        page = body.decode("utf-8", "replace")
        if 'tgme_widget_message_wrap' not in page:
            # The owner has switched the web preview off: t.me/s/ shows the landing page and no posts at all.
            # Reported as a failed source, not a quiet one — "no drones reported" and "we cannot read this
            # channel" must never look the same in the sources panel.
            self.state.set_source(f"tg:{ch}", False, error="web preview disabled by the channel — cannot be read via t.me/s/")
            return
        raw_posts = []
        for part in page.split('<div class="tgme_widget_message_wrap')[1:]:
            mid = re.search(r'data-post="([^"]+)"', part)
            mtxt = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', part, re.S)
            mdt = re.search(r'<time[^>]*datetime="([^"]+)"', part)
            if not (mid and mtxt and mdt):
                continue
            text = html.unescape(self.STRIP_RE.sub("", self.TAG_RE.sub("\n", mtxt.group(1)))).strip()
            raw_posts.append((mid.group(1), mdt.group(1), text))
        self.ingest(ch, raw_posts, f"tg:{ch}")

    def ingest(self, ch, raw_posts, source):
        """Everything a channel post goes through, whichever way it was read (web preview or Telegram API):
        raw_posts is a list of (post_id "channel/123", ISO time, text)."""
        posts = []
        for post_id, dt, text in raw_posts:
            text = re.sub(r"[ \t]+", " ", (text or "").strip())
            if not text:
                continue
            if self.state.store.has_post(post_id):
                continue          # already stored (and translated) on an earlier poll
            tags = tag_feed_text(text, ch)
            if not is_relevant(text, tags, ch):
                self.state.store.mark_seen(post_id)
                continue          # news, fundraising, culture… — not an air-threat post, not stored
            # The offline glossary, instantly. A machine translation replaces it later, off this path (Translations):
            # on the server the free translator refuses every call, and each refusal cost ~3 s per post — a burst
            # of ten posts held the drone reports in them back by half a minute.
            en, fb = (_tr.translate_offline(text), True) if _tr else (text, True)
            posts.append({"post_id": post_id, "channel": ch, "ts": dt, "text": text, "tags": tags, "text_en": en, "en_fallback": fb})
        new = self.state.store.add_feed(posts)
        self.state.set_source(source, True, count=len(posts), new=len(new))
        if ch == "eRadarrua" and geo:
            for p in sorted(posts, key=lambda p: p["ts"], reverse=True):
                summ = geo.parse_eradar_summary(p["text"]) if "◦" in p["text"] else None
                if summ:
                    self.state.set_eradar(p["ts"], summ)
                    break
        for p in sorted(new, key=lambda p: p["ts"]):
            self.state.publish({"kind": "feed", "ts": p["ts"], "post": p})
        tr = getattr(self.state, "tr", None)
        if tr and new:
            for lang in ("en", "fr"):
                if tr.wanted(lang):
                    tr.add(sorted(new, key=lambda p: p["ts"], reverse=True), lang, front=True)
        if OFFICIAL_ALERTS_FROM_TELEGRAM and geo and ch in geo.OFFICIAL_PARSERS:
            self.official.ingest(ch, sorted(new, key=lambda p: p["ts"]), initial=ch not in self.seen_channels)
        self.seen_channels.add(ch)
        return new


class TelegramAPI(threading.Thread):
    """Reads the channels whose web preview is switched off — chyste_nebo — through the Telegram client API.

    chyste_nebo is the channel that states drone heights most often, and its owner switched t.me/s/ off, so the
    preview reader sees nothing. A user session (api_id / api_hash from my.telegram.org, and a one-time sign-in
    with scripts/telegram-login.bat) reads it the way the Telegram app does. That session is a key to a whole
    Telegram account: it lives only in Fly secrets (TG_SESSION) — never in config.json, the logs or the repository.

    It READS the channel — the last 20 posts every 15 s, every 8 s while a missile is in the air — and does not
    subscribe to updates. 25 Sep 2026: with updates on, the session (a personal account) received the update stream
    of every chat and channel it is in; on the 256 MB server that took the whole process down within minutes of
    the first sign-in — pages, the official alert reader, everything stopped answering. Every post goes through
    the same Telegram.ingest() as the preview reader, so nothing downstream knows or cares how it was read."""
    POLL_S, POLL_MISSILE_S = 15, 8
    NAME = "tgapi"

    def __init__(self, state, cfg, tg):
        super().__init__(daemon=True, name="tgapi")
        self.state, self.cfg, self.tg = state, cfg, tg
        self.channels = [c for c in (cfg.get("telegram_api_channels") or []) if c in AUTHORITATIVE_CHANNELS]

    @staticmethod
    def configured(cfg):
        return bool(cfg.get("tg_api_id") and cfg.get("tg_api_hash") and cfg.get("tg_session") and cfg.get("telegram_api_channels"))

    @staticmethod
    def raw_of(ch, msg):
        """A Telethon message → the (post_id, time, text) the preview reader produces for the same post."""
        return (f"{ch}/{msg.id}", msg.date.astimezone(timezone.utc).isoformat(), msg.message or "")

    def _status(self, ok, **kw):
        for ch in self.channels:
            self.state.set_source(f"tga:{ch}", ok, via="Telegram API", **kw)

    def run(self):
        try:
            import asyncio

            from telethon import TelegramClient
            from telethon.sessions import StringSession
        except Exception as e:
            self._status(False, error="telethon is not installed")
            log("telegram api: telethon not available —", e)
            return
        while True:
            try:
                asyncio.run(self._session(TelegramClient, StringSession))
                return            # stopped for good (session revoked)
            except Exception as e:
                self.tg.api_channels -= set(self.channels)     # let the preview reader report the channel again
                self._status(False, error=str(e)[:160])
                log("telegram api error:", e)
                time.sleep(30)

    async def _session(self, TelegramClient, StringSession):
        import asyncio
        try:
            sess = StringSession(self.cfg["tg_session"])
        except ValueError:
            self._status(False, error="TG_SESSION is not a Telegram session — run scripts\\telegram-login.bat again")
            log("telegram api: TG_SESSION is not a valid session string")
            return
        client = TelegramClient(sess, int(self.cfg["tg_api_id"]), self.cfg["tg_api_hash"],
                                device_model="Clear Sky server", app_version=APP_VERSION, receive_updates=False)
        await client.connect()
        if not await client.is_user_authorized():
            self._status(False, error="Telegram session no longer valid — run scripts\\telegram-login.bat again")
            log("telegram api: session not authorised — run scripts/telegram-login.bat")
            await client.disconnect()
            return
        ents = {}
        for ch in self.channels:
            ents[ch] = await client.get_entity(ch)
        self.tg.api_channels |= set(self.channels)
        with self.state.lock:
            for ch in self.channels:
                self.state.sources.pop(f"tg:{ch}", None)       # no longer a failed preview source
        log("telegram api: reading " + ", ".join("@" + c for c in self.channels))

        try:
            while client.is_connected():
                for ch, e in ents.items():
                    try:
                        msgs = await client.get_messages(e, limit=20)
                        # the parsing and the database work run off this thread's event loop
                        await asyncio.to_thread(self.tg.ingest, ch, [self.raw_of(ch, m) for m in reversed(msgs) if m.message], f"tga:{ch}")
                    except Exception as ex:
                        wait = getattr(ex, "seconds", None)       # FloodWaitError: Telegram says how long to wait
                        self.state.set_source(f"tga:{ch}", False, via="Telegram API", error=str(ex)[:160])
                        if wait:
                            await asyncio.sleep(min(int(wait), 600))
                await asyncio.sleep(self.POLL_MISSILE_S if self.state.missile_active() else self.POLL_S)
        finally:
            self.tg.api_channels -= set(self.channels)
            await client.disconnect()
        raise ConnectionError("disconnected from Telegram")


class Watchdog(threading.Thread):
    """A service people take shelter by must never hang in silence.

    25 Sep 2026: the process stopped answering — pages, the version ping, the official alert reader — while
    /healthz still said "ok", and nothing restarted it. Every 20 s this checks that the database and the alert
    state can be had within 15 s, and that the process itself is not starved (its own 20 s sleep did not take a
    minute). A held lock writes every thread's stack to the log, which says exactly where it is stuck; a lock held
    through five checks in a row (about two minutes) and the process exits: Fly starts it again in seconds. A
    starved process is only logged — that is load, and a restart under load would bring every page back at once."""
    INTERVAL, WAIT, MISSES = 20, 15, 5

    def __init__(self, state):
        super().__init__(daemon=True, name="watchdog")
        self.state, self.misses, self.last = state, 0, None

    def check(self):
        why = []
        for name, lk in (("database", self.state.store.lock), ("alert state", self.state.lock)):
            if lk.acquire(timeout=self.WAIT):
                lk.release()
            else:
                why.append(f"{name} lock held for more than {self.WAIT} s")
        now = time.monotonic()
        if self.last is not None and now - self.last > self.INTERVAL * 3 + self.WAIT * 2:
            why.append(f"the process was starved: a {self.INTERVAL} s sleep took {round(now - self.last)} s")
        self.last = now
        return why

    def run(self):
        while True:
            time.sleep(self.INTERVAL)
            why = self.check()
            if not why:
                self.misses = 0
                continue
            locked = any("lock held" in w for w in why)
            # starvation alone is load, not a deadlock: logged, never a restart (a restart under load brings
            # every page back at once and makes it worse)
            self.misses = self.misses + 1 if locked else 0
            log(f"WATCHDOG ({self.misses}/{self.MISSES}): " + "; ".join(why))
            if not locked:
                continue
            names = {t.ident: t.name for t in threading.enumerate()}
            for ident, frame in sys._current_frames().items():
                log(f"--- thread {names.get(ident, ident)}\n" + "".join(traceback.format_stack(frame)[-8:]))
            if self.misses >= self.MISSES:
                log("WATCHDOG: the server is not answering — exiting so that Fly restarts it")
                os._exit(3)


class OfficialAlerts:
    """Turns posts of official administration channels (Kyiv oblast / Kyiv city) into alerts.
    Raion-level alerts are authoritative from here; oblast/city-level ones get merged with keyed sources."""
    NAME = "tg_official"

    def __init__(self, state):
        self.state = state
        self.active = {}   # key -> alert
        self.lock = threading.Lock()

    def ingest(self, channel, posts, initial=False):
        kind, oblast_uid, fixed_name = geo.OFFICIAL_PARSERS[channel]
        changed = False
        cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
        with self.lock:
            for p in posts:
                pts = parse_iso(p["ts"])
                if initial and pts and pts < cutoff:
                    continue
                if kind == "koda":
                    rs = [geo.parse_koda(p["text"])]
                elif kind == "kmda":
                    rs = [geo.parse_kmda(p["text"])]
                elif kind == "etryvoga":
                    rs = geo.parse_etryvoga(p["text"]) or []
                elif kind == "eradar_alert":
                    rs = geo.parse_eradar_alert(p["text"]) or []
                else:
                    r0 = geo.parse_city_siren(p["text"])
                    rs = [dict(r0, name=fixed_name, type="raion")] if r0 else []
                for r in rs:
                    if not r:
                        continue
                    if self._apply(r, kind, oblast_uid, channel, p):
                        changed = True
            stale = datetime.now(timezone.utc) - timedelta(hours=4)
            for k in [k for k, a in self.active.items() if (parse_iso(a["started_at"]) or stale) < stale]:
                self.active.pop(k, None)
                changed = True
            alerts = [dict(a, threats=list(a["threats"])) for a in self.active.values()]
        self.state.set_source(self.NAME, True, count=len(alerts))
        if changed or initial:
            self.state.apply_snapshot(self.NAME, alerts, {"raion"})

    def _apply(self, r, kind, oblast_uid, channel, p):
        """Apply one parsed alert change to self.active (lock held). Returns True when something changed."""
        changed = False
        if True:
            if True:
                if kind == "kmda" or (kind in ("etryvoga", "eradar_alert") and r.get("type") == "city"):
                    key = "tgo:31"
                    if r["kind"] == "end":
                        self.active.pop(key, None)
                        changed = True
                    elif r["kind"] == "start":
                        a = self.active.get(key) or {"key": key, "source": self.NAME, "location_uid": "31", "location_title": "м. Київ",
                                                     "location_title_en": "Kyiv city", "location_type": "oblast", "oblast_uid": "31",
                                                     "alert_type": "air_raid", "alert_level": r["level"], "started_at": p["ts"], "finished_at": None,
                                                     "notes": "@" + channel, "threats": []}
                        a["alert_level"] = "red" if "red" in (a.get("alert_level"), r["level"]) else r["level"]
                        for t in r["threats"]:
                            t = dict(t, started_at=p["ts"])
                            if not any(x["threat_type"] == t["threat_type"] for x in a["threats"]):
                                a["threats"].append(t)
                        self.active[key] = a
                        changed = True
                    elif r["kind"] == "threat":
                        a = self.active.get(key)
                        if a:
                            for t in r["threats"]:
                                t = dict(t, started_at=p["ts"])
                                if not any(x["threat_type"] == t["threat_type"] for x in a["threats"]):
                                    a["threats"].append(t)
                            changed = True
                    return changed
                # raion / oblast alerts (koda-format channels, єТривога, city sirens)
                ouid = r.get("oblast_uid") or oblast_uid
                name = r["name"]
                uid = ouid if r["type"] == "oblast" else ouid + ":" + re.sub(r"\s+", "_", name.lower())
                key = f"tgo:{uid}"
                if r["kind"] == "start":
                    a = self.active.get(key) or {"key": key, "source": self.NAME, "location_uid": uid, "location_title": name,
                                                 "location_title_en": None, "location_type": r["type"], "oblast_uid": ouid,
                                                 "alert_type": "air_raid", "alert_level": r["level"], "started_at": p["ts"], "finished_at": None,
                                                 "notes": "@" + channel, "threats": []}
                    a["alert_level"] = r["level"]
                    if a["notes"] != "@" + channel and "@" + channel not in a["notes"]:
                        a["notes"] += " @" + channel
                    for t in r["threats"]:
                        t = dict(t, started_at=p["ts"])
                        if not any(x["threat_type"] == t["threat_type"] for x in a["threats"]):
                            a["threats"].append(t)
                    self.active[key] = a
                elif r["kind"] == "threat":
                    a = self.active.get(key)
                    if not a:
                        a = {"key": key, "source": self.NAME, "location_uid": uid, "location_title": name, "location_title_en": None,
                             "location_type": r["type"], "oblast_uid": ouid, "alert_type": "air_raid", "alert_level": "yellow",
                             "started_at": p["ts"], "finished_at": None, "notes": "@" + channel, "threats": []}
                        self.active[key] = a
                    for t in r["threats"]:
                        t = dict(t, started_at=p["ts"])
                        if not any(x["threat_type"] == t["threat_type"] for x in a["threats"]):
                            a["threats"].append(t)
                else:
                    self.active.pop(key, None)
                changed = True
        return changed


class Demo(threading.Thread):
    """Fake alert generator so the dashboard can be tested without keys."""
    NAME = "demo"

    def __init__(self, state, cfg):
        super().__init__(daemon=True)
        self.state, self.cfg = state, cfg

    def run(self):
        import random
        active = {}
        threats_pool = [("drones", "yellow", "Загроза застосування ударних БпЛА"), ("ballistic_missiles", "red", "Загроза застосування балістичного озброєння"),
                        ("mig31k_departure", "red", "Зліт МіГ-31К"), ("cruise_missiles", "red", "Пуски крилатих ракет")]
        while True:
            uid = random.choice([o[0] for o in OBLASTS] + ["31", "14", "31"])
            if uid in active and random.random() < 0.5:
                del active[uid]
            else:
                uk, en = NAME_BY_UID[uid]
                t = random.sample(threats_pool, k=random.randint(1, 2))
                active[uid] = {"key": f"demo:{uid}", "source": "demo", "location_uid": uid, "location_title": uk,
                               "location_title_en": en, "location_type": "oblast", "oblast_uid": uid, "alert_type": "air_raid",
                               "alert_level": t[0][1], "started_at": now_iso(), "finished_at": None, "notes": "DEMO",
                               "threats": [{"threat_type": a, "level": b, "started_at": now_iso(), "source_message": c} for a, b, c in t]}
            self.state.set_source(self.NAME, True, count=len(active))
            self.state.apply_snapshot(self.NAME, list(active.values()), {"oblast", "raion", "city", "hromada", "unknown"})
            dtext = random.choice(["🛵 Ударні БпЛА на Київщині ➡️ курсом на Київ", "Загроза застосування балістики з півдня", "Зліт МіГ-31К з аеродрому Саваслейка", "Відбій загрози балістики",
                                                       "🛵 Ударні БпЛА на Чернігівщині в р-ні Добрянки курс південний", "🏍 Реактивний БпЛА в р-ні Володарки (Київщина) курс північний.",
                                                       "🛵 Група ударних БпЛА на Сумщині курсом на Полтавщину", "🚀 Швидкісна ціль на Дніпропетровщині курсом на Павлоград!",
                                                       "🛵 БпЛА в районі Кременчуцького водосховища, курс північно-західний.", "💣 КАБи на Донеччину.", "🛵 Ударні БпЛА на півночі Чернігівщини на/повз Ріпки ➡️ у південно-західному напрямку.",
                                                       "🛵 Харківщина: ударні БпЛА ➡️ курсом на Богодухів.", "🛵 Житомирщина - БпЛА поряд н.п. Ємільчине курсом на Рівненську область.", "🏍 Реактивний БпЛА на Білу Церкву з півдня",
                                                       "🛵 Ударні БпЛА на Черкащині повз Канів курсом на Бориспіль", "🚀 Крилаті ракети з Чорного моря курсом на Одесу",
                                                       "🏍 Реактивний БпЛА в р-ні Козельця курсом на Бровари", "🛵 Ударні БпЛА на Київщині повз Іванків курсом на Вишгород", "🛵 2х БпЛА від Фастова на Васильків",
                                                       "🛵 БпЛА над Обуховом курсом на столицю", "🅿️ 1х реактив від Ніжина у напрямку Києва", "🟡 Броварський район — повітряна тривога, жовтий рівень: Дронова загроза (жовтий рівень)",
                                                       "1х зниження Троєщина.", "🅿️ БпЛА збито в районі Василькова", "По реактивам довкола Києва та Броварів чисто.", "Вибух у Бортничах, попередньо ППО"])
            dpost = {"post_id": f"demo/{int(time.time())}", "channel": "demo", "ts": now_iso(), "text": dtext, "tags": tag_feed_text(dtext), "text_en": to_en(dtext)}
            self.state.store.add_feed([dpost])
            self.state.publish({"kind": "feed", "ts": now_iso(), "post": dpost})
            time.sleep(random.randint(8, 20))


# ---------------------------------------------------------------------------
# Web Push (phone notifications even with the page closed)
# ---------------------------------------------------------------------------
RAION_STEMS = [("бровар", "brovary"), ("бориспіл", "boryspil"), ("бучан", "bucha"), ("вишгород", "vyshhorod"), ("обухів", "obukhiv"), ("білоцерків", "bila-tserkva"), ("фастів", "fastiv")]


class Pusher:
    """Sends Web Push notifications for Kyiv events: city / oblast-wide starts, ends and threats, raion events
    of the subscriber's priority raion, and MiG-31K / ballistic warnings. One background worker, deduped."""
    def __init__(self, store):
        self.store = store
        self.enabled = bool(_push and _push.AVAILABLE)
        self.priv = self.pub = None
        if self.enabled:
            self.priv, self.pub = store.kv_get("vapid_priv"), store.kv_get("vapid_pub")
            if not (self.priv and self.pub):
                self.priv, self.pub = _push.generate_vapid()
                store.kv_set("vapid_priv", self.priv); store.kv_set("vapid_pub", self.pub)
        self.q = []
        self.cond = threading.Condition()
        self.recent = {}
        if self.enabled:
            threading.Thread(target=self._run, daemon=True, name="push").start()

    def on_event(self, ev):
        if not self.enabled:
            return
        a = ev["alert"]; kind = ev["kind"]
        ou = a.get("oblast_uid")
        title = body = None; tag = "alert"
        thr = ev.get("threat") or {}
        tt = thr.get("threat_type")
        # A MiG-31K take-off is not a Kyiv event. The Air Force declares an alert over the whole country for
        # it, because the aircraft can turn toward anywhere before it fires, and the channels post it as
        # "тривога по усій території країни". Gating it on the Kyiv oblast uid meant a subscriber in Kharkiv
        # got nothing at all for the one warning that comes an hour ahead of the missile.
        if kind == "threat" and tt == "mig31k_departure":
            title = "✈ MiG-31K airborne — ballistic risk, country-wide"
            body = (thr.get("source_message") or "")[:120]; tag = "threat"
        elif kind == "threat" and tt == "ballistic_missiles" and ou in ("31", "14"):
            # Still oblast-gated: a ballistic launch concerns a region, and a subscription stores a coarse
            # home cell with no region on it. Positioned ballistic markers are already covered region-wide by
            # proximity_watch; the rest waits for the subscriber's own region to be a stored thing.
            title = "🚀 Ballistic threat — shelter now"
            body = (thr.get("source_message") or "")[:120]; tag = "threat"
        # Siren-start and all-clear pushes are gone. "Kyiv oblast — alert" says nothing a person can act on:
        # not what, not where, not how far — the siren itself already said that much, louder. A push that
        # cannot be acted on still wakes somebody at 3 a.m., and after enough of those the ones that matter
        # get swiped away too. What is left is what is specific: a MiG-31K or ballistic launch, above, and a
        # target actually near the reader, in proximity_watch.
        if not title:
            return
        self._queue(title, body, tag)

    def _queue(self, title, body, tag, raion=None):
        k = (title, raion); now = time.time()
        if now - self.recent.get(k, 0) < 90:
            return
        self.recent[k] = now
        with self.cond:
            self.q.append({"title": title, "body": body, "tag": tag, "raion": raion, "ts": now_iso()})
            self.cond.notify()

    def queue_direct(self, title, body, tag, endpoint):
        """One device only — used by the proximity watcher, which does its own deduplication."""
        with self.cond:
            self.q.append({"title": title, "body": body, "tag": tag, "raion": None, "only": endpoint, "ts": now_iso()})
            self.cond.notify()

    def send_test(self, endpoint=None):
        with self.cond:
            self.q.append({"title": "Clear Sky", "body": "Push notifications are on for this phone.", "tag": "test", "raion": None, "only": endpoint, "ts": now_iso()})
            self.cond.notify()

    def _run(self):
        while True:
            with self.cond:
                while not self.q:
                    self.cond.wait()
                n = self.q.pop(0)
            subs = self.store.push_all()
            for s_ in subs:
                if n.get("only") and s_["endpoint"] != n["only"]:
                    continue
                if n.get("raion") and (s_["home"] or {}).get("raion") != n["raion"]:
                    continue
                try:
                    code, gone = _push.send(s_["sub"], {"title": n["title"], "body": n["body"], "tag": n["tag"], "ts": n["ts"], "url": "/m"}, self.priv, self.pub)
                except Exception as e:
                    log("push error:", e); continue
                if gone:
                    self.store.push_remove(s_["endpoint"])


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


# Notifications by distance from the subscriber's own position.
#
# The rules exist because a false alert at 3 a.m. costs more than a missed one here: the official siren is the
# primary warning, this is the detail on top of it.
#   - only live targets: never a stale one, never a closed track, never an outcome marker;
#   - each target notifies a given device once (30 min memory), so a target re-reported every minute is one push;
#   - at most one push per device every 2 minutes;
#   - the distance sent is the distance to where the post said the target was, and the text says so.
PROX_SEEN = {}          # endpoint -> {marker id: time}
PROX_LAST = {}          # endpoint -> time of the last push to that device


def proximity_watch(state, interval=20):
    while True:
        time.sleep(interval)
        try:
            pusher = getattr(state, "pusher", None)
            if not (pusher and pusher.enabled):
                continue
            subs = [x for x in state.store.push_all() if (x.get("home") or {}).get("lat") is not None]
            if not subs:
                continue
            live = [m for m in state.markers_now() if not m.get("status") and not m.get("stale") and not m.get("endedBy")]
            if not live:
                continue
            now = time.time()
            for sb in subs:
                ep = sb["endpoint"]; home = sb["home"]
                radius = float(home.get("radius") or 15)
                seen = PROX_SEEN.setdefault(ep, {})
                for mid, t0 in list(seen.items()):
                    if now - t0 > 1800:
                        del seen[mid]
                near = []
                urgent = []
                for m in live:
                    if m["id"] in seen:
                        continue
                    # the stored point is a ~10 km cell, not a house, so the test is widened by half a
                    # cell: nobody inside their own radius is missed because their village was rounded
                    d = haversine_km(home["lat"], home["lon"], m["lat"], m["lon"])
                    # A ballistic missile crosses any radius a person can choose in seconds. Filtering it by
                    # that radius does not filter, it deletes the warning. These bypass it and alert the
                    # whole region — see geo.IMMEDIATE_TYPES for why the list is exactly this short.
                    if geo and geo.is_immediate(m["type"]):
                        if d <= geo.REGION_ALERT_KM:
                            urgent.append((d, m))
                    elif d <= radius + HOME_CELL_KM / 2:
                        near.append((d, m))
                if not (near or urgent):
                    continue
                # A ballistic warning is never held back by the every-two-minutes throttle the rest obey.
                if not urgent and now - PROX_LAST.get(ep, 0) < 120:
                    continue
                if urgent:
                    urgent.sort(key=lambda x: x[0])
                    m = urgent[0][1]
                    for _, mm in urgent:
                        seen[mm["id"]] = now
                    PROX_LAST[ep] = now
                    kind = THREAT_EN.get(m["type"], "Ballistic threat")
                    place = m.get("place") or "?"
                    # No distance and no direction: at this speed neither is something the reader can act on,
                    # and the stored home is a grid cell that could not support a distance anyway.
                    pusher.queue_direct(
                        f"\U0001f6a8 {kind} in your region — shelter now",
                        f"Reported near {place} at {fmt_kyiv(m['ts'])} \u00b7 position from a public post, not radar",
                        "threat", ep)
                    # Nothing else goes out in the same breath. A Shahed 8 km away still matters, but not in
                    # the same second as this, and it will be re-reported on the next pass.
                    continue
                near.sort(key=lambda x: x[0])
                d, m = near[0]
                for _, mm in near:
                    seen[mm["id"]] = now
                PROX_LAST[ep] = now
                kind = {"drones": "Jet drone" if m.get("jet") else "Shahed"}.get(m["type"]) or THREAT_EN.get(m["type"], "Target")
                extra = f" ×{m['count']}" if (m.get("count") or 1) > 1 else ""
                more = f" (+{len(near) - 1} more)" if len(near) > 1 else ""
                place = m.get("place") or "?"
                hdg = f", heading {compass_en(m['heading'])}" if m.get("heading") is not None else ""
                # The stored home is a grid cell, so "23 km from you" would be precision this no longer
                # has. The reader gets the radius they chose and the place the POST named — both true.
                pusher.queue_direct(
                    f"\u26a0 {kind}{extra} within {round(radius)} km of you{more}",
                    f"Reported near {place}{hdg} at {fmt_kyiv(m['ts'])} \u00b7 position from a public post, not radar",
                    "near", ep)
        except Exception as e:
            log("proximity error:", e)


THREAT_EN = {"ballistic_missiles": "Ballistic missile", "cruise_missiles": "Cruise missile", "unspecified_missiles": "Missile",
             "supersonic_missiles": "Supersonic missile (Kh-22/32)",
             "guided_aerial_bombs": "Guided bomb (KAB)", "tactic_aircraft_activity": "Tactical aviation"}


def compass_en(deg):
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int(round((deg % 360) / 45)) % 8]


def fmt_kyiv(ts):
    d = parse_iso(ts)
    if not d:
        return "?"
    return d.astimezone(timezone(timedelta(hours=3))).strftime("%H:%M")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    state: State = None  # set at startup

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send_cached(self, hit):
        """A cached answer: the same bytes for everybody, gzipped once when the client takes it."""
        _, body, gz, _, _ = hit
        use_gz = gz is not None and "gzip" in (self.headers.get("Accept-Encoding") or "")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Accept-Encoding")
        if use_gz:
            self.send_header("Content-Encoding", "gzip")
        data = gz if use_gz else body
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _peer_ip(self):
        # only ever used to make a daily, salted, one-way hash — never stored, never logged
        fwd = self.headers.get("X-Forwarded-For") or ""
        return (fwd.split(",")[0].strip() if fwd else (self.client_address[0] if self.client_address else ""))

    # A marker somebody says is wrong. The whole point is that this costs one tap while looking at the map:
    # capture that is any harder than noticing simply does not happen during a raid.
    _corpus_cache = None

    def _corpus_pending(self, limit=30):
        """Cases nobody has ruled on yet, with what this build makes of them."""
        cls = type(self)
        if cls._corpus_cache is None:
            cases = []
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "data", "corpus.jsonl")
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            cases.append(json.loads(line))
            except Exception:
                cases = []
            cls._corpus_cache = cases
        done = self.state.store.corpus_reviews()
        out, total_pending = [], 0
        for c in cls._corpus_cache:
            if c.get("state") != "pending" or c["id"] in done:
                continue
            total_pending += 1
            if len(out) >= limit:
                continue
            reading = None
            if geo:
                try:
                    clean = _clean_post(c["text"])
                    reading = {"tags": sorted(tag_feed_text(clean, c["channel"])),
                               "markers": [{"status": m.get("status"), "type": m.get("type"),
                                            "place": m.get("place"), "count": m.get("count"),
                                            "jet": bool(m.get("jet")), "likely": m.get("likely"),
                                            "conf": ((m.get("evidence") or {}).get("position") or {}).get("confidence")}
                                           for m in (geo.parse_for_channel(c["channel"], clean) or [])]}
                except Exception as e:
                    reading = {"error": str(e)[:120], "tags": [], "markers": []}
            # Reviewing a reading means judging it against what the post SAYS. Ukrainian is not the first
            # language of everybody doing the reviewing, and a case with no way back to the source cannot be
            # checked at all — so every case carries its English text and a link to the post it came from.
            post_id, text_en = self.state.store.feed_find(c["channel"], c["text"])
            en_src = "feed" if text_en else None
            if not text_en:
                cached = self.state.store.kv_get("corpus_en:" + c["id"])
                if cached:
                    text_en, en_src = cached, "online"
            if not text_en and _tr:
                try:
                    # The glossary, not the network: this runs while somebody waits for the page, and 25 cases
                    # at a third of a second each is not a page load. A proper translation for the one case on
                    # screen is fetched separately by /api/corpus/translate.
                    text_en, en_src = _tr.translate_offline(c["text"]), "offline"
                except Exception:
                    text_en = None
            out.append({"id": c["id"], "channel": c["channel"], "ts": c.get("ts", ""),
                        "text": c["text"], "text_en": text_en, "en_src": en_src,
                        "post_id": post_id or c.get("post_id"), "reading": reading})
        return {"cases": out, "pending": total_pending, "reviewed": len(done)}

    def _flag(self, data):
        st = self.state
        mid = str(data.get("marker") or "")[:120]
        post_id = mid.split("#")[0] if "#" in mid else (str(data.get("post_id") or "")[:120] or None)
        reason = str(data.get("reason") or "other")[:32]
        if reason not in ("place", "type", "not_a_threat", "already_gone", "other"):
            reason = "other"
        text, channel = "", str(data.get("channel") or "")[:64]
        if post_id:
            try:
                with st.store.lock:
                    row = st.store.conn.execute("SELECT channel,text FROM feed WHERE post_id=?", (post_id,)).fetchone()
                if row:
                    channel, text = row[0], row[1]
            except Exception:
                pass
        usage = getattr(st, "usage", None)
        day_hash = ""
        if usage:
            try:
                import hashlib
                with usage.lock:
                    usage._roll()
                    day_hash = hashlib.blake2s(
                        usage.salt + (self._peer_ip() or "").encode()
                        + (self.headers.get("User-Agent") or "")[:120].encode(), digest_size=8).hexdigest()
            except Exception:
                day_hash = ""
        ok = st.store.flag_add({"ts": now_iso(), "post_id": post_id, "channel": channel, "marker_id": mid,
                                "place": str(data.get("place") or "")[:80], "kind": str(data.get("kind") or "")[:40],
                                "reason": reason, "note": data.get("note"), "day_hash": day_hash, "text": text})
        return self._json({"ok": bool(ok)})

    # TWO DIFFERENT KEYS, and confusing them takes the map away from everybody.
    #
    #   ACCESS_KEY  locks the WHOLE app behind a login form. It exists for a private deployment — a unit, a
    #               closed group. On a public instance it is an outage: every reader gets the form instead of
    #               the map, which during a raid is the worst thing this server can do.
    #   ADMIN_KEY   locks only /admin, /api/usage and /api/flags. The map stays open to everyone. This is what
    #               a public deployment wants, and it is what the dashboard needs.
    #
    # ADMIN_KEY falls back to ACCESS_KEY so an existing private deployment keeps working unchanged.
    def _admin_key(self):
        return (self.state.cfg.get("admin_key") or os.environ.get("ADMIN_KEY")
                or self.state.cfg.get("access_key") or os.environ.get("ACCESS_KEY") or "")

    def _admin_ok(self, q):
        key = self._admin_key()
        if not key:
            return False               # no key configured: the dashboard does not exist, it is never open
        if q.get("key", [None])[0] == key:
            return True
        cookie = self.headers.get("Cookie") or ""
        return any(c.strip() in (f"uadm={key}", f"uak={key}") for c in cookie.split(";"))

    def _authorized(self, u, q):
        key = self.state.cfg.get("access_key") or os.environ.get("ACCESS_KEY") or ""
        if not key:
            return True
        if q.get("key", [None])[0] == key:
            return "set"
        cookie = self.headers.get("Cookie") or ""
        return any(c.strip() == f"uak={key}" for c in cookie.split(";"))

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        st = self.state
        if u.path == "/healthz":
            # "ok" only when the database can actually be had: it said "ok" all through the 25 Sep freeze
            if not st.store.lock.acquire(timeout=3):
                return self._json({"ok": False, "stuck": "database"}, 503)
            st.store.lock.release()
            return self._json({"ok": True})
        if u.path.startswith("/static/logo") or u.path == "/favicon.ico":
            return self._file(u.path.split("/")[-1] if u.path != "/favicon.ico" else "logo-cs-64.png", "image/png")
        auth = self._authorized(u, q)
        if not auth:
            body = b"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><body style='font-family:system-ui;background:#0b0e13;color:#e6e9ef;padding:40px;text-align:center'><img src='/static/logo-192.png' style='width:96px;border-radius:18px'><h2>Clear Sky</h2><form><input name=key placeholder='access key' style='padding:10px;font-size:16px;border-radius:8px;border:1px solid #333'> <button style='padding:10px 14px;border-radius:8px'>Enter</button></form></body>"
            self.send_response(401); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if auth == "set":
            self.send_response(302); self.send_header("Location", u.path or "/"); self.send_header("Set-Cookie", f"uak={q['key'][0]}; Path=/; Max-Age=31536000; SameSite=Lax"); self.end_headers(); return
        try:
            if u.path in ("/", "/m", "/k", "/kyiv"):
                usage = getattr(st, "usage", None)
                if usage:
                    ua = self.headers.get("User-Agent") or ""
                    usage.hit(self._peer_ip(), ua, "load",
                              lang=(q.get("lang", [None])[0] or ("uk" if "uk" in (self.headers.get("Accept-Language") or "").lower() else None)),
                              pwa="standalone" in (self.headers.get("Sec-Fetch-Site") or ""), lite=False)
                return self._file("kyiv.html", "text/html; charset=utf-8")
            if u.path in ("/desktop", "/index.html", "/dash"):
                return self._file("index.html", "text/html; charset=utf-8")
            if u.path in ("/ua", "/mobile", "/mobile.html"):
                return self._file("mobile.html", "text/html; charset=utf-8")
            if u.path == "/manifest.json":
                return self._file("manifest.json", "application/manifest+json")
            # The light page: one place, its official alert status, the threats within 60 km. Its own manifest,
            # its own scope, so it installs on the phone as a separate app from the full map.
            if u.path in ("/light", "/l"):
                usage = getattr(st, "usage", None)
                if usage:
                    usage.hit(self._peer_ip(), self.headers.get("User-Agent") or "", "load", lang=None, pwa=False, lite=True)
                return self._file("light.html", "text/html; charset=utf-8")
            if u.path == "/light.webmanifest":
                return self._file("light.webmanifest", "application/manifest+json")
            if u.path == "/api/places":
                oq = q.get("oblast", ["14,31"])[0]
                obl = None if oq == "all" else set(oq.split(","))
                pl = [{"name": n.split(" (")[0], "lon": v[0], "lat": v[1], "oblast_uid": v[2]} for n, v in (geo.PLACES.items() if geo else []) if obl is None or v[2] in obl]
                return self._json({"places": pl})
            if u.path == "/api/markers":
                st.markers_now()
                return self._send_cached(st._resp["markers"])
            # The dashboard is yours alone: it needs ACCESS_KEY, and it refuses to serve anything when no key
            # is configured, so an open deployment can never expose it by accident.
            if u.path in ("/api/usage", "/admin"):
                if not self._admin_ok(q):
                    return self._json({"error": "set ADMIN_KEY and open /admin?key=… — ADMIN_KEY protects only "
                                                "the dashboard; ACCESS_KEY would lock the whole map"}, 403)
                if u.path == "/admin":
                    if q.get("key", [None])[0]:       # remember it so the page's own fetches are authorised
                        self.send_response(302); self.send_header("Location", "/admin")
                        self.send_header("Set-Cookie", f"uadm={q['key'][0]}; Path=/; Max-Age=31536000; SameSite=Lax")
                        self.end_headers(); return
                    return self._file("admin.html", "text/html; charset=utf-8")
                usage = getattr(st, "usage", None)
                return self._json(usage.report(int(q.get("days", ["30"])[0])) if usage else {"days": []})
            if u.path == "/api/version":
                usage = getattr(st, "usage", None)
                if usage:
                    usage.hit(self._peer_ip(), self.headers.get("User-Agent") or "", "ping")
                with st.cond:
                    seq = st.seq
                lvl = st.missile_active()
                # `missile` stays a boolean for any client still on an older build; `msl` carries the level.
                return self._json({"v": f"{seq}-{st.store.feed_count()}", "now": now_iso(),
                                   "missile": bool(lvl), "msl": lvl, "build": BUILD, "app": APP_VERSION})
            if u.path == "/api/stats":
                days = max(1, min(int(q.get("days", ["14"])[0]), 30))
                return self._send_cached(st.cached(f"stats:{days}", 120, lambda: st.stats(days)))
            if u.path == "/api/impacts":
                hours = max(1, min(int(q.get("hours", ["24"])[0]), 168))
                return self._send_cached(st.cached(f"impacts:{hours}", 30, lambda: {"now": now_iso(), "hours": hours, "impacts": st.impacts(hours)}))
            if u.path == "/api/state":
                return self._send_cached(st.cached("state", 5, st.snapshot, ver=st.seq))
            if u.path == "/api/feed":
                limit = max(1, min(int(q.get("limit", ["80"])[0]), 200))
                lang = q.get("lang", [""])[0]
                hit = st.cached(f"feed:{limit}", 15, lambda: {"feed": st.store.feed(limit)}, ver=st.seq)
                tr = getattr(st, "tr", None)
                if tr and lang in ("en", "fr"):
                    tr.want(lang)
                    tr.add([p for p in hit[3]["feed"] if not (p["en_mt"] if lang == "en" else p["text_fr"])], lang)
                return self._send_cached(hit)
            if u.path == "/api/history":
                hours = int(q.get("hours", ["24"])[0])
                obl = q.get("oblast", [None])[0]
                return self._json({"alerts": st.store.history(hours, obl.split(",") if obl else None)})
            if u.path == "/api/events_log":
                return self._json({"events": st.store.events(int(q.get("limit", ["200"])[0]))})
            # Flagged readings are yours alone, on the same terms as the dashboard: they quote posts and
            # somebody's opinion of them, and neither belongs on an open endpoint.
            # The corpus, for reviewing from a browser instead of a terminal. The reading shown is computed
            # by THIS build, not the one recorded when the case was captured, because that is what the
            # reviewer is being asked to judge.
            if u.path == "/api/corpus":
                if not self._admin_ok(q):
                    return self._json({"error": "set ADMIN_KEY to review the corpus"}, 403)
                return self._json(self._corpus_pending(int(q.get("limit", ["30"])[0])))
            # The dashboard's health view: sources, channels, how early the map was. Yours alone as well.
            if u.path == "/api/health":
                if not self._admin_ok(q):
                    return self._json({"error": "set ADMIN_KEY to read the source health"}, 403)
                return self._json(st.health())
            if u.path == "/api/flags":
                if not self._admin_ok(q):
                    return self._json({"error": "set ADMIN_KEY to read flagged readings"}, 403)
                return self._json({"flags": st.store.flags(int(q.get("limit", ["200"])[0]),
                                                           include_done=q.get("done", ["0"])[0] == "1")})
            if u.path == "/api/stream":
                return self._stream()
            if u.path == "/sw.js":
                return self._file("sw.js", "text/javascript")
            if u.path == "/api/push/key":
                pu = getattr(st, "pusher", None)
                return self._json({"enabled": bool(pu and pu.enabled), "key": pu.pub if pu and pu.enabled else None, "subs": len(st.store.push_all()) if pu and pu.enabled else 0})
            if u.path.startswith("/static/"):
                return self._file(u.path[len("/static/"):], None)
            if u.path == "/favicon.ico":
                return self._file("logo-cs-64.png", "image/png")
            self._json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            traceback.print_exc()
            try:
                self._json({"error": str(e)}, 500)
            except Exception:
                pass

    def do_POST(self):
        u = urlparse(self.path); q = parse_qs(u.query); st = self.state
        if not self._authorized(u, q):
            return self._json({"error": "unauthorized"}, 401)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
            if u.path == "/api/flag":
                return self._flag(data)
            if u.path == "/api/corpus/review":
                if not self._admin_ok(q):
                    return self._json({"error": "unauthorized"}, 401)
                cid, state = str(data.get("id") or "")[:64], str(data.get("state") or "")
                if not cid or state not in ("verified", "known_bad", "pending"):
                    return self._json({"error": "bad review"}, 400)
                st.store.corpus_review(cid, state, data.get("note"))
                return self._json({"ok": True})
            if u.path == "/api/corpus/translate":
                # A real translation of the ONE case on screen. The list endpoint ships the offline glossary
                # so the panel is never empty, and this replaces it for the case actually being judged —
                # transliterated Ukrainian is not something anybody can review a reading against. One call per
                # case, cached forever, and the offline text stands if the service is unreachable.
                if not self._admin_ok(q):
                    return self._json({"error": "unauthorized"}, 401)
                cid = str(data.get("id") or "")[:64]
                text = str(data.get("text") or "")[:4000]
                if not cid or not text:
                    return self._json({"error": "bad request"}, 400)
                key = "corpus_en:" + cid
                cached = None if data.get("force") else st.store.kv_get(key)
                if cached:
                    return self._json({"text_en": cached, "en_src": "online"})
                if not _tr:
                    return self._json({"error": "no translator on this server"}, 503)
                try:
                    out = _tr.translate(text)
                except Exception as e:
                    return self._json({"error": str(e)[:120]}, 502)
                if out and getattr(_tr, "LAST_OK", [False])[0]:
                    st.store.kv_set(key, out)
                    return self._json({"text_en": out, "en_src": "online"})
                return self._json({"text_en": out, "en_src": "offline"})
            pu = getattr(st, "pusher", None)
            if not (pu and pu.enabled):
                return self._json({"error": "push not available on this server (pip install cryptography)"}, 503)
            if u.path == "/api/push/subscribe":
                sub = data.get("subscription") or {}
                if not sub.get("endpoint"):
                    return self._json({"error": "bad subscription"}, 400)
                st.store.push_add(sub, data.get("home") or {})
                return self._json({"ok": True})
            if u.path == "/api/push/unsubscribe":
                st.store.push_remove((data.get("endpoint") or ""))
                return self._json({"ok": True})
            if u.path == "/api/push/test":
                pu.send_test(data.get("endpoint"))
                return self._json({"ok": True})
            self._json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            self._json({"error": str(e)}, 500)

    def _file(self, name, ctype):
        p = os.path.normpath(os.path.join(STATIC, name))
        if not p.startswith(STATIC) or not os.path.isfile(p):
            return self._json({"error": "not found"}, 404)
        with open(p, "rb") as f:
            body = f.read()
        ext = p.rsplit(".", 1)[-1]
        if ctype is None:
            ctype = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "png": "image/png", "svg": "image/svg+xml", "json": "application/json", "html": "text/html; charset=utf-8", "md": "text/markdown"}.get(ext, "application/octet-stream")
        # On 2G every kilobyte is a second. Text goes out gzipped (the map outlines shrink to a third), and a
        # file the phone already holds is answered with a 304 instead of being sent again. "no-cache" still
        # makes the browser ask every time, so a new version is picked up exactly as with "no-store".
        etag = '"' + hashlib.md5(body).hexdigest()[:16] + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        enc = None
        if ext in ("js", "css", "json", "html", "svg", "md") and len(body) > 1400 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body, enc = _gzipped(p, body), "gzip"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", etag)
        self.send_header("Vary", "Accept-Encoding")
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = self.state.subscribe()
        # a page reading English or French keeps its language "wanted" for as long as it is open
        lang = (parse_qs(urlparse(self.path).query).get("lang") or [""])[0]
        tr = getattr(self.state, "tr", None)
        try:
            self.wfile.write(b"event: hello\ndata: {}\n\n")
            self.wfile.flush()
            last_beat = time.time()
            if tr:
                tr.want(lang)
            while True:
                with self.state.cond:
                    self.state.cond.wait(timeout=15)
                    items, q[:] = list(q), []
                for ev in items:
                    self.wfile.write(f"event: {ev['kind']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                if time.time() - last_beat > 20:
                    self.wfile.write(b": ping\n\n")
                    last_beat = time.time()
                    if tr:
                        tr.want(lang)
                self.wfile.flush()
        finally:
            self.state.unsubscribe(q)


# ---------------------------------------------------------------------------
def load_config(args):
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    else:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        log("created config.json — add your alerts.in.ua token there")
    if args.demo:
        cfg["demo"] = True
    if args.port:
        cfg["port"] = args.port
    if os.environ.get("PORT"):
        cfg["port"] = int(os.environ["PORT"])
        cfg["bind"] = "0.0.0.0"
    if os.environ.get("ALERTS_IN_UA_TOKEN"):
        cfg["alerts_in_ua_token"] = os.environ["ALERTS_IN_UA_TOKEN"]
    if os.environ.get("UKRAINEALARM_KEY"):
        cfg["ukrainealarm_key"] = os.environ["UKRAINEALARM_KEY"]
    for env, key in (("DEEPL_KEY", "deepl_key"), ("GOOGLE_TRANSLATE_KEY", "google_translate_key"),
                     ("TG_API_ID", "tg_api_id"), ("TG_API_HASH", "tg_api_hash"), ("TG_SESSION", "tg_session")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


def lan_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="UA Alerts local tracker")
    ap.add_argument("--demo", action="store_true", help="generate fake alerts (no keys needed)")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()
    cfg = load_config(args)

    store = Store(DB_PATH)
    # re-tag the last hours with the current rules (a rule fix must also apply to posts already stored — a forecast
    # tagged 'ballistic' before the fix would otherwise keep the strip lit until it ages out)
    try:
        with store.lock:
            rows = store.conn.execute("SELECT post_id, channel, text, tags FROM feed WHERE ts>=?", ((datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(),)).fetchall()
            n = 0
            for pid, ch, text, tg in rows:
                new = tag_feed_text(text, ch)
                if json.dumps(new) != tg:
                    store.conn.execute("UPDATE feed SET tags=? WHERE post_id=?", (json.dumps(new), pid)); n += 1
            store.conn.commit()
        if n:
            log(f"re-tagged {n} recent post(s)")
    except Exception as e:
        log(f"retag failed: {e}")
    global BUILD
    BUILD = build_id()
    log("build", BUILD)
    state = State(store, cfg)
    state.usage = Usage(store)
    state.pusher = Pusher(store)
    log("push notifications:", "enabled (VAPID key ready)" if state.pusher.enabled else "disabled — pip install cryptography to enable")
    Handler.state = state
    try:
        n = store.coarsen_homes()
        if n:
            log(f"push: coarsened {n} stored home location(s) to ~{int(HOME_CELL_KM)} km cells")
    except Exception as e:
        log("could not coarsen stored homes:", e)
    if state.pusher.enabled:
        threading.Thread(target=proximity_watch, args=(state,), daemon=True, name="proximity").start()

    keyed = False
    if cfg.get("demo"):
        Demo(state, cfg).start()
        keyed = True
        log("DEMO mode — fake alerts")
    else:
        if cfg.get("alerts_in_ua_token"):
            AlertsInUa(state, cfg).start(); keyed = True
            log("source: alerts.in.ua (primary)")
        # the official data by raion: the API itself with a key, the keyless proxy of it without one
        detail = None
        if cfg.get("ukrainealarm_key") or cfg.get("use_siren_proxy", True):
            detail = UkraineAlarm(state, cfg)
            detail.start()
            log(f"source: {detail.NAME} — official alerts by raion" + ("" if keyed else " (primary)"))
        if cfg.get("use_ubilling_fallback", True):
            Ubilling(state, cfg, primary=not keyed, detail=detail).start()
            log("source: ubilling keyless mirror" + (" (cross-check)" if keyed else
                " (stands in, oblast level only, when the raion-level source is down)" if detail else " (primary — oblast level only)"))
    if cfg.get("telegram_channels"):
        tg = Telegram(state, cfg)
        tg.start()
        log("feed:", ", ".join("t.me/" + c for c in cfg["telegram_channels"]))
        if TelegramAPI.configured(cfg):
            TelegramAPI(state, cfg, tg).start()
        elif cfg.get("telegram_api_channels"):
            log("telegram api: not configured — " + ", ".join("@" + c for c in cfg["telegram_api_channels"])
                + " can only be read after scripts/telegram-login.bat (TG_API_ID, TG_API_HASH, TG_SESSION)")

    bind = cfg.get("bind", "127.0.0.1")
    # machine translation of the feed: DeepL / Google Cloud with a key (config or env), off the alert path
    if _tr:
        _tr.configure(deepl=cfg.get("deepl_key"), google_cloud=cfg.get("google_translate_key"))
        log("translation: " + (", ".join(n for n, _ in _tr.backends()) or "offline glossary only") + " — machine translation on demand")
    state.tr = Translations(state)
    state.tr.start()
    Watchdog(state).start()

    # the listen backlog: Python's default of 5 left a burst of pages (a new post, a restart) waiting on SYN retries
    ThreadingHTTPServer.request_queue_size = 128
    srv = ThreadingHTTPServer((bind, int(cfg["port"])), Handler)
    srv.daemon_threads = True
    log(f"dashboard → http://localhost:{cfg['port']}   mobile → http://localhost:{cfg['port']}/m")
    if bind == "0.0.0.0":
        ip = lan_ip()
        if ip:
            log(f"on your phone (same Wi-Fi) → http://{ip}:{cfg['port']}/m")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
