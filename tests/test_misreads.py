"""Three ways the map once said something that was not true.

Each of these shipped, each was spotted on the live map, and each is the same failure underneath: the app
stated more than its source supported. They are pinned here so they cannot come back quietly.
"""
import geo


def st(text):
    return geo._status_of(geo._norm(text))


def found(text):
    return [n for _, _, n in geo._find_places(geo._norm(text))]


# -- 1. a destroyed building is not an intercepted target ------------------------------------------

def test_a_destroyed_warehouse_is_damage_not_a_shoot_down():
    """"Склад … знищено" drew a green "confirmed shot down" over a strike site — the opposite meaning,
    and it inflated the interception count with somebody's ruined building."""
    assert st("Склад гуманітарного фонду Олени Зеленської у Києві знищено") == "damage"


def test_a_real_shoot_down_is_still_a_shoot_down():
    assert st("Збито ціль над Києвом") == "down"
    assert st("Знищено 12 шахедів на Київщині") == "down"


def test_debris_damaging_a_roof_reads_as_damage_not_an_interception():
    assert st("Внаслідок падіння уламків пошкоджено дах будинку в Оболоні") == "damage"


def test_an_interception_that_also_damaged_something_stays_an_interception():
    assert st("Збито 5 БпЛА, уламки пошкодили будинок") == "down"


def test_a_plain_damage_report_is_damage():
    assert st("У Дніпровському районі пошкоджено скління та фасад офісної будівлі") == "damage"
    assert st("Вибито вікна у кількох житлових будинках") == "damage"


# -- 2. an over-stemmed name is not a match --------------------------------------------------------

def test_kolomak_is_never_read_as_kolomyia():
    """Stemming cut "Коломия" down to "колом", which then swallowed "Коломак" — a different town 700 km
    east, in another oblast. Five Shaheds were drawn over Ivano-Frankivshchyna because of it."""
    assert "Коломия" not in found("Харківщина: 5 БпЛА в районі Котельва - Коломак")


def test_the_towns_that_do_decline_still_resolve():
    """The guard must not cost a single real match."""
    cases = [("БпЛА на Коломию", "Коломия"), ("БпЛА над Фастовом", "Фастів"), ("БпЛА на Броварах", "Бровари"),
             ("У Києві вибух", "Київ"), ("Шахед над Білою Церквою", "Біла Церква"),
             ("Дрон над Кропивницьким", "Кропивницький"), ("БпЛА курсом на Ніжин", "Ніжин"),
             ("Ціль на Бориспіль", "Бориспіль"), ("на Одесу", "Одеса")]
    for text, name in cases:
        assert name in found(text), text


# -- 3. the oblast a marker claims must be the oblast of the place it found -------------------------

def test_a_marker_never_claims_an_oblast_its_own_town_is_not_in():
    """The Kolomyia marker carried Kharkiv's oblast id and Ivano-Frankivsk's coordinates at the same time.
    That contradiction was visible inside the app before it was visible on the map."""
    for text in ["Харківщина: БпЛА на Полтаву", "Полтавщина: ціль на Харків", "Київщина: дрон на Житомир"]:
        for m in (geo.parse_post(text) or []):
            place = (m.get("place") or "").replace("→ ", "")
            if place in geo.PLACES:
                assert m["oblast_uid"] == geo.PLACES[place][2], (text, place, m["oblast_uid"])


# -- 4. a stated part of an oblast is not the oblast centre ----------------------------------------

def test_the_north_of_an_oblast_is_drawn_in_the_north():
    """"Реактивні БпЛА на півночі … Київщини" was dropped on the oblast centre, which for Kyiv oblast sits
    near Vasylkiv — in the south. A quadrant the post states is information; discarding it is the same
    failure as inventing one."""
    centre = [m for m in geo.parse_post("БпЛА над Київщиною") if m["place"] == "область"][0]
    north = [m for m in geo.parse_post("Реактивні БпЛА на півночі у західному напрямку Київщини.")
             if m["place"] == "область"][0]
    assert north["lat"] > centre["lat"] + 0.3
    assert north["evidence"]["position"]["quad"] == "n"


def test_a_course_word_never_moves_the_marker():
    """"у західному напрямку" is where it is going, not where it is. Only locative phrasing counts."""
    assert geo.oblast_quadrant(geo._norm("БпЛА у західному напрямку Київщини")) is None
    assert geo.oblast_quadrant(geo._norm("БпЛА курсом на південь Київщини")) is None


def test_the_quadrant_keeps_its_low_confidence():
    m = [x for x in geo.parse_post("БпЛА на сході Харківщини") if x["place"] == "область"][0]
    assert m["evidence"]["position"]["confidence"] == "low"


# -- 5. a press release is not an observation of the sky -------------------------------------------

NEWS = ("Уряд розширив існуючу програму страхування воєнних ризиків для бізнесу та спростив отримання "
        "компенсацій. Росія щодня цілеспрямовано атакує цивільні підприємства, складські та логістичні "
        "потужності. Бізнес зазнає значних втрат і потребує підтримки для швидкого відновлення. Розширюємо "
        "перелік майна, за пошкодження або знищення якого можна отримати компенсацію. Збільшуємо максимальну "
        "компенсацію страхової премії з 3 до 5 млн грн на рік для одного підприємства. Пільгові кредити тепер "
        "можна буде залучати для відновлення паливної та складської інфраструктури.")


def test_a_government_announcement_never_becomes_a_marker():
    """This one put "damage on the ground" over Kyiv and closed a live Shahed track with it. It is a policy
    announcement about insurance — the words "пошкодження" and "знищення" in it are about compensation."""
    assert st(NEWS) is None
    assert geo.parse_post(NEWS) == []


def test_policy_vocabulary_alone_disqualifies_an_outcome():
    for text in ["Уряд ухвалив постанову про компенсацію за знищене житло",
                 "Кабмін збільшив бюджет на відшкодування за пошкоджені підприємства"]:
        assert st(text) is None, text


def test_a_real_report_is_not_caught_by_the_news_filter():
    """The filter must not cost a single real observation."""
    for text, want in [("Збито ціль над Києвом", "down"),
                       ("Вибух у Києві", "impact"),
                       ("У Дніпровському районі пошкоджено скління та фасад офісної будівлі", "damage"),
                       ("Горить дах будинку в Оболоні", "fire"),
                       ("Київщина чисто", "clear")]:
        assert st(text) == want, text


# -- 6. an article, a fire station, and an oblast that is also a metro station ----------------------

FIRE_STATION = ("Під час робочої поїздки на Олевщину привітав із прийдешнім професійним святом працівників "
                "обласного комунального підприємства «Житомироблагроліс». В Олевську разом із першим "
                "заступником Житомирської обласної ради оглянули нову пожежну станцію. Тут є все необхідне "
                "для роботи — спеціальне спорядження, пожежна техніка, облаштовані приміщення та зона "
                "відпочинку для працівників. Такі умови дозволяють бути готовими до оперативного реагування "
                "на пожежі, особливо в умовах великих лісових масивів Олевщини.")


def test_a_visit_to_a_new_fire_station_is_not_a_fire():
    """The root "пожеж" is in the name of every fire service, engine and brigade in the country. On its own
    it cannot mean something is burning — this post drew a fire marker 9 km from Kyiv."""
    assert st(FIRE_STATION) is None
    assert geo.parse_post(FIRE_STATION) == []


def test_fire_service_vocabulary_alone_is_never_a_fire():
    for text in ["Оглянули нову пожежну станцію, пожежна техніка",
                 "Пожежники працюють у Броварах",
                 "Прибули пожежні розрахунки ДСНС"]:
        assert st(text) != "fire", text


def test_a_post_saying_there_is_no_fire_does_not_draw_one():
    """"Пожежі попередньо немає" drew a fire. And it must not cost the post its damage marker either."""
    text = ("У Дніпровському районі пошкоджені скління та фасад офісної будівлі. У Солом'янському районі "
            "зруйнована складська будівля. Пожежі попередньо немає.")
    assert st(text) == "damage"


def test_a_real_fire_is_still_a_fire():
    assert st("Горить дах будинку в Оболоні") == "fire"
    assert st("Пожежа в Дарницькому районі") == "fire"
    assert st("Внаслідок влучання виникла пожежа на складі") == "impact"


def test_an_oblast_adjective_is_not_the_kyiv_place_of_the_same_name():
    """"Житомирська" is a metro station on Kyiv's red line and also how every post names Zhytomyr oblast.
    Read as the station, a forestry post about Olevsk — 150 km away — planted a marker beside Kyiv."""
    for text in ["Житомирська обласна рада ухвалила рішення", "Житомирська область, відбій тривоги",
                 "Харківська ОВА повідомляє"]:
        assert "Житомирська" not in found(text), text


def test_the_kyiv_place_still_resolves_when_it_is_actually_meant():
    assert "Житомирська" in found("БпЛА на Житомирську")


def test_an_article_produces_no_status_of_any_kind():
    """The news guard now covers every status, not only the two that were caught first."""
    for text in [FIRE_STATION, NEWS]:
        assert st(text) is None, text[:60]
