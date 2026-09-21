"""The header band, when there is more than one thing to say and when there is nothing.

Two failures this guards against. First, silently dropping threats: the strip showed the single most urgent
one and threw the rest away, so a night with a MiG-31K airborne *and* a ballistic launch looked exactly like
a night with only the MiG. Second, contradicting itself: a green "no alert" line sitting directly under a red
official alert band is worse than showing nothing, because one of the two is wrong and the reader cannot tell
which.

Everything here is read from the page source. The behaviour itself is exercised in a browser; these pin the
decisions that are easy to undo by accident.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = open(os.path.join(ROOT, "static", "kyiv.html"), encoding="utf-8").read()
I18N = open(os.path.join(ROOT, "static", "i18n.js"), encoding="utf-8").read()


def _strip_fn():
    i = PAGE.index("function threatStrip(){")
    return PAGE[i:PAGE.index("\nfunction render(){", i)]


# ── stacking ───────────────────────────────────────────────────────────────────────────────────────────
def test_threats_past_the_first_are_not_thrown_away():
    body = _strip_fn()
    assert "thr.slice(1)" in body, "the strip still renders only the top threat"
    assert "ts_more" in body, "there is no count for the ones that did not fit on the line"


def test_the_same_threat_from_four_channels_is_one_row():
    """kpszsu, monitor_ukr, eRadar and a regional channel relay a MiG-31K take-off inside a minute. Four
    identical rows add nothing and push the ballistic warning below them off the visible line."""
    body = _strip_fn()
    assert "srcs" in body and "by.set(" in body, "reports are not collapsed by threat type"
    assert "ts_conf" in body, "the channels that agree are not shown as corroboration"


def test_the_expanded_list_stays_open_across_a_re_render():
    """The page re-renders every few seconds. State inside threatStrip() would fold the list up under the
    reader's finger."""
    assert re.search(r"^let TS_OPEN=false;", PAGE, re.M), "the open/closed state is not outside the function"


def test_it_is_a_band_in_the_header_and_never_a_pop_up_over_the_map():
    """A warning that covers the map takes away the thing the reader opened the app to look at."""
    i = PAGE.index('<div class="tstrip" id="tstrip"')
    assert PAGE.index("</header>") > i, "the threat strip escaped the header"
    assert "position:fixed" not in PAGE[PAGE.index(".tstrip{"):PAGE.index(".tstrip{") + 400]
    body = _strip_fn()
    assert body.count("--top") >= 3, "the map offset is not updated for every state the strip can be in"


# ── the calm state ─────────────────────────────────────────────────────────────────────────────────────
def test_quiet_is_reported_rather_than_left_blank():
    body = _strip_fn()
    assert "idleLine()" in body
    assert "tstrip calm" in body


def test_the_calm_line_never_appears_while_the_readers_own_region_is_under_alert():
    i = PAGE.index("function idleLine(){")
    body = PAGE[i:PAGE.index("\n}", i)]
    assert "return ''" in body, "nothing suppresses the calm line during an alert"
    assert "mine.has(a.oblast_uid)" in body


def test_kyiv_city_and_oblast_count_as_one_place_in_both_directions():
    """Picking the city and getting "no alert" while the oblast around you is under one is the same bug as
    the reverse, and the reverse is the one that is easy to write. One shared helper, so the calm line and
    the banner colour can never disagree about which region is the reader's."""
    m = re.search(r"const oblPair=u=>(.*)", PAGE)
    assert m, "there is no city/oblast pairing at all"
    assert "u==='31'||u==='14'" in m.group(1).replace(" ", ""), "the pairing is one-directional"
    assert PAGE.count("oblPair(MYOBL.uid)") >= 2, "the calm line and the banner colour use different rules"


def test_the_banner_takes_its_colour_from_the_official_alert():
    """A red strip over a yellow band, or the reverse, makes the reader work out which one to believe."""
    body = _strip_fn()
    assert "officialLevel()" in body
    assert "'offr'" in body and "'offy'" in body
    i = PAGE.index("function officialLevel(){")
    fn = PAGE[i:PAGE.index("\n}", i)]
    assert "alert_level" in fn and "'R'" in fn and "'Y'" in fn
    assert "location_type==='oblast'||x.location_type==='city'" in fn.replace(" ", ""), (
        "a raion-level alert would set the colour for the whole region")


def test_a_threat_with_no_official_alert_yet_keeps_its_own_colour():
    """The app reads the channels before the siren is declared. Painting that green, or red, would either
    hide it or claim an alert that has not been issued."""
    body = _strip_fn()
    i = body.index("el.className='tstrip '")
    line = body[i:body.index(";", i)]
    assert "'mig'" in line and "'bal'" in line, (
        "the threat-type colours were dropped, so an unconfirmed threat has no colour of its own")


def test_the_calm_line_counts_and_never_projects():
    """"3 targets on the map" is a count of what was reported. "heading your way" would be a claim about
    where they are going, which nothing in the data supports."""
    i = PAGE.index("function idleLine(){")
    body = PAGE[i:PAGE.index("\n}", i)]
    for banned in ("heading", "eta", "inbound", "towards", "SPEED("):
        assert banned not in body, f"the calm line projects something: {banned!r}"
    assert "!m.status&&!m.endedBy&&!m.stale" in body.replace(" ", ""), (
        "the target count includes outcomes or stale marks, which are not targets on the map")


def test_every_new_string_exists_in_all_three_languages():
    for k in ("ts_more", "ts_conf", "ts_calm", "ts_calm_obl", "ts_calm_noobl", "ts_calm_tgt"):
        assert I18N.count(f"{k}:") == 3, f"{k} is missing from a language"
