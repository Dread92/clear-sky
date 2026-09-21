"""What a mark on the map says without being read.

A silhouette is read before any label is. That makes it the fastest thing on the screen and the easiest one
to lie with: a pointed shape is read as a heading whether or not anybody reported one, and a shape that
moves is read as observed motion whether or not anything was observed. Both of those were happening.

So: a mark turns only along a course the post actually reported, and stays upright when there is none;
nothing animates; and each weapon has a shape of its own rather than borrowing a neighbour's.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = open(os.path.join(ROOT, "static", "kyiv.html"), encoding="utf-8").read()
I18N = open(os.path.join(ROOT, "static", "i18n.js"), encoding="utf-8").read()


def _fn(name):
    i = PAGE.index(f"function {name}(")
    return PAGE[i:PAGE.index("\n}", i)]


# ── orientation ────────────────────────────────────────────────────────────────────────────────────────
def test_a_mark_turns_only_to_a_course_the_post_reported():
    body = _fn("orientOf")
    assert "m.heading==null" in body.replace(" ", ""), "there is no test for a missing heading"
    assert "confidence==='none'" in body.replace(" ", ""), (
        "a heading the app inferred is good enough to turn the silhouette, which draws a bearing nobody gave")
    assert "return m.heading" in body


def test_an_outcome_mark_never_points_anywhere():
    """Shot down, exploded, lost: none of them have a course, and a pointed one would invent a direction
    for something that is no longer going anywhere."""
    body = _fn("orientOf")
    assert "m.status||m.endedBy" in body.replace(" ", "")


def test_the_rotation_that_reaches_the_dom_is_the_reported_one():
    assert "rotate(${orientOf(m)})" in PAGE, "the tick draws some other angle than orientOf's"


# ── motion ─────────────────────────────────────────────────────────────────────────────────────────────
def test_nothing_on_the_map_animates_along_a_path():
    """The figure-of-eight loiter stood in for "the exact point is not known". An animation does not read as
    uncertainty — it reads as a drone observed flying a path that nobody ever reported it flying."""
    assert "animateMotion" not in PAGE
    assert "LOITER" not in PAGE, "the loiter path constants are still here"
    assert "class=\"orbit\"" not in PAGE and "class='orbit'" not in PAGE


def test_the_uncertainty_the_loiter_stood_for_is_still_said_some_other_way():
    """Removing it must not quietly remove the doubt as well. The reach ring and the stated confidence are
    what carry it now, and both have to still be there."""
    assert "function reachOf(" in PAGE and "paintReach(" in PAGE
    assert "sh_conf" in PAGE


# ── the tail ───────────────────────────────────────────────────────────────────────────────────────────
def test_the_tail_is_capped_at_a_few_minutes():
    """A twenty-minute tail across three oblasts stops reading as "it came from there" and starts reading as
    a route — which is a claim about a flight plan nobody filed."""
    m = re.search(r"const TAIL_MIN=(\d+)", PAGE)
    assert m and 3 <= int(m.group(1)) <= 5


def test_the_tail_fades_towards_its_old_end():
    """One line of even weight says every point on it is equally current."""
    i = PAGE.index("if(m.history&&m.history.length&&!m.status&&!m.endedBy){")
    body = PAGE[i:i + 1400]
    assert "opacity:${" in body, "the legs are all drawn at the same weight"
    assert "(i+1)/(pts.length-1)" in body.replace(" ", ""), "nothing scales the fade along the track"


def test_an_outcome_has_no_tail():
    """It did not travel to where it is; it happened there."""
    i = PAGE.index("if(m.history&&m.history.length&&!m.status&&!m.endedBy){")
    assert "!m.status&&!m.endedBy" in PAGE[i:i + 80].replace(" ", "")


# ── silhouettes ────────────────────────────────────────────────────────────────────────────────────────
def test_every_threat_type_the_parser_emits_has_a_shape_of_its_own():
    import sys
    sys.path.insert(0, os.path.join(ROOT, "app"))
    import geo
    m = re.search(r"const GLYPH_BY_TYPE=\{(.*?)\};", PAGE, re.S)
    assert m
    table = dict(re.findall(r"(\w+):'(\w+)'", m.group(1)))
    for t, _ in geo.TYPE_RX:
        assert t in table, f"{t} has no silhouette and falls through to a generic one"


def test_the_shapes_it_points_at_all_exist():
    m = re.search(r"const GLYPH_BY_TYPE=\{(.*?)\};", PAGE, re.S)
    keys = set(re.findall(r"\w+:'(\w+)'", m.group(1)))
    for k in keys | {"jetdrone"}:
        assert re.search(rf"(?:^|[,{{\n ]){k}:", PAGE) or f"G.{k}=" in PAGE, f"glyph {k!r} is referenced but never drawn"


def test_an_aircraft_does_not_look_like_the_missile_it_carries():
    """A Tu-95 on its way to a launch line and a Kh-101 already in flight are different warnings. Drawn the
    same, the reader cannot tell how much time they have."""
    m = re.search(r"const GLYPH_BY_TYPE=\{(.*?)\};", PAGE, re.S)
    table = dict(re.findall(r"(\w+):'(\w+)'", m.group(1)))
    assert table["strategic_aircraft_activity"] != table["cruise_missiles"]
    assert table["tactic_aircraft_activity"] != table["guided_aerial_bombs"]
    assert table["supersonic_missiles"] != table["cruise_missiles"]


_MOVETO = re.compile(r"d=\"M\s*(-?\d*\.?\d+)\s*,?\s*(-?\d*\.?\d+)")


def _nose(name):
    """The first vertex of a glyph's first path — by convention here, its nose."""
    for pat in (rf"G\.{name}\s*=\s*'", rf"\b{name}\s*=\s*'", rf"[,{{\n ]{name}:\s*'"):
        m = re.search(pat, PAGE)
        if m:
            return _MOVETO.search(PAGE, m.end(), m.end() + 400)
    return None


def test_every_flying_silhouette_is_drawn_nose_up():
    """The mark turns to the reported course, so a shape drawn pointing down is drawn 180° wrong — which has
    already happened here once, to the ballistic glyph, and once to the KAB, whose fins were at the top so it
    flew backwards. Up is negative y in this coordinate system."""
    for name in ("supersonic", "bomber", "fighter", "missile", "banderol", "ballistic", "bomb", "SHAHED_BODY"):
        m = _nose(name)
        assert m, f"{name} has no path to check"
        x, y = float(m.group(1)), float(m.group(2))
        assert abs(x) < 0.01, f"{name}'s first vertex is off the centreline at x={x}"
        assert y < -5, f"{name}'s first vertex is at y={y} — that is not the top of the shape"


# ── event cleanup ──────────────────────────────────────────────────────────────────────────────────────
def test_outcome_marks_are_gone_within_a_quarter_of_an_hour():
    assert re.search(r"const OUTCOME_TTL=15\b", PAGE)


def test_an_explosion_keeps_its_own_shorter_clock():
    """Left up as long as the rest, it reads as "it is still going on there" — and one reported for a whole
    oblast says least of all, so it goes soonest."""
    i = PAGE.index("const outcomeTtl=")
    line = PAGE[i:PAGE.index("\n", i)].replace(" ", "")
    assert "m.status!=='impact'?OUTCOME_TTL" in line
    assert "m.place==='область'?5:10" in line


# ── the map surface ────────────────────────────────────────────────────────────────────────────────────
def test_the_running_tally_is_not_across_the_bottom_of_the_map():
    i = PAGE.index("$('livebar').innerHTML=")
    line = PAGE[i:PAGE.index("\n", i)]
    assert "parts.join" not in line, "the counters are still drawn over the map"


def test_the_tally_moved_somewhere_rather_than_being_deleted():
    assert "function renderLiveCounts(" in PAGE
    assert "id=\"livecounts\"" in PAGE
    assert I18N.count("lv_now:") == 3 and I18N.count("lv_none:") == 3


def test_the_history_layer_keeps_its_key_while_it_is_switched_on():
    """Those marks are drawn over the map by an explicit toggle. Taking the legend away would leave symbols
    on the map with nothing to read them by."""
    i = PAGE.index("$('livebar').innerHTML=")
    line = PAGE[i:PAGE.index("\n", i)]
    assert "IMP_H" in line
