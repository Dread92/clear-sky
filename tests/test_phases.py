"""Where a threat is in its own life, read only from what the post actually said.

A Tu-95 being loaded, a Tu-95 in the air, and a Tu-95 on the launch line are three different nights for the
person reading this. The channels name those stages explicitly, so the app can show them — but only when the
wording carries one. The two ways to get this wrong are both worse than showing nothing: reading a stage from
a weapon that has no such stage (a Shahed does not release a bomb), and hardening a hedge into a fact (a
"probable launch" is not a launch — somebody decides whether to go to a shelter on that difference).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import geo  # noqa: E402


# ── the stages the channels actually write ─────────────────────────────────────────────────────────────
def test_each_stage_is_read_from_its_own_wording():
    cases = [
        ("strategic_aircraft_activity", "Зафіксовано перебазування Ту-95МС на аеродром Оленья", "redeploy"),
        ("strategic_aircraft_activity", "Ту-95МС у повітрі", "takeoff"),
        ("cruise_missiles", "Борти вийшли на рубіж пуску", "line"),
        ("cruise_missiles", "Носії на рубежі пуску", "line"),
        ("cruise_missiles", "Пуски крилатих ракет з акваторії Каспійського моря", "launch"),
        ("cruise_missiles", "Ракети увійшли в повітряний простір України", "entering"),
        ("drones", "Пуск шахедів з Приморсько-Ахтарська", "launch"),
        ("drones", "Група шахедів перетнула державний кордон", "entering"),
        ("guided_aerial_bombs", "Скид КАБів по Куп'янську", "drop"),
        ("guided_aerial_bombs", "Тактична авіація відходить від рубежу", "exit"),
        ("tactic_aircraft_activity", "Тактична авіація в повітрі, злетіли з Міллерово", "takeoff"),
        ("mig31k_departure", "Зліт МіГ-31К з аеродрому Саваслейка", "takeoff"),
        ("ballistic_missiles", "Фіксуємо пуск балістики з Брянської області", "launch"),
    ]
    for mtype, text, want in cases:
        assert geo.detect_phase(text, mtype) == want, f"{text!r} read as {geo.detect_phase(text, mtype)!r}"


def test_a_post_that_names_no_stage_gets_none():
    """Most posts are a type and a direction. Inventing a stage for those would make the stage meaningless."""
    for mtype, text in [
        ("drones", "Шахеди на Чернігівщині, курсом на захід"),
        ("ballistic_missiles", "Балістика на Київ!"),
        ("guided_aerial_bombs", "КАБи на Сумщину"),
        ("cruise_missiles", "Ракета в напрямку Кропивницького"),
    ]:
        assert geo.detect_phase(text, mtype) is None, f"{text!r} invented {geo.detect_phase(text, mtype)!r}"


def test_a_direction_is_not_a_release():
    """"КАБи на Сумщину" says where they are aimed. "Скид" says one left the aircraft. Reading the first as
    the second tells a town a bomb is already falling on it."""
    assert geo.detect_phase("КАБи на Сумщину", "guided_aerial_bombs") is None
    assert geo.detect_phase("КАБи по Костянтинівці", "guided_aerial_bombs") is None
    assert geo.detect_phase("Скид КАБ по Костянтинівці", "guided_aerial_bombs") == "drop"


# ── the two failures that matter ───────────────────────────────────────────────────────────────────────
def test_a_stage_is_never_borrowed_from_another_weapon():
    assert geo.detect_phase("Скид КАБ", "drones") is None
    assert geo.detect_phase("Шахеди відходять у бік Чернігова", "drones") is None
    assert geo.detect_phase("Ракети увійшли в повітряний простір", "guided_aerial_bombs") is None
    assert geo.detect_phase("Перебазування", "drones") is None


def test_an_expected_launch_is_never_reported_as_a_launch():
    """The single most dangerous confusion in the whole ladder."""
    for mtype, text in [
        ("ballistic_missiles", "Активність носіїв балістики, ймовірний пуск"),
        ("cruise_missiles", "Загроза пуску крилатих ракет"),
        ("cruise_missiles", "Можливий пуск найближчим часом"),
        ("drones", "Очікуємо пуски шахедів"),
        ("mig31k_departure", "Ризик пуску Кинджалів"),
    ]:
        assert geo.detect_phase(text, mtype) == "prep", f"{text!r} → {geo.detect_phase(text, mtype)!r}"


def test_the_hedge_guard_holds_even_where_prep_is_off_the_ladder():
    """A ladder without a "prep" rung must still refuse the launch — falling through to "launch" is exactly
    the bug this guards. Checked against every family so a future ladder edit cannot reopen it."""
    for fam, mtype in [("ballistic", "ballistic_missiles"), ("cruise", "cruise_missiles"),
                       ("uav", "drones"), ("airlaunch", "mig31k_departure"), ("kab", "guided_aerial_bombs")]:
        got = geo.detect_phase("ймовірний пуск", mtype)
        assert got != "launch", f"{fam}: a hedged launch came back as a launch"


def test_a_real_launch_still_reads_as_one_when_the_post_also_mentions_activity():
    """The generic "activity/preparing" wording sits below launch on purpose, so it cannot downgrade one."""
    assert geo.detect_phase("Активність авіації. Фіксуємо пуск крилатих ракет", "cruise_missiles") == "launch"


# ── wording taken verbatim from the channels, not invented ─────────────────────────────────────────────
def test_the_wording_the_feed_actually_uses():
    """Every string below is copied from a real post in data/corpus.jsonl. The first pass of this ladder was
    written from what Ukrainian air-defence wording *ought* to look like and matched almost none of it."""
    cases = [
        # kpszsu writes a glide-bomb release as a "launch". For a KAB the launch is the release.
        ("guided_aerial_bombs",
         "🚀Пуски керованих авіаційних бомб ворожою тактичною авіацією на Харківщину.", "drop"),
        ("drones", "Пуск реактивних з Орла, 4 групи", "launch"),
        ("mig31k_departure", "⚠️ Зафіксовано зліт МіГ-31К!", "takeoff"),
        ("mig31k_departure",
         "Оголошено тривогу по усій території країни через зліт російського винищувача МіГ-31К"
         " - носія аеробалістичної ракети Х-47М2 \"Кинджал\".", "takeoff"),
        # the nightly assessment: a launch that "may happen at any moment" has not happened
        ("ballistic_missiles",
         "Загальна оцінка загроз на ніч: запуск балістики може відбутись у будь-який момент.", "prep"),
        ("guided_aerial_bombs", "🚀КАБи на Сумщину та Донеччину.", None),
        ("drones", "Реактивні БпЛА на півночі у західному напрямку Київщини.", None),
    ]
    for mtype, text, want in cases:
        assert geo.detect_phase(text, mtype) == want, f"{text[:60]!r} → {geo.detect_phase(text, mtype)!r}"


def test_a_launcher_ukraine_destroyed_is_not_a_launch():
    """"пускові установки С-400" — the adjective, in a post about a Ukrainian strike. Reading that as an
    incoming launch would raise an alarm out of good news."""
    assert geo.detect_phase(
        "🔥Цієї ночі у Брянській області ЗСУ уразили дві пускові установки С-400", "unspecified_missiles") is None


def test_the_hedge_is_scoped_to_its_own_sentence():
    """A post often states one launch and speculates about the next. The speculation must not erase the fact,
    and the fact must not licence the speculation."""
    assert geo.detect_phase(
        "Фіксуємо пуски крилатих ракет. Можливі нові пуски найближчим часом.", "cruise_missiles") == "launch"
    assert geo.detect_phase(
        "Можливі пуски. Зафіксовано пуск з Каспію.", "cruise_missiles") == "launch"
    assert geo.detect_phase("Не виключаємо пуск найближчим часом.", "cruise_missiles") == "prep"


# ── the ladder itself ──────────────────────────────────────────────────────────────────────────────────
def test_every_type_the_app_reads_has_a_family():
    known = set(geo.PHASE_FAMILY)
    missing = {t for t, _ in geo.TYPE_RX if t not in known and t != "unknown"}
    assert not missing, f"these threat types have no lifecycle family: {sorted(missing)}"


def test_every_family_points_at_a_real_ladder():
    for mtype, fam in geo.PHASE_FAMILY.items():
        assert fam in geo.PHASE_LADDER, f"{mtype} → {fam}, which has no ladder"


def test_every_stage_the_detector_can_emit_is_on_some_ladder():
    """A stage no ladder contains would be detected and then silently dropped for every single type."""
    emitted = {key for key, _ in geo.PHASE_RX}
    on_ladders = {s for rungs in geo.PHASE_LADDER.values() for s in rungs}
    assert emitted <= on_ladders, f"unreachable stages: {sorted(emitted - on_ladders)}"


def test_progress_counts_along_the_right_ladder():
    assert geo.phase_progress("drones", "entering") == (3, 3)
    assert geo.phase_progress("cruise_missiles", "launch") == (5, 6)
    assert geo.phase_progress("guided_aerial_bombs", "drop") == (3, 4)


def test_progress_is_none_rather_than_a_guess():
    assert geo.phase_progress("drones", "drop") is None
    assert geo.phase_progress("drones", None) is None
    assert geo.phase_progress("something_new", "launch") is None


# ── what reaches the map ───────────────────────────────────────────────────────────────────────────────
def test_a_parsed_post_carries_the_stage_it_named():
    ms = geo.parse_post("Крилаті ракети на Полтавщині, зафіксовано пуски")
    assert ms and ms[0]["phase"] == "launch"


def test_a_parsed_post_that_named_no_stage_carries_none():
    ms = geo.parse_post("Шахеди на Чернігівщині, курсом на південь")
    assert ms and ms[0]["phase"] is None


def test_every_marker_has_the_key_whether_or_not_it_has_a_stage():
    """The map reads marker["phase"]; a missing key and a null stage must not be two different things."""
    for text in ("Шахеди на Чернігівщині", "Пуск балістики", "КАБи на Сумщину"):
        for m in geo.parse_post(text):
            assert "phase" in m, f"{text!r} produced a marker with no phase key"


# ── the trip to the map ────────────────────────────────────────────────────────────────────────────────
def test_the_stage_survives_the_trip_to_the_client():
    """parse_post sets it, but markers() copies, merges, chains and prunes on the way out. A stage that is
    correct in geo.py and missing from the API is a stage nobody ever sees."""
    import datetime as dt

    import server

    st = server.Store(":memory:")
    ts = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    st.add_feed([{"post_id": "war_monitor/test-phase-1", "channel": "war_monitor", "ts": ts,
                  "text": "Група шахедів над Ніжином, перетнули державний кордон", "tags": []}])
    out = server.State(st, {}).markers()
    assert out, "the post produced no marker at all"
    assert out[0].get("phase") == "entering", f"phase arrived as {out[0].get('phase')!r}"


def test_the_ladder_ships_with_the_marker_so_the_page_shows_stages_not_a_fraction():
    """The page prints the rungs by name. A bare "5 of 6" beside a missile reads as a countdown to impact —
    and the last rung is "in Ukrainian airspace", not arrival. So the names travel, and the page shows them."""
    ms = geo.parse_post("Крилаті ракети над Конотопом, зафіксовано пуски з Каспію")
    assert ms and ms[0]["phase"] == "launch"
    assert ms[0]["phase_ladder"] == geo.PHASE_LADDER["cruise"]
    assert ms[0]["phase_ladder"][ms[0]["phase_step"][0] - 1] == "launch", "step does not index its own ladder"


def test_a_marker_with_no_stage_ships_no_ladder():
    ms = geo.parse_post("Шахеди на Чернігівщині")
    assert ms and ms[0]["phase_ladder"] is None


def test_the_page_can_name_every_rung_in_every_language():
    """A rung with no translation would print its raw key — "redeploy" — to a Ukrainian reader."""
    import re
    src = open(os.path.join(ROOT, "static", "i18n.js"), encoding="utf-8").read()
    rungs = {r for ladder in geo.PHASE_LADDER.values() for r in ladder}
    for lang_block in re.findall(r"\n(en|uk|fr):\{", src):
        pass
    for r in sorted(rungs):
        assert src.count(f"ph_{r}:") == 3, f"ph_{r} is defined {src.count(f'ph_{r}:')}× — expected 3 languages"
