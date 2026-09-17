#!/usr/bin/env python3
"""The regression corpus: real posts, the parse they must produce, and one command that replays them all.

Why this exists
---------------
Everything this app draws comes from patterns matched against free text written by people in a hurry, in two
languages, with declensions. That produces a steady trickle of misreads — "знищено" on a warehouse read as a
target shot down, "колом" swallowing a town 700 km away, a government press release closing a live Shahed
track. Every one of those was found by a human looking at the map, which is a detection method that does not
work at three in the morning during a mass attack.

A case here is a real post plus the parse it must produce. `replay` re-parses every one of them and reports
what changed. A fix that quietly breaks another reading stops being something you find on the map next week.

The three states of a case
--------------------------
    pending     captured, nobody has said whether the recorded parse is right. Replayed and reported,
                never enforced — otherwise capturing a bug would freeze it as the expected answer.
    verified    a human confirmed this is the correct reading. Enforced: a difference is a failure.
    known_bad   a human confirmed this reading is WRONG and it is not fixed yet. Enforced in reverse —
                when it starts matching `expect`, the suite says so, because a fixed bug should be promoted
                rather than forgotten.

Usage
-----
    python scripts/corpus.py harvest [--limit N] [--db alerts.sqlite]   # add new posts as pending
    python scripts/corpus.py add --channel C --text "..."               # add one post by hand
    python scripts/corpus.py review                                     # walk the pending cases
    python scripts/corpus.py replay [--all]                             # the regression run
    python scripts/corpus.py stats
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "app"))

import geo  # noqa: E402
import server  # noqa: E402

# Not under tests/: that directory is excluded from the deployed image, and the server serves this file to
# the review panel. A corpus the running app cannot read is a review page that says "nothing to review".
CORPUS = os.path.join(ROOT, "data", "corpus.jsonl")
MAX_TEXT = 1200


# --------------------------------------------------------------------------------------------------
# The projection. This is the contract of the whole file: what counts as "the same reading".
# --------------------------------------------------------------------------------------------------
def project(channel, text):
    """The parse of one post, reduced to what actually matters.

    Deliberately excluded: the human-readable evidence sentences. They are reworded for clarity often, and a
    corpus that fails on wording would be turned off within a week. Deliberately included: the *confidence*
    of a position — "high confidence" on a guess is a safety bug, not a cosmetic one — and the feed tags,
    because the MiG-31K banner and the missile refresh cadence are driven by tags rather than by markers.
    """
    clean = server._clean_post(text)
    tags = sorted(server.tag_feed_text(clean, channel))
    try:
        markers = geo.parse_for_channel(channel, clean) or []
    except Exception as e:                       # a crash is a result too, and a regression if it is new
        return {"error": f"{type(e).__name__}: {e}", "tags": tags}
    out = []
    for m in markers:
        pos = (m.get("evidence") or {}).get("position") or {}
        out.append({
            "status": m.get("status"),
            "type": m.get("type"),
            "place": m.get("place"),
            "oblast": m.get("oblast_uid"),
            "lon": None if m.get("lon") is None else round(float(m["lon"]), 2),
            "lat": None if m.get("lat") is None else round(float(m["lat"]), 2),
            "heading": m.get("heading"),
            "count": m.get("count"),
            "jet": bool(m.get("jet")),
            "likely": m.get("likely"),
            "alt_m": (m.get("alt") or {}).get("m"),
            "alt_state": (m.get("alt") or {}).get("state"),
            "pos_conf": pos.get("confidence"),
            "quad": pos.get("quad"),
        })
    return {"clean": clean, "tags": tags, "relevant": bool(server.is_relevant(clean, tags, channel)),
            "markers": out}


def case_id(channel, text):
    return hashlib.blake2s((channel + "\x00" + text).encode("utf-8"), digest_size=8).hexdigest()


# --------------------------------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------------------------------
def load():
    if not os.path.exists(CORPUS):
        return []
    out = []
    with open(CORPUS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//"):
                out.append(json.loads(line))
    return out


def save(cases):
    os.makedirs(os.path.dirname(CORPUS), exist_ok=True)
    order = {"verified": 0, "known_bad": 1, "pending": 2}
    cases.sort(key=lambda c: (order.get(c.get("state"), 3), c.get("channel", ""), c.get("id", "")))
    tmp = CORPUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, CORPUS)


def add_case(cases, channel, text, ts=None, note=""):
    text = (text or "").strip()[:MAX_TEXT]
    if not text:
        return None
    cid = case_id(channel, text)
    if any(c["id"] == cid for c in cases):
        return None
    case = {"id": cid, "channel": channel, "ts": ts or "", "text": text,
            "state": "pending", "note": note, "expect": project(channel, text)}
    cases.append(case)
    return case


# --------------------------------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------------------------------
def cmd_harvest(args):
    """Take real posts the app has already collected. No network: these are posts it actually saw."""
    db = args.db if os.path.isabs(args.db) else os.path.join(ROOT, args.db)
    if not os.path.exists(db):
        print(f"no database at {db} — run the app once, or pass --db")
        return 1
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT post_id,channel,ts,text FROM feed ORDER BY ts DESC LIMIT ?",
                        (args.limit,)).fetchall()
    conn.close()
    cases = load()
    before = len(cases)
    per_channel = {}
    for _pid, channel, ts, text in rows:
        if args.channel and channel != args.channel:
            continue
        if per_channel.get(channel, 0) >= args.per_channel:
            continue
        if add_case(cases, channel, text, ts):
            per_channel[channel] = per_channel.get(channel, 0) + 1
    save(cases)
    added = len(cases) - before
    print(f"+{added} pending case(s) from {len(rows)} post(s); corpus now {len(cases)}")
    if added:
        print("next: python scripts/corpus.py review")
    return 0


def cmd_add(args):
    cases = load()
    text = args.text
    if text == "-":
        text = sys.stdin.read()
    c = add_case(cases, args.channel, text, note=args.note or "")
    if not c:
        print("already in the corpus (or empty)")
        return 0
    save(cases)
    print(f"added {c['id']} as pending — this is what it parses to today:\n")
    show(c["expect"])
    print("\nnext: python scripts/corpus.py review")
    return 0


def show(p, indent="  "):
    if p.get("error"):
        print(f"{indent}CRASH: {p['error']}")
        return
    print(f"{indent}tags     : {', '.join(p['tags']) or '—'}   relevant: {p['relevant']}")
    if not p["markers"]:
        print(f"{indent}markers  : none")
        return
    for m in p["markers"]:
        bits = [f"status={m['status'] or '—'}", f"type={m['type']}", f"place={m['place']}",
                f"obl={m['oblast']}", f"{m['lon']},{m['lat']}"]
        if m["count"]:
            bits.append(f"×{m['count']}")
        if m["jet"]:
            bits.append("jet")
        if m["heading"] is not None:
            bits.append(f"hdg={m['heading']}")
        if m["likely"]:
            bits.append(f"likely={m['likely']}")
        if m["alt_m"] or m["alt_state"]:
            bits.append(f"alt={m['alt_m']}/{m['alt_state']}")
        if m["quad"]:
            bits.append(f"quad={m['quad']}")
        bits.append(f"conf={m['pos_conf']}")
        print(f"{indent}marker   : " + "  ".join(bits))


def diff(expected, actual):
    """Human-sized differences between two projections."""
    out = []
    if expected.get("error") != actual.get("error"):
        out.append(f"error: {expected.get('error')!r} -> {actual.get('error')!r}")
    if expected.get("tags") != actual.get("tags"):
        out.append(f"tags: {expected.get('tags')} -> {actual.get('tags')}")
    if expected.get("relevant") != actual.get("relevant"):
        out.append(f"relevant: {expected.get('relevant')} -> {actual.get('relevant')}")
    em, am = expected.get("markers") or [], actual.get("markers") or []
    if len(em) != len(am):
        out.append(f"markers: {len(em)} -> {len(am)}")
    for i in range(max(len(em), len(am))):
        e = em[i] if i < len(em) else None
        a = am[i] if i < len(am) else None
        if e == a:
            continue
        if e is None:
            out.append(f"marker[{i}] appeared: {a}")
        elif a is None:
            out.append(f"marker[{i}] vanished: {e}")
        else:
            for k in sorted(set(e) | set(a)):
                if e.get(k) != a.get(k):
                    out.append(f"marker[{i}].{k}: {e.get(k)!r} -> {a.get(k)!r}")
    return out


def cmd_replay(args):
    cases = load()
    if not cases:
        print("corpus is empty — python scripts/corpus.py harvest")
        return 0
    bad, drift, fixed, ok = [], [], [], 0
    for c in cases:
        actual = project(c["channel"], c["text"])
        d = diff(c["expect"], actual)
        state = c.get("state", "pending")
        if state == "verified":
            if d:
                bad.append((c, d))
            else:
                ok += 1
        elif state == "known_bad":
            if d:
                fixed.append((c, d))          # it no longer does the wrong thing: promote it
            else:
                ok += 1
        else:
            if d:
                drift.append((c, d))
    width = 78
    for c, d in bad:
        print("=" * width)
        print(f"BROKEN  {c['id']}  @{c['channel']}   {c.get('note') or ''}")
        print(f"  {c['text'][:200]}")
        for line in d:
            print(f"    {line}")
    for c, d in fixed:
        print("=" * width)
        print(f"FIXED?  {c['id']}  @{c['channel']}  — known_bad case no longer reproduces")
        print(f"  {c['text'][:200]}")
        for line in d:
            print(f"    {line}")
        print("    → re-check it and mark it verified: python scripts/corpus.py review --id " + c["id"])
    if args.all:
        for c, d in drift:
            print("=" * width)
            print(f"drift   {c['id']}  @{c['channel']}  (pending — not enforced)")
            print(f"  {c['text'][:160]}")
            for line in d:
                print(f"    {line}")
    print("=" * width)
    n = len(cases)
    print(f"{n} case(s): {sum(1 for c in cases if c.get('state') == 'verified')} verified, "
          f"{sum(1 for c in cases if c.get('state') == 'known_bad')} known_bad, "
          f"{sum(1 for c in cases if c.get('state', 'pending') == 'pending')} pending")
    print(f"broken: {len(bad)}   known_bad now passing: {len(fixed)}   pending drift: {len(drift)}"
          + ("" if args.all else "  (--all to list)"))
    return 1 if bad else 0


def cmd_review(args):
    cases = load()
    todo = [c for c in cases if (c["id"] == args.id if args.id else c.get("state", "pending") == "pending")]
    if not todo:
        print("nothing to review")
        return 0
    print(f"{len(todo)} case(s). For each: is the reading below CORRECT?\n"
          "  y = yes, enforce it (verified)     n = no, it is wrong (known_bad, records why)\n"
          "  s = skip                           q = save and quit\n")
    changed = 0
    for i, c in enumerate(todo, 1):
        print("=" * 78)
        print(f"[{i}/{len(todo)}] @{c['channel']}  {c.get('ts', '')[:16]}")
        print(f"\n{c['text'][:600]}\n")
        show(project(c["channel"], c["text"]))
        while True:
            a = input("\n  y / n / s / q > ").strip().lower()
            if a in ("y", "n", "s", "q"):
                break
        if a == "q":
            break
        if a == "s":
            continue
        if a == "y":
            c["expect"] = project(c["channel"], c["text"])
            c["state"] = "verified"
            changed += 1
        elif a == "n":
            c["note"] = input("  what is wrong with it? > ").strip()
            c["expect"] = project(c["channel"], c["text"])
            c["state"] = "known_bad"
            changed += 1
    save(cases)
    print(f"\n{changed} case(s) updated → {CORPUS}")
    return 0


def cmd_flags(args):
    """Readings people flagged as wrong from inside the app, turned into corpus cases.

    This is the path that actually gets used: somebody taps a marker on their phone during a raid, says what
    is wrong with it, and the post lands here. No terminal involved at the moment that matters.
    """
    db = args.db if os.path.isabs(args.db) else os.path.join(ROOT, args.db)
    if not os.path.exists(db):
        print(f"no database at {db}")
        return 1
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT id,ts,channel,reason,note,text,done FROM flags ORDER BY id DESC "
                            "LIMIT ?", (args.limit,)).fetchall()
    except sqlite3.OperationalError:
        print("nobody has flagged anything yet")
        conn.close()
        return 0
    open_rows = [r for r in rows if not r[6] or args.all]
    if not open_rows:
        print("no open flags")
        conn.close()
        return 0
    if not args.import_:
        for _id, ts, ch, reason, note, text, done in open_rows:
            print("=" * 78)
            print(f"#{_id}  {reason}  @{ch}  {ts[:16]}{'  (done)' if done else ''}")
            if note:
                print(f'  "{note}"')
            print(f"  {(text or '')[:200]}")
        print("=" * 78)
        print(f"{len(open_rows)} open flag(s). --import turns them into pending corpus cases.")
        conn.close()
        return 0
    cases = load()
    added, ids = 0, []
    for _id, ts, ch, reason, note, text, _done in open_rows:
        if not text:
            continue
        c = add_case(cases, ch, text, ts, note=f"flagged: {reason}" + (f" — {note}" if note else ""))
        ids.append(_id)
        if c:
            added += 1
    save(cases)
    if ids and not args.keep:
        conn.executemany("UPDATE flags SET done=1 WHERE id=?", [(i,) for i in ids])
        conn.commit()
    conn.close()
    print(f"+{added} pending case(s) from {len(ids)} flag(s)")
    if added:
        print("next: python scripts/corpus.py review")
    return 0


def cmd_pull(args):
    """Merge verdicts given on /admin back into the corpus file.

    The corpus ships inside the deployed image, so a machine in Amsterdam cannot write to it. The reviewing
    happens where the reviewer is — a browser, a phone — and the answers come home here.
    """
    db = args.db if os.path.isabs(args.db) else os.path.join(ROOT, args.db)
    if not os.path.exists(db):
        print(f"no database at {db} — copy it from the server, or pass --db")
        return 1
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT case_id,state,note FROM corpus_reviews").fetchall()
    except sqlite3.OperationalError:
        print("no reviews in that database yet")
        conn.close()
        return 0
    conn.close()
    verdicts = {r[0]: (r[1], r[2] or "") for r in rows}
    cases = load()
    applied, missing, changed = 0, 0, []
    for c in cases:
        v = verdicts.get(c["id"])
        if not v or v[0] == c.get("state"):
            continue
        state, note = v
        # a verdict is about the reading somebody SAW, so the expectation is re-recorded from today's code
        c["expect"] = project(c["channel"], c["text"])
        c["state"] = state
        if note:
            c["note"] = note
        applied += 1
        changed.append((state, c["channel"], " ".join((note or c["text"]).split())[:60]))
    missing = len([k for k in verdicts if not any(c["id"] == k for c in cases)])
    save(cases)
    for state, ch, what in changed[:20]:
        print(f"  {state:10} @{ch:22} {what}")
    print(f"{applied} verdict(s) applied" + (f", {missing} for cases not in this corpus" if missing else ""))
    if applied:
        print("next: python scripts/corpus.py replay")
    return 0


def cmd_stats(args):
    cases = load()
    by_state, by_channel = {}, {}
    for c in cases:
        by_state[c.get("state", "pending")] = by_state.get(c.get("state", "pending"), 0) + 1
        by_channel[c["channel"]] = by_channel.get(c["channel"], 0) + 1
    print(f"{len(cases)} case(s) in {CORPUS}")
    for k in ("verified", "known_bad", "pending"):
        if by_state.get(k):
            print(f"  {k:10} {by_state[k]}")
    print("\nby channel:")
    for ch, n in sorted(by_channel.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4}  @{ch}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("harvest", help="add real posts from the app's own feed as pending cases")
    h.add_argument("--db", default="alerts.sqlite")
    h.add_argument("--limit", type=int, default=400, help="how many recent posts to look at")
    h.add_argument("--per-channel", type=int, default=25, help="cap per channel, so one loud channel cannot flood it")
    h.add_argument("--channel", default=None)
    h.set_defaults(fn=cmd_harvest)

    a = sub.add_parser("add", help="add one post by hand (use - to read it from stdin)")
    a.add_argument("--channel", required=True)
    a.add_argument("--text", required=True)
    a.add_argument("--note", default="")
    a.set_defaults(fn=cmd_add)

    r = sub.add_parser("review", help="walk the pending cases and mark them")
    r.add_argument("--id", default=None)
    r.set_defaults(fn=cmd_review)

    p = sub.add_parser("replay", help="re-parse every case and report what changed")
    p.add_argument("--all", action="store_true", help="also list pending drift")
    p.set_defaults(fn=cmd_replay)

    f = sub.add_parser("flags", help="readings people flagged as wrong from inside the app")
    f.add_argument("--db", default="alerts.sqlite")
    f.add_argument("--limit", type=int, default=200)
    f.add_argument("--import", dest="import_", action="store_true", help="turn them into pending cases")
    f.add_argument("--keep", action="store_true", help="do not mark them handled after importing")
    f.add_argument("--all", action="store_true", help="include ones already handled")
    f.set_defaults(fn=cmd_flags)

    pl = sub.add_parser("pull", help="merge verdicts given on /admin back into the corpus")
    pl.add_argument("--db", default="alerts.sqlite")
    pl.set_defaults(fn=cmd_pull)

    s = sub.add_parser("stats", help="what is in the corpus")
    s.set_defaults(fn=cmd_stats)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
