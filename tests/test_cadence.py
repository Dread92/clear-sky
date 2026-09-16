"""How fast the app refreshes, and why it is not one number.

A Shahed at ~3 km a minute forgives a fifteen-second gap. A ballistic missile at ~35 does not: every second
nobody knows about a report is about half a kilometre of somebody's warning. So the cadence is a level the
server decides, and the server speeds its own sources up to match — a one-second client sitting in front of a
fifteen-second poller would only be refreshing a stale answer faster.
"""
from datetime import datetime, timedelta, timezone

import server


def _state(**cfg):
    return server.State(server.Store(":memory:"), cfg)


def _post(st, tags):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    st.store.add_feed([{"post_id": "p1", "channel": "kyiv_airdef", "ts": ts, "text": "тест", "tags": tags}])


def test_quiet_sky_is_level_zero():
    assert _state().missile_active() == 0


def test_a_cruise_missile_is_level_one():
    st = _state()
    _post(st, ["cruise_missiles"])
    assert st.missile_active() == server.State.MSL_CRUISE


def test_a_ballistic_missile_is_level_two():
    st = _state()
    _post(st, ["ballistic_missiles"])
    assert st.missile_active() == server.State.MSL_BALLISTIC


def test_ballistic_outranks_cruise_whatever_the_order():
    st = _state()
    _post(st, ["cruise_missiles", "ballistic_missiles"])
    assert st.missile_active() == server.State.MSL_BALLISTIC


def test_the_level_is_still_truthy_for_older_call_sites():
    """`missile` in /api/version stayed a boolean; anything testing it as a flag must keep working."""
    st = _state()
    _post(st, ["cruise_missiles"])
    assert bool(st.missile_active()) is True
    assert bool(_state().missile_active()) is False


def test_sources_are_read_faster_while_a_ballistic_alert_is_open():
    quiet, bal = _state(), _state()
    _post(bal, ["ballistic_missiles"])
    assert server.poll_gap(quiet, 15) == 15
    assert server.poll_gap(bal, 15) == 5


def test_a_broken_state_never_changes_the_poll_interval():
    """Cadence is not allowed to be the thing that breaks a poller."""
    class Boom:
        def missile_active(self):
            raise RuntimeError("no")
    assert server.poll_gap(Boom(), 30) == 30


def test_pings_are_batched_rather_than_written_one_by_one():
    """At one ping a second per device, a commit per ping would stall the alert path itself."""
    u = server.Usage(server.Store(":memory:"))
    u.hit("1.2.3.4", "A", "load")                       # the device is registered once
    writes = []
    real = u._flush
    u._flush = lambda day, n: (writes.append(n), real(day, n))[1]
    for _ in range(500):
        u.hit("1.2.3.4", "A", "ping")
    assert len(writes) <= 5, f"one write per ping: {len(writes)}"
    assert u.report()["days"][0]["pings"] == 500   # including whatever has not been written yet
