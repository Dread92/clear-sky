"""The tags decide what lights the app up. A wrong tag on a real attack is a missed warning;
a wrong tag on a forecast is a false ballistic banner. Both are tested here."""
import server


def tags(text, channel="war_monitor"):
    return set(server.tag_feed_text(text, channel))


# --- forecasts must never be treated as live threats ------------------------------------------
FORECAST = """Загальна оцінка загроз для України на ніч 17 вересня.

🟩 Стратегічна авіація не активна.

🟨 Загроза ударних БпЛА низька.

🟧 Балістичні загрози на ніч вище середнього — противник активно присутній на Брянщині із запасом БК.
Запуск Іскандер/С-400/Циркон може відбутись у будь-який момент.

#обстановка@war_monitor"""


def test_nightly_assessment_is_a_forecast_not_a_ballistic_threat():
    t = tags(FORECAST)
    assert "forecast" in t
    assert "ballistic_missiles" not in t
    assert "drones" not in t
    assert "strategic_aircraft_activity" not in t


def test_forecast_keeps_no_marker_producing_tag():
    assert tags(FORECAST) <= {"alert", "forecast"}


# --- real events must still be tagged ----------------------------------------------------------
def test_real_ballistic_launch_is_tagged():
    assert "ballistic_missiles" in tags("⚠️ Зафіксовано пуск балістики з Брянщини! Негайно в укриття!")


def test_mig_takeoff_is_tagged():
    t = tags("Зліт МіГ-31К з аеродрому Саваслейка. Загроза застосування балістичного озброєння.", "kpszsu")
    assert "mig31k_departure" in t and "ballistic_missiles" in t


def test_drone_report_is_tagged():
    assert "drones" in tags("🛸 3 шахеди курсом на Бровари", "kyiv_airdef")


def test_all_clear_is_tagged():
    assert "clear" in tags("🟢 Відбій повітряної тривоги", "kyivoda")


def test_explosion_is_tagged():
    assert "impact" in tags("💥 Васильків — вибухи. Працює ППО", "povitryanatrivogaaa")


# --- irrelevant posts are dropped before storage ------------------------------------------------
def test_news_post_is_not_relevant():
    text = "Збірна України з волейболу перемогла у чвертьфіналі чемпіонату світу!"
    assert not server.is_relevant(text, server.tag_feed_text(text, "kyiv_times_official"), "kyiv_times_official")


def test_threat_post_is_relevant():
    text = "🛸 Шахед над Броварами, курс на Київ"
    assert server.is_relevant(text, server.tag_feed_text(text, "kyiv_airdef"), "kyiv_airdef")
