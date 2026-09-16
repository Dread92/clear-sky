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
    u.hit("1.2.3.4", "A", "load")
    first = u.salt
    u.day = "1999-01-01"          # pretend the day rolled over
    u.hit("1.2.3.4", "A", "load")
    assert u.salt != first and u.seen != set()


def test_counting_never_breaks_the_app():
    u = _usage()
    u.hit(None, None, "load")     # junk in, no exception out
    u.hit("1.2.3.4", "A", "ping")
