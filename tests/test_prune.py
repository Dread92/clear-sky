"""How long a marker is allowed to stay on the map.

An unspecified threat is the special case: it is loud while it is fresh and then it is gone, because there is
no target type to keep half-remembered as a grey ghost."""
from datetime import datetime, timedelta, timezone

import server


def _state():
    st = server.State(server.Store(":memory:"), {"track_stale_minutes": 5})
    return st


def _m(minutes_ago, type_="drones", **kw):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    d = {"id": f"c#{minutes_ago}{type_}", "type": type_, "lon": 30.5, "lat": 50.4, "ts": ts,
         "channel": "kyiv_airdef", "place": "Київ", "heading": None, "count": 1, "oblast_uid": "31"}
    d.update(kw)
    return d


def _ids(kept):
    return {m["id"] for m in kept}


def test_a_fresh_unspecified_threat_is_shown():
    kept = _state()._chain_and_prune([_m(2, "unknown")], 45)
    assert len(kept) == 1 and not kept[0].get("stale")


def test_an_unspecified_threat_disappears_after_five_minutes():
    """No grey fade: nobody ever said what it was, so there is nothing to keep half-alive."""
    assert _state()._chain_and_prune([_m(7, "unknown")], 45) == []


def test_a_drone_goes_grey_instead_of_disappearing():
    kept = _state()._chain_and_prune([_m(7, "drones")], 45)
    assert len(kept) == 1 and kept[0]["stale"] >= 5


def test_a_drone_is_dropped_after_fifteen_minutes():
    assert _state()._chain_and_prune([_m(20, "drones")], 45) == []


# --- missiles are not aged like drones -----------------------------------------------------------
def test_a_missile_never_gets_the_grey_stale_flag():
    """A cruise missile covers 13 km a minute. A grey pin five minutes old would be a lie with a
    precise dot on it; the client draws a growing circle of where it could be instead."""
    kept = _state()._chain_and_prune([_m(7, "cruise_missiles")], 45)
    assert len(kept) == 1
    assert not kept[0].get("stale")
    assert kept[0]["fast"] is True


def test_a_missile_is_dropped_sooner_than_a_drone():
    assert _state()._chain_and_prune([_m(14, "ballistic_missiles")], 45) == []
    assert len(_state()._chain_and_prune([_m(14, "drones")], 45)) == 1      # a drone at 14 min is still grey


def test_a_fresh_missile_is_kept_plainly():
    kept = _state()._chain_and_prune([_m(1, "ballistic_missiles")], 45)
    assert len(kept) == 1 and not kept[0].get("stale")
