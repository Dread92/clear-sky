"""@xydessa_live — an Odesa live-tracking channel.

It writes the way the people watching the sky write: a bare district name, a count, "реактивний" for a jet
Shahed, half of it in Russian, and "-1" when one comes down. Everything below pins what the app is allowed to
conclude from that, and — more importantly — what it must not.
"""
import geo
import server
import translate


def p(text):
    return geo.parse_for_channel("xydessa_live", text)


def places(text):
    return [(m.get("place"), m.get("type")) for m in (p(text) or [])]


# -- the channel's own shorthand -------------------------------------------------------------------

def test_a_bare_district_name_is_a_position_but_never_a_threat_type():
    """This channel posts "Пересип" and means "it is over Peresyp now". It does not mean "a Shahed"."""
    out = p("Пересип")
    assert len(out) == 1
    assert out[0]["place"] == "Пересип"
    assert out[0]["type"] == "unknown"          # amber "type not stated", never a drone glyph
    assert out[0]["likely"] == "drones"         # a likelihood, carried separately, shown as a guess


def test_a_stated_type_is_used_and_a_jet_shahed_is_marked_as_one():
    out = p("+Реактивний санжійка")
    assert (out[0]["place"], out[0]["type"], out[0]["jet"]) == ("Санжійка", "drones", True)


def test_russian_spellings_resolve_to_the_same_place():
    """Half the posts on this channel are Russian. "черноморск" is Чорноморськ or it is nothing."""
    assert places("На чорноморськ один реактивний")[0][0] == "Чорноморськ"
    assert places("На черноморск один реактивный")[0][0] == "Чорноморськ"
    assert places("Аркадия")[0][0] == "Аркадія"


def test_poskot_is_the_district_everyone_calls_poskot():
    assert places("Ще один на поскот")[0][0] == "Селище Котовського"


def test_minus_one_is_never_read_as_anything():
    """"-1" means one was brought down. With no place and no other word it stays a line in the feed:
    a shoot-down drawn at a guessed position is worse than no shoot-down at all."""
    assert p("-1") == []
    assert p("-2") == []


def test_an_all_clear_is_an_all_clear():
    out = p("Одеса чисто")
    assert out and out[0]["status"] == "clear"


# -- what it must NOT conclude ---------------------------------------------------------------------

def test_a_southern_course_never_becomes_a_marker_over_the_town_of_pivdenne():
    """Південне is both a town near Odesa and the adjective "southern". The channel uses both. Until a post
    can be told apart from "курс південний", neither becomes a marker — a wrong pin 30 km up the coast is
    worse than a missing one."""
    assert p("курс південний") == []
    assert p("Назад в бік південного") == []


def test_the_channels_promo_tail_never_reaches_a_warning():
    """Every post is signed with a promo line, and one of them is profane. Nobody deciding whether to go to
    a shelter should be reading it."""
    cleaned = server._clean_post("Реактивний санжійка\n⚓️ Хуевая Одесса | LIVE Афиша | Прислать новость")
    assert cleaned == "Реактивний санжійка"
    assert "Одесса" not in cleaned and "Афиша" not in cleaned


def test_promo_stripping_leaves_an_ordinary_post_alone():
    assert translate.clean("Пересип") == "Пересип"
    assert server._clean_post("2 реактивних на південне") == "2 реактивних на південне"


# -- the gazetteer itself --------------------------------------------------------------------------

def test_every_new_odesa_place_sits_in_odesa_oblast():
    added = ["Пересип", "Лузанівка", "Селище Котовського", "Аркадія", "Хаджибейський лиман",
             "Санжійка", "Татарбунари", "Тузли", "Маяки"]
    for name in added:
        assert name in geo.PLACES, name
        lon, lat, uid = geo.PLACES[name]
        assert uid == "18", name
        # Odesa oblast, generously bounded: nothing here may land in the wrong half of the country
        assert 28.0 <= lon <= 31.5, (name, lon)
        assert 45.2 <= lat <= 48.2, (name, lat)


def test_the_city_sections_really_are_in_the_city():
    for name in ["Пересип", "Лузанівка", "Селище Котовського", "Аркадія"]:
        lon, lat, _ = geo.PLACES[name]
        olon, olat, _ = geo.PLACES["Одеса"]
        assert abs(lon - olon) < 0.3 and abs(lat - olat) < 0.25, name


# -- spellings the channels actually use -----------------------------------------------------------

def test_a_mig31k_is_recognised_however_the_channel_declines_it():
    """"Мігну31к в небе" is the same warning as "МіГ-31К". It raises the loudest banner in the app, so the
    pattern stays tight: міг/миг/mig, then 31 within three letters."""
    for text in ["я тебе Мігну31к в небе взлет с саваслейки", "міг-31 злетів", "миг31к", "MiG-31 airborne"]:
        assert "mig31k_departure" in server.tag_feed_text(text, "xydessa_live"), text


def test_ordinary_words_never_raise_the_mig_banner():
    for text in ["мігрант", "міграція 31 людина", "мигдаль", "мігом"]:
        assert "mig31k_departure" not in server.tag_feed_text(text, "xydessa_live"), text


def test_kalibrs_are_recognised_in_the_russian_spelling_too():
    assert "cruise_missiles" in server.tag_feed_text("калибы на зп", "xydessa_live")
    assert "cruise_missiles" in server.tag_feed_text("калібри на Одесу", "xydessa_live")
