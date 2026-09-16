"""The Air Force morning summary is the only source that gives a real count of what was launched.
Everything else in the app counts *reports*, not targets — mixing the two would be a lie in the stats."""
import server

REAL_SUMMARY = """У ніч на 16 вересня (з 18.00 15 вересня) противник атакував 113-ма ударними БпЛА
типу Shahed, Гербера, різних типів з напрямків: Курськ, Міллерово, Орел — рф,
та 2-ма балістичними ракетами Іскандер-М з Курської області.

Станом на 08.30 підтверджено збиття/подавлення 89 ворожих БпЛА типу Shahed."""


def test_reads_drones_missiles_and_downed():
    s = server.parse_af_summary(REAL_SUMMARY)
    assert s == {"drones": 113, "missiles": 2, "down": 89}


def test_itemised_total_in_brackets_is_not_double_counted():
    s = server.parse_af_summary(
        "Ворог масовано атакував Україну: 810 ударними БпЛА та 13 ракетами "
        "(9 крилатих ракет Іскандер-К та 4 балістичні Іскандер-М). Збито/подавлено 747 цілей."
    )
    assert s["drones"] == 810 and s["missiles"] == 13


def test_itemised_list_after_colon_is_not_double_counted():
    s = server.parse_af_summary("Ворог атакував 35 ракетами: 6 балістичних Іскандер-М, 29 крилатих Х-101, та 280 ударними БпЛА.")
    assert s["drones"] == 280 and s["missiles"] == 35


def test_live_report_is_not_a_summary():
    assert server.parse_af_summary("Увага! 3 шахеди курсом на Київ з півночі.") is None


def test_empty_text_is_not_a_summary():
    assert server.parse_af_summary("") is None
