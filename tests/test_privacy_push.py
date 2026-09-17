"""The one row in this database that could say where somebody sleeps.

A push subscription paired the address a phone can be reached at with the exact coordinates of the village
its owner chose. Nothing in the app needs that: proximity alerts ask "is anything within N km", and N is
never smaller than 10. So the point is snapped to a ~10 km cell and the name is dropped.

The other half: alarm pushes. "Kyiv oblast — alert" wakes somebody at 3 a.m. and tells them nothing they can
act on — the siren already said that, louder. Enough of those and the ones that matter get swiped away too.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import server  # noqa: E402

VYSHENKY = {"name": "Vyshenky", "uk": "Вишеньки", "lat": 50.3013, "lon": 30.7211, "radius": 15}


def test_the_village_name_is_not_stored():
    c = server.coarse_home(VYSHENKY)
    assert "name" not in c and "uk" not in c
    assert set(c) <= {"lat", "lon", "radius", "cell_km"}


def test_the_point_is_moved_off_the_house():
    c = server.coarse_home(VYSHENKY)
    d = server.haversine_km(VYSHENKY["lat"], VYSHENKY["lon"], c["lat"], c["lon"])
    assert d > 0.4, "the stored point is still essentially the exact one"
    assert d <= server.HOME_CELL_KM, "it moved further than one cell, which would break proximity alerts"


def test_two_neighbours_collapse_onto_the_same_cell():
    """That is the point of a grid: inside a cell, the stored value cannot tell two people apart."""
    a = server.coarse_home({"lat": 50.3013, "lon": 30.7211})
    b = server.coarse_home({"lat": 50.3090, "lon": 30.7280})
    assert (a["lat"], a["lon"]) == (b["lat"], b["lon"])


def test_a_radius_is_never_smaller_than_the_cell():
    """A 2 km radius against a 10 km cell would be precision the stored point does not have."""
    c = server.coarse_home({"lat": 50.3, "lon": 30.7, "radius": 2})
    assert c["radius"] >= server.HOME_CELL_KM


def test_junk_is_dropped_rather_than_guessed():
    for bad in (None, {}, {"name": "Vyshenky"}, {"lat": "x", "lon": "y"}, "not a dict"):
        assert server.coarse_home(bad) == {}


def test_subscribing_stores_the_coarse_form():
    st = server.Store(":memory:")
    st.push_add({"endpoint": "https://example/1"}, VYSHENKY)
    got = st.push_all()[0]["home"]
    assert "name" not in got and got["lat"] != VYSHENKY["lat"]


def test_rows_written_before_this_are_rewritten_at_startup():
    """Otherwise the change protects new subscribers and leaves the existing ones exactly as exposed."""
    st = server.Store(":memory:")
    st.conn.execute("INSERT INTO push_subs(endpoint,sub,home,created,last_ok) VALUES(?,?,?,?,?)",
                    ("https://example/2", "{}", json.dumps(VYSHENKY), "", ""))
    st.conn.commit()
    assert st.coarsen_homes() == 1
    got = st.push_all()[0]["home"]
    assert "name" not in got
    assert st.coarsen_homes() == 0          # and it is idempotent


def _code(text):
    """Only the lines that execute — the comments here explain the very phrasing being asserted against."""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def test_the_push_no_longer_claims_an_exact_distance():
    """The stored point is a cell, so "23 km from you" would be precision the app gave away on purpose."""
    src = open(os.path.join(ROOT, "app", "server.py"), encoding="utf-8").read()
    body = src[src.index("def proximity_watch("):]
    body = _code(body[:body.index("\nTHREAT_EN")])
    assert "km from you" not in body, "the push still reports a distance the stored home cannot support"
    assert "within {round(radius)} km of you" in body
    assert "HOME_CELL_KM / 2" in body, "the radius test was not widened for the grid"


def test_siren_pushes_are_gone_and_the_specific_ones_are_not():
    src = open(os.path.join(ROOT, "app", "server.py"), encoding="utf-8").read()
    body = src[src.index("    def on_event(self, ev):"):]
    body = _code(body[:body.index("    def _queue(")])
    for gone in ("air raid alert", "all clear", "oblast — alert"):
        assert gone not in body, f"an alarm push is still being sent: {gone!r}"
    assert "MiG-31K airborne" in body and "Ballistic threat" in body, (
        "the specific, actionable pushes were removed too — those are the ones worth sending")


def test_nothing_else_writes_a_home_into_the_database():
    """One door in. A second INSERT bypassing coarse_home would quietly undo all of the above."""
    src = open(os.path.join(ROOT, "app", "server.py"), encoding="utf-8").read()
    writes = re.findall(r"(?:INSERT|REPLACE)[^\"']*push_subs", src)
    assert len(writes) == 1, f"push_subs is written from {len(writes)} places, not one"
