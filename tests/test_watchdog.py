"""The server never hangs in silence: a held lock is reported with every thread's stack, and a server that stays
stuck exits so that Fly restarts it (25 Sep 2026: pages, pings and the official alert reader stopped answering
while /healthz still said "ok")."""
import os
import threading

import server

SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "server.py"), encoding="utf-8").read()


def _dog():
    st = server.State(server.Store(":memory:"), {})
    d = server.Watchdog(st)
    d.WAIT = 0.2
    return st, d


def test_a_free_server_passes():
    _, d = _dog()
    assert d.check() == []


def test_a_held_database_lock_is_caught():
    st, d = _dog()
    st.store.lock.acquire()
    try:
        why = d.check()
    finally:
        st.store.lock.release()
    assert why and "database" in why[0]


def test_a_held_state_lock_is_caught():
    st, d = _dog()
    t = threading.Thread(target=lambda: (st.lock.acquire(), __import__("time").sleep(1), st.lock.release()))
    t.start()
    __import__("time").sleep(0.05)
    why = d.check()
    t.join()
    assert any("alert state" in w for w in why)


def test_it_restarts_the_process_and_is_started():
    body = SRC[SRC.index("class Watchdog"):SRC.index("class OfficialAlerts")]
    assert "os._exit(" in body and "sys._current_frames()" in body
    assert "Watchdog(state).start()" in SRC


def test_healthz_is_not_ok_when_the_database_is_stuck():
    i = SRC.index('if u.path == "/healthz":')
    assert "st.store.lock.acquire(timeout=" in SRC[i:i + 400] and "503" in SRC[i:i + 400]
