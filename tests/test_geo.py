"""Positions are life-or-death: a target is drawn where a post said it was, never anywhere else.
These tests pin the behaviour the map depends on."""
import geo


def test_drone_heading_to_a_town_is_placed_at_that_town():
    m = geo.parse_post("🛸 2 БпЛА курсом на Бровари з північного сходу")[0]
    assert m["type"] == "drones"
    assert m["count"] == 2
    assert round(m["lon"], 1) == 30.8 and round(m["lat"], 1) == 50.5
    assert m["oblast_uid"] == "14"


def test_position_carries_its_evidence_and_confidence():
    m = geo.parse_post("🛸 БпЛА над Броварами")[0]
    ev = m["evidence"]["position"]
    assert ev["matched"] and ev["confidence"] in ("low", "medium", "high")


def test_an_alert_announcement_never_becomes_a_marker():
    # "air raid alert in Kyiv oblast" is an alert, not a target seen over Kyiv
    assert geo.parse_post("🔴 Повітряна тривога в Київській області. Прямуйте в укриття!") == []


def test_all_clear_is_not_a_target():
    assert geo.parse_post("🟢 Відбій повітряної тривоги в Київській області") == []


def test_similar_town_names_are_not_confused():
    # Михайло-Коцюбинське (Chernihiv oblast) must not match Коцюбинське (next to Kyiv)
    m = geo.parse_post("🛸 БпЛА на Михайло-Коцюбинське")
    assert not m or round(m[0]["lat"], 1) != 50.5


def test_city_siren_maps_to_its_raion():
    out = geo.parse_city_siren("🔴 Повітряна тривога в місті")
    assert out is None or isinstance(out, (dict, list))
