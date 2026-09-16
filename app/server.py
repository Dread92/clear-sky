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
    "telegram_channels": ["kpszsu", "povitryanatrivogaaa", "war_monitor", "monitor_ukr", "kyivoda", "KyivCityOfficial", "kyiv_airdef",
                          "UkraineAlarmSignal", "cherkasy_alerts", "cherkasy_monitor", "poltavskaODA", "zhytomyrskaODA",
                          "chernihiv_alert", "Zhytomyr_alert", "sumy_alert", "sumy_alerts", "eRadarrua", "kyiv_times_official",
                          "kharkiv_alert", "odesa_alert", "dnipro_alert", "lviv_alert", "zaporizhzhia_alert", "mykolaiv_alert", "kherson_alert", "vinnytsia_alert", "khmelnytskyi_alert", "lutsk_alert", "ternopil_alert", "ivanofrankivsk_alert", "chernivtsi_alert", "uzhhorod_alert", "kropyvnytskyi_alert", "kryvyirih_alert", "kremenchuk_alert", "cherkasy_alert"],
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
    ("cruise_missiles", re.compile(r"крилат|калібр|х-?101|х-?555|х-?59|х-?69|х-?22|х-?32|ракет", re.I)),
    ("mig31k_departure", re.compile(r"міг-?31|миг-?31|mig-?31", re.I)),
    ("strategic_aircraft_activity", re.compile(r"ту-?95|ту-?160|ту-?22|стратегічн", re.I)),
    ("tactic_aircraft_activity", re.compile(r"тактичн|су-?34|су-?35|су-?25|су-?24", re.I)),
    ("guided_aerial_bombs", re.compile(r"каб|керован.*бомб", re.I)),
    ("air_defense", re.compile(r"ппо|протиповітрян|збит|знищен", re.I)),
    ("clear", re.compile(r"відбій|загроза минула|небезпека минула|\bчисто\b|посадк|приземл", re.I)),
    ("impact", re.compile(r"вибух|приліт|влучанн|уражен", re.I)),
    ("alert", re.compile(r"тривог|укритт|небезпек|загроз|сирен", re.I)),
]
THREAT_TAGS = {n for n, _ in FEED_TAGS}


LIVE_TAGS = {"drones", "ballistic_missiles", "cruise_missiles", "mig31k_departure", "strategic_aircraft_activity", "tactic_aircraft_activity", "guided_aerial_bombs", "clear"}
NEWS_CHANNELS = {"kyiv_times_official", "eRadarrua"}   # big mixed channels: only short live-threat posts are kept


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


# nightly / daily *assessments* ("Загальна оцінка загроз на ніч…", "#обстановка", "може відбутись у будь-який момент") describe
# what MIGHT happen — they must never light the ballistic / MiG strip or place a marker. They keep only 'forecast' + 'alert'.
FORECAST_RX = re.compile(r"оцінка\s+загроз|загальна\s+оцінка|прогноз\s+(?:на|загроз)|#обстановка|на\s+ніч\s+\d|може\s+відбутись\s+у\s+будь-який\s+момент|впродовж\s+ночі\s+(?:ймовірн|можлив)|(?:ймовірн|можлив)\w*\s+(?:додаткові\s+)?запуск", re.I)


def tag_feed_text(text, channel=None):
    tags = [name for name, rx in FEED_TAGS if rx.search(text)]
    if FORECAST_RX.search(text) and not re.search(r"\bпуск\b|зафіксовано\s+пуск|швидкісн\w*\s+ціл|зліт\s+міг|злетів|у\s+повітрі\s+міг", text, re.I):
        return [t for t in tags if t == "alert"] + ["forecast"]
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
    if channel and geo and channel in geo.OFFICIAL_PARSERS:
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


def build_id():
    """A short id that changes whenever the front end or the service changes. An open page compares it with
    the one it loaded and offers a reload — that is how someone running for three days on an old build finds out."""
    h = 0
    for rel in ("static/kyiv.html", "static/i18n.js", "static/sw.js", "app/server.py", "app/geo.py"):
        p = os.path.join(ROOT, rel)
        try:
            st = os.stat(p)
            h = (h * 1000003 + int(st.st_mtime) ^ st.st_size) & 0xFFFFFFFF
        except OSError:
            pass
    return format(h, "08x")


BUILD = None    # filled at startup


def _clean_post(text):
    """What the reader sees: the warning without the channel's ad tail ("Купуємо контент | ❤️")."""
    if not _tr:
        return text
    try:
        return _tr.strip_promo(text)
    except Exception:
        return text


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


def http_get(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "ua-alerts-local/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


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
        c.execute("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS push_subs(endpoint TEXT PRIMARY KEY, sub TEXT, home TEXT, created TEXT, last_ok TEXT)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_alerts_started ON alerts(started_at)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_feed_ts ON feed(ts)")
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
                              (sub["endpoint"], json.dumps(sub), json.dumps(home or {}), now_iso(), now_iso()))
            self.conn.commit()

    def push_remove(self, endpoint):
        with self.lock:
            self.conn.execute("DELETE FROM push_subs WHERE endpoint=?", (endpoint,))
            self.conn.commit()

    def push_all(self):
        with self.lock:
            rows = self.conn.execute("SELECT endpoint,sub,home FROM push_subs").fetchall()
        return [{"endpoint": r[0], "sub": json.loads(r[1]), "home": json.loads(r[2] or "{}")} for r in rows]

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

    retry_en = set()   # post_ids whose English text came from the offline fallback

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
                if p.get("en_fallback"):
                    self.retry_en.add(p["post_id"])
                cur = self.conn.execute("INSERT OR IGNORE INTO feed(post_id,channel,ts,text,tags,text_en) VALUES(?,?,?,?,?,?)",
                                        (p["post_id"], p["channel"], p["ts"], p["text"], json.dumps(p["tags"]), p.get("text_en")))
                if cur.rowcount:
                    new.append(p)
            self.conn.commit()
        return new

    def feed_count(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM feed").fetchone()[0]

    def feed_since(self, minutes, limit=200, translate=True):
        since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        with self.lock:
            rows = self.conn.execute("SELECT post_id,channel,ts,text,tags,text_en FROM feed WHERE ts>=? ORDER BY ts DESC LIMIT ?", (since, limit)).fetchall()
        return [{"post_id": r[0], "channel": r[1], "ts": r[2], "text": _clean_post(r[3]), "tags": json.loads(r[4]), "text_en": r[5] or (to_en(r[3]) if translate else None)} for r in rows]

    def feed(self, limit=80):
        with self.lock:
            rows = self.conn.execute("SELECT post_id,channel,ts,text,tags,text_en FROM feed ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [{"post_id": r[0], "channel": r[1], "ts": r[2], "text": _clean_post(r[3]), "tags": json.loads(r[4]), "text_en": r[5] or to_en(r[3])} for r in rows]

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
                elif m.get("live"):
                    if ctx and pts and (pts - ctx[0]).total_seconds() <= 8 * 60 and m.get("heading") is None:
                        d = math.hypot((m["lon"] - ctx[1]["lon"]) * 70.7, (m["lat"] - ctx[1]["lat"]) * 111)
                        if d >= 0.4:
                            m["heading"] = round(geo.bearing(ctx[1]["lon"], ctx[1]["lat"], m["lon"], m["lat"]))
                            ev = dict(m.get("evidence") or {}); ev["heading"] = {"matched": f"{ctx[1].get('place')} → {m.get('place')}", "method": "bearing between the two latest reports of this live-tracking channel", "confidence": "medium"}
                            m["evidence"] = ev
                if m.get("lon") is not None and not m.get("status"):
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
        out = self._chain_and_prune(out, ttl)
        out.sort(key=lambda m: m["ts"], reverse=True)
        if len(self._marker_cache) > 2000:
            self._marker_cache.clear()
            self._ev_cache.clear()
        return out

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
            by_day.setdefault(d, {"alerts": 0, "minutes": 0, "impacts": 0, "down": 0, "drone_posts": 0, "missile_posts": 0})
            by_day[d]["impacts" if m["status"] == "impact" else "down"] += 1
            ou = m.get("oblast_uid") or "?"
            by_obl.setdefault(ou, {"impacts": 0, "down": 0})
            by_obl[ou]["impacts" if m["status"] == "impact" else "down"] += 1
        # 3. launches: Air Force morning summaries ("противник атакував N ударними БпЛА ... та M ракетами") give the
        #    official count of what was launched over Ukraine; per-day max (the summary is sometimes re-posted / corrected).
        #    Reports: every monitoring post tagged drones / missiles, all of Ukraine — a volume of reporting, not a count of targets.
        empty = {"alerts": 0, "minutes": 0, "impacts": 0, "down": 0, "drone_posts": 0, "missile_posts": 0,
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
            cur = day_max.get(d, {"drones": 0, "missiles": 0, "down": 0, "age": 0})
            day_max[d] = {"drones": max(cur["drones"], summ["drones"]), "missiles": max(cur["missiles"], summ["missiles"]),
                          "down": max(cur["down"], summ["down"]), "age": (now - pts).total_seconds() / 86400}
        for d, v in day_max.items():
            for w, acc in windows.items():
                if v["age"] <= w:
                    acc["launched_drones"] += v["drones"]; acc["launched_missiles"] += v["missiles"]; acc["af_down"] += v["down"]
        # Explosions and confirmed shoot-downs come from parsed posts, and that parsing covers at most 96 h —
        # so they are reported for 24 h and 72 h only. Reporting them "per 30 days" would be a number that is
        # simply missing most of its days.
        imp_windows = {1: {"impacts": 0, "down": 0}, 3: {"impacts": 0, "down": 0}}
        for m in imp:
            age_d = (now - (parse_iso(m["ts"]) or now)).total_seconds() / 86400
            for w, acc in imp_windows.items():
                if age_d <= w:
                    acc["impacts" if m["status"] == "impact" else "down"] += 1
        daysl = [(since + timedelta(days=i)).astimezone(kyiv_tz).strftime("%Y-%m-%d") for i in range(days + 1)]
        series = [{"day": d, **by_day.get(d, empty)} for d in daysl]
        out = {"days": days, "since": since.isoformat(), "series": series, "hours": hours, "kyiv_minutes": round(tot_min),
               "kyiv_alerts": sum(1 for a in al if a["oblast_uid"] == "31"), "longest": longest, "by_oblast": by_obl,
               "impacts_total": sum(1 for m in imp if m["status"] == "impact"), "down_total": sum(1 for m in imp if m["status"] == "down"),
               "impact_window_h": min(days * 24, 96), "windows": {str(k): v for k, v in windows.items()},
               "imp_windows": {str(k): v for k, v in imp_windows.items()}, "imp_max_h": min(days * 24, 96)}
        self._stats_res[days] = (time.time(), out)
        return out

    # -- missile mode -----------------------------------------------------
    # True while a ballistic / cruise-missile (or MiG-31K) threat is open on a watched region, or a post
    # tagged ballistic/cruise came in during the last 10 min → clients poll every 5 s and Telegram every 10 s.
    _missile_res = (0, False)

    def missile_active(self):
        t0, v = self._missile_res
        if time.time() - t0 < 4:
            return v
        v = self._missile_active()
        self._missile_res = (time.time(), v)
        return v

    def _missile_active(self):
        fav = set(self.cfg.get("favourites") or [])
        kinds = {"ballistic_missiles", "cruise_missiles", "mig31k_departure"}
        with self.lock:
            for a in self.active.values():
                if fav and a.get("oblast_uid") not in fav:
                    continue
                for t in a.get("threats") or []:
                    if (t.get("threat_type") if isinstance(t, dict) else t) in kinds:
                        return True
        for p in self.store.feed_since(10, limit=60, translate=False):
            if kinds & set(p.get("tags") or []):
                return True
        return False

    # -- impact / shoot-down history (24/48/72 h) ---------------------------
    # "impact / explosion" and confirmed "shot down" outcome markers, parsed from every post of the window (cached per post,
    # result cached 60 s per window). Same-place relays from other channels within 5 min are merged.
    def impacts(self, hours):
        hours = max(1, min(int(hours), 96))
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
                          if m.get("status") in ("impact", "down") and m.get("lon") is not None and m.get("place") != "область"
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
            for a in self.active.values():
                if a["location_type"] == "oblast" or a["oblast_uid"] in ("31", "14"):
                    obl_status[a["oblast_uid"]] = "A"
        ms = sorted(ms, key=lambda m: m["ts"])
        tracks = [m for m in ms if not m.get("status")]
        for m in tracks:
            m["superseded_by"] = None
            m["history"] = []
        def km(a, b):
            return math.hypot((a["lon"] - b["lon"]) * 70.7, (a["lat"] - b["lat"]) * 111)
        def fam(m):
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
                if d > vmax * dt + 25:
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
                pm["superseded_by"] = m["id"]
                m["history"] = pm["history"] + [{"id": pm["id"], "lon": pm["lon"], "lat": pm["lat"], "ts": pm["ts"], "channel": pm["channel"], "place": pm.get("place")}]
        keep = []
        for m in ms:
            if m.get("superseded_by"):
                continue
            age = (now - parse_iso(m["ts"])).total_seconds() / 60 if parse_iso(m["ts"]) else 0
            if m.get("status"):
                if age <= 25:
                    keep.append(m)
                continue
            if age > stale:
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
                    m = dict(b if b["source"] in ("alerts_in_ua", "ukrainealarm") else a)
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
            time.sleep(interval)

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


class UkraineAlarm(threading.Thread):
    NAME = "ukrainealarm"
    TYPE_MAP = {"AIR": "air_raid", "ARTILLERY": "artillery_shelling", "URBAN_FIGHTS": "urban_fights",
                "CHEMICAL": "chemical", "NUCLEAR": "nuclear", "INFO": "info"}
    LEVEL_MAP = {"State": "oblast", "District": "raion", "Community": "hromada"}

    def __init__(self, state, cfg):
        super().__init__(daemon=True)
        self.state, self.cfg = state, cfg
        self.key = cfg["ukrainealarm_key"]
        self.last_action = None
        self.primary = not cfg.get("alerts_in_ua_token")

    def run(self):
        interval = max(10, int(self.cfg.get("poll_alerts_seconds", 15)))
        while True:
            try:
                self.poll()
            except Exception as e:
                self.state.set_source(self.NAME, False, error=str(e)[:200])
                log("ukrainealarm error:", e)
            time.sleep(interval)

    def poll(self):
        h = {"Authorization": self.key, "Accept": "application/json"}
        st, _, body = http_get("https://api.ukrainealarm.com/api/v3/alerts/status", h)
        action = json.loads(body.decode()).get("lastActionIndex")
        if action == self.last_action:
            self.state.set_source(self.NAME, True, note=f"unchanged ({action})")
            return
        self.last_action = action
        st, _, body = http_get("https://api.ukrainealarm.com/api/v3/alerts", h)
        regions = json.loads(body.decode())
        alerts = []
        for r in regions:
            ltype = self.LEVEL_MAP.get(r.get("regionType"), "unknown")
            name = r.get("regionName") or ""
            ouid = norm_uk(name) if ltype == "oblast" else None
            for a in r.get("activeAlerts") or []:
                atype = self.TYPE_MAP.get(a.get("type"), "air_raid")
                alerts.append({
                    "key": f"ua:{r.get('regionId')}:{atype}",
                    "source": self.NAME,
                    "location_uid": f"ua-{r.get('regionId')}",
                    "location_title": name, "location_title_en": None,
                    "location_type": ltype,
                    "oblast_uid": ouid or (self._oblast_of(r) or "?"),
                    "alert_type": atype, "alert_level": None,
                    "started_at": a.get("lastUpdate"), "finished_at": None, "notes": None, "threats": [],
                })
        self.state.set_source(self.NAME, True, count=len(alerts))
        if self.primary:
            self.state.apply_snapshot(self.NAME, alerts, {"oblast", "raion", "hromada"})

    @staticmethod
    def _oblast_of(r):
        # /alerts doesn't carry parent info; best effort from name
        return norm_uk(r.get("regionName"))


class Ubilling(threading.Thread):
    """Keyless mirror (oblast-level booleans). Used only when no keyed source is configured."""
    NAME = "ubilling_mirror"

    def __init__(self, state, cfg, primary):
        super().__init__(daemon=True)
        self.state, self.cfg, self.primary = state, cfg, primary

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
                        if not d or d.year < 2022:  # mirror often reports epoch 0 — start time unknown
                            started = now_iso()
                        uk, en = NAME_BY_UID[uid]
                        alerts.append({"key": f"ub:{uid}", "source": self.NAME, "location_uid": uid,
                                       "location_title": uk, "location_title_en": en, "location_type": "oblast",
                                       "oblast_uid": uid, "alert_type": "air_raid", "alert_level": None,
                                       "started_at": started, "finished_at": None, "notes": "keyless mirror", "threats": []})
                self.state.set_source(self.NAME, True, count=len(alerts), cached_at=data.get("cachedat"))
                if self.primary:
                    self.state.apply_snapshot(self.NAME, alerts, {"oblast"})
            except Exception as e:
                self.state.set_source(self.NAME, False, error=str(e)[:200])
                log("ubilling error:", e)
            time.sleep(interval)


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
        self.channels = cfg.get("telegram_channels") or []
        self.official = OfficialAlerts(state)
        self.seen_channels = set()

    def run(self):
        interval = max(30, int(self.cfg.get("poll_telegram_seconds", 45)))
        fast = max(8, int(self.cfg.get("poll_telegram_missile_seconds", 10)))
        import concurrent.futures as _cf
        pool = _cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="tg")
        def one(ch):
            try:
                self.poll(ch)
            except Exception as e:
                self.state.set_source(f"tg:{ch}", False, error=str(e)[:200])
                log(f"telegram {ch} error:", e)
        while True:
            missile = self.state.missile_active()
            list(pool.map(one, self.channels))   # 4 channels at a time, ~1 round trip each
            time.sleep(fast if missile else interval)

    def poll(self, ch):
        st, _, body = http_get(f"https://t.me/s/{ch}", {"Accept-Language": "uk,en"})
        page = body.decode("utf-8", "replace")
        posts = []
        for part in page.split('<div class="tgme_widget_message_wrap')[1:]:
            mid = re.search(r'data-post="([^"]+)"', part)
            mtxt = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', part, re.S)
            mdt = re.search(r'<time[^>]*datetime="([^"]+)"', part)
            if not (mid and mtxt and mdt):
                continue
            post_id, raw, dt = mid.group(1), mtxt.group(1), mdt.group(1)
            text = html.unescape(self.STRIP_RE.sub("", self.TAG_RE.sub("\n", raw))).strip()
            text = re.sub(r"[ \t]+", " ", text)
            if not text:
                continue
            if self.state.store.has_post(post_id):
                continue          # already stored (and translated) on an earlier poll
            tags = tag_feed_text(text, ch)
            if not is_relevant(text, tags, ch):
                self.state.store.mark_seen(post_id)
                continue          # news, fundraising, culture… — not an air-threat post, not stored
            pts = parse_iso(dt)
            if ch not in self.seen_channels and pts and (datetime.now(timezone.utc) - pts) > timedelta(hours=2) and _tr:
                en, fb = _tr.translate_offline(text), True     # cold start: old posts get the fast offline glossary, re-translated later
            else:
                en, fb = to_en(text), bool(_tr) and not _tr.LAST_OK[0]
            posts.append({"post_id": post_id, "channel": ch, "ts": dt, "text": text, "tags": tags, "text_en": en, "en_fallback": fb})
        new = self.state.store.add_feed(posts)
        self.state.set_source(f"tg:{ch}", True, count=len(posts), new=len(new))
        if ch == "eRadarrua" and geo:
            for p in sorted(posts, key=lambda p: p["ts"], reverse=True):
                summ = geo.parse_eradar_summary(p["text"]) if "◦" in p["text"] else None
                if summ:
                    self.state.set_eradar(p["ts"], summ)
                    break
        for p in sorted(new, key=lambda p: p["ts"]):
            self.state.publish({"kind": "feed", "ts": p["ts"], "post": p})
        if geo and ch in geo.OFFICIAL_PARSERS:
            self.official.ingest(ch, sorted(new, key=lambda p: p["ts"]), initial=ch not in self.seen_channels)
        self.seen_channels.add(ch)


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
        ou = a.get("oblast_uid"); lt = a.get("location_type")
        title = body = None; tag = "alert"
        thr = ev.get("threat") or {}
        if kind == "threat" and thr.get("threat_type") in ("mig31k_departure", "ballistic_missiles") and ou in ("31", "14"):
            title = "✈ MiG-31K airborne — ballistic risk" if thr["threat_type"] == "mig31k_departure" else "🚀 Ballistic threat — shelter now"
            body = (thr.get("source_message") or "")[:120]; tag = "threat"
        elif ou == "31" and kind in ("start", "end"):
            title = "🔴 Kyiv — air raid alert" if kind == "start" else "🟢 Kyiv — all clear"
            body = ("%s level" % (a.get("alert_level") or "red")) if kind == "start" else "Alert lifted"
        elif ou == "14" and lt == "oblast" and kind in ("start", "end"):
            title = "🟡 Kyiv oblast — alert" if kind == "start" else "🟢 Kyiv oblast — all clear"
            body = (a.get("alert_level") or "") + " level" if kind == "start" else ""
        elif ou == "14" and lt == "raion" and kind in ("start", "end"):
            t = (a.get("location_title") or "").lower(); slug = next((sl for st, sl in RAION_STEMS if st in t), None)
            if not slug:
                return
            title = ("🔴 " if a.get("alert_level") == "red" else "🟡 ") + a["location_title"] + (" — alert" if kind == "start" else " — all clear"); body = ""
            self._queue(title, body, tag, raion=slug); return
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
            live = [m for m in state.markers() if not m.get("status") and not m.get("stale") and not m.get("endedBy")]
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
                if now - PROX_LAST.get(ep, 0) < 120:
                    continue
                near = []
                for m in live:
                    if m["id"] in seen:
                        continue
                    d = haversine_km(home["lat"], home["lon"], m["lat"], m["lon"])
                    if d <= radius:
                        near.append((d, m))
                if not near:
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
                pusher.queue_direct(
                    f"\u26a0 {kind}{extra} {round(d)} km from you{more}",
                    f"Reported near {place}{hdg} at {fmt_kyiv(m['ts'])} \u00b7 position from a public post, not radar",
                    "near", ep)
        except Exception as e:
            log("proximity error:", e)


THREAT_EN = {"ballistic_missiles": "Ballistic missile", "cruise_missiles": "Cruise missile", "unspecified_missiles": "Missile",
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

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
            return self._json({"ok": True})
        if u.path.startswith("/static/logo") or u.path == "/favicon.ico":
            return self._file(u.path.split("/")[-1] if u.path != "/favicon.ico" else "logo-64.png", "image/png")
        auth = self._authorized(u, q)
        if not auth:
            body = b"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><body style='font-family:system-ui;background:#0b0e13;color:#e6e9ef;padding:40px;text-align:center'><img src='/static/logo-192.png' style='width:96px;border-radius:18px'><h2>Clear Sky</h2><form><input name=key placeholder='access key' style='padding:10px;font-size:16px;border-radius:8px;border:1px solid #333'> <button style='padding:10px 14px;border-radius:8px'>Enter</button></form></body>"
            self.send_response(401); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if auth == "set":
            self.send_response(302); self.send_header("Location", u.path or "/"); self.send_header("Set-Cookie", f"uak={q['key'][0]}; Path=/; Max-Age=31536000; SameSite=Lax"); self.end_headers(); return
        try:
            if u.path in ("/", "/m", "/k", "/kyiv"):
                return self._file("kyiv.html", "text/html; charset=utf-8")
            if u.path in ("/desktop", "/index.html", "/dash"):
                return self._file("index.html", "text/html; charset=utf-8")
            if u.path in ("/ua", "/mobile", "/mobile.html"):
                return self._file("mobile.html", "text/html; charset=utf-8")
            if u.path == "/manifest.json":
                return self._file("manifest.json", "application/manifest+json")
            if u.path == "/api/places":
                oq = q.get("oblast", ["14,31"])[0]
                obl = None if oq == "all" else set(oq.split(","))
                pl = [{"name": n.split(" (")[0], "lon": v[0], "lat": v[1], "oblast_uid": v[2]} for n, v in (geo.PLACES.items() if geo else []) if obl is None or v[2] in obl]
                return self._json({"places": pl})
            if u.path == "/api/markers":
                return self._json({"now": now_iso(), "ttl_minutes": int(st.cfg.get("marker_ttl_minutes", 45)), "stale_minutes": int(st.cfg.get("track_stale_minutes", 5)), "markers": st.markers()})
            if u.path == "/api/version":
                with st.cond:
                    seq = st.seq
                return self._json({"v": f"{seq}-{st.store.feed_count()}", "now": now_iso(), "missile": st.missile_active(), "build": BUILD})
            if u.path == "/api/stats":
                return self._json(st.stats(int(q.get("days", ["14"])[0])))
            if u.path == "/api/impacts":
                hours = int(q.get("hours", ["24"])[0])
                return self._json({"now": now_iso(), "hours": hours, "impacts": st.impacts(hours)})
            if u.path == "/api/state":
                return self._json(st.snapshot())
            if u.path == "/api/feed":
                return self._json({"feed": st.store.feed(int(q.get("limit", ["80"])[0]))})
            if u.path == "/api/history":
                hours = int(q.get("hours", ["24"])[0])
                obl = q.get("oblast", [None])[0]
                return self._json({"alerts": st.store.history(hours, obl.split(",") if obl else None)})
            if u.path == "/api/events_log":
                return self._json({"events": st.store.events(int(q.get("limit", ["200"])[0]))})
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
                return self._file("logo-64.png", "image/png")
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
        if ctype is None:
            ctype = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8", "png": "image/png", "svg": "image/svg+xml", "json": "application/json", "html": "text/html; charset=utf-8", "md": "text/markdown"}.get(p.rsplit(".", 1)[-1], "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
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
        try:
            self.wfile.write(b"event: hello\ndata: {}\n\n")
            self.wfile.flush()
            last_beat = time.time()
            while True:
                with self.state.cond:
                    self.state.cond.wait(timeout=15)
                    items, q[:] = list(q), []
                for ev in items:
                    self.wfile.write(f"event: {ev['kind']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                if time.time() - last_beat > 20:
                    self.wfile.write(b": ping\n\n")
                    last_beat = time.time()
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
    state.pusher = Pusher(store)
    log("push notifications:", "enabled (VAPID key ready)" if state.pusher.enabled else "disabled — pip install cryptography to enable")
    Handler.state = state
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
        if cfg.get("ukrainealarm_key"):
            UkraineAlarm(state, cfg).start()
            log("source: ukrainealarm.com" + ("" if keyed else " (primary)")); keyed = True
        if cfg.get("use_ubilling_fallback", True):
            Ubilling(state, cfg, primary=not keyed).start()
            log("source: ubilling keyless mirror" + (" (primary — oblast level only, add a token for full detail)" if not keyed else " (cross-check)"))
    if cfg.get("telegram_channels"):
        Telegram(state, cfg).start()
        log("feed:", ", ".join("t.me/" + c for c in cfg["telegram_channels"]))

    bind = cfg.get("bind", "127.0.0.1")
    # re-translate the last 12 h of posts with the current translator in the background (older DBs used the offline glossary)
    def _retranslate():
        try:
            rows = store.feed_since(12 * 60)
            for p in rows[::-1]:
                en = to_en(p["text"])
                if en and en != p.get("text_en"):
                    with store.lock:
                        store.conn.execute("UPDATE feed SET text_en=? WHERE post_id=?", (en, p["post_id"]))
                        store.conn.commit()
                time.sleep(0.25)
        except Exception as e:
            log("retranslate:", e)
    threading.Thread(target=_retranslate, daemon=True).start()

    def _retry_loop():   # posts translated with the offline fallback get another go at the online translator
        while True:
            time.sleep(90)
            try:
                ids = list(store.retry_en)[:20]
                for pid in ids:
                    with store.lock:
                        row = store.conn.execute("SELECT text FROM feed WHERE post_id=?", (pid,)).fetchone()
                    if not row:
                        store.retry_en.discard(pid); continue
                    en = to_en(row[0])
                    if _tr and _tr.LAST_OK[0]:
                        with store.lock:
                            store.conn.execute("UPDATE feed SET text_en=? WHERE post_id=?", (en, pid)); store.conn.commit()
                        store.retry_en.discard(pid)
            except Exception as e:
                log("retry translate:", e)
    threading.Thread(target=_retry_loop, daemon=True).start()

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
