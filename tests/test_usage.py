"""The usage counters exist to answer one question — is the app being used — without being able to answer
"by whom". These tests pin the privacy properties, not the numbers."""
import server


def _usage():
    return server.Usage(server.Store(":memory:"))


def test_a_device_is_counted_once_a_day():
    u = _usage()
    for _ in range(5):
        u.hit("1.2.3.4", "Mozilla/5.0", "load", lang="uk")
    day = u.report()["days"][0]
    assert day["devices"] == 1 and day["loads"] == 5


def test_different_devices_are_counted_separately():
    u = _usage()
    u.hit("1.2.3.4", "A", "load")
    u.hit("5.6.7.8", "B", "load")
    assert u.report()["days"][0]["devices"] == 2


def test_no_address_is_ever_stored():
    u = _usage()
    u.hit("203.0.113.45", "Mozilla/5.0 (very identifying)", "load", lang="uk")
    dump = str(u.report()) + str(u.store.conn.execute("SELECT * FROM usage").fetchall())
    assert "203.0.113.45" not in dump
    assert "very identifying" not in dump


def test_the_salt_changes_with_the_day_so_devices_cannot_be_linked_across_days():
    u = _usage()
    a = u._salt_for("1999-01-01")
    b = u._salt_for("1999-01-02")
    assert a != b


def test_yesterdays_salt_and_hashes_are_destroyed():
    """That destruction is the whole privacy claim: once the salt is gone, the day's hashes mean nothing."""
    u = _usage()
    u._salt_for("1999-01-01")
    u.store.conn.execute("INSERT INTO usage_seen(day,h) VALUES('1999-01-01','deadbeef')")
    u.store.conn.commit()
    u._salt_for("1999-01-02")
    left = u.store.conn.execute("SELECT COUNT(*) FROM usage_seen WHERE day='1999-01-01'").fetchone()[0]
    salts = u.store.conn.execute("SELECT COUNT(*) FROM kv WHERE k LIKE 'usage_salt:%'").fetchone()[0]
    assert left == 0 and salts == 1


def test_a_restart_does_not_recount_the_same_people():
    """This is what put 62 "devices" on the dashboard for a handful of readers: the salt and the seen-set
    were rebuilt at every process start, so every deploy and every auto-stop counted everybody again."""
    store = server.Store(":memory:")
    u1 = server.Usage(store)
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        u1.hit(ip, "A", "load")
    assert u1.report()["days"][0]["devices"] == 3

    for _ in range(4):                       # four restarts, same three readers coming back
        u = server.Usage(store)
        for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
            u.hit(ip, "A", "load")
    assert u.report()["days"][0]["devices"] == 3


def test_counting_never_breaks_the_app():
    u = _usage()
    u.hit(None, None, "load")     # junk in, no exception out
    u.hit("1.2.3.4", "A", "ping")
