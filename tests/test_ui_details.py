"""The small things a live night found (1.19): each of these was reported from a phone."""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = open(os.path.join(ROOT, "static", "kyiv.html"), encoding="utf-8").read()
LIGHT = open(os.path.join(ROOT, "static", "light.html"), encoding="utf-8").read()
I18N = open(os.path.join(ROOT, "static", "i18n.js"), encoding="utf-8").read()
SRC = open(os.path.join(ROOT, "app", "server.py"), encoding="utf-8").read()


def _rule(src, sel):
    m = re.search(re.escape(sel) + r"\{([^}]*)\}", src)
    assert m, sel
    return m.group(1)


def test_the_zone_label_is_sized_in_screen_pixels():
    """"10 km" was 2.2 km tall: zoomed in, it filled the screen."""
    r = _rule(PAGE, ".zlbl")
    assert "var(--k" in r and "2.2px" not in r


def test_the_tile_credit_sits_in_a_bottom_corner():
    r = _rule(PAGE, ".attrib")
    assert "bottom:" in r and "top:" not in r


def test_the_crimea_cossack_is_drawn_above_the_raion_outlines_in_one_path_per_colour():
    body = PAGE[PAGE.index("function cossack(){"):PAGE.index("// ---- street tiles")]
    assert "<rect x=\"${x-6}\"" not in body                     # no longer 200 separate squares
    i = PAGE.index("$('obllbl').innerHTML=")
    assert "cossack()" in PAGE[i:PAGE.index("\n", PAGE.index("\n", i) + 1)]
    assert "cossack()" not in PAGE[PAGE.index("$('oblastsg').innerHTML="):i]


def test_press_and_hold_survives_a_trembling_finger():
    """The drag test was 0.2 km — less than a pixel at oblast zoom — so the hold was always cancelled."""
    assert "HOLD_SLOP_PX" in PAGE and "Math.abs(dx)+Math.abs(dy)>0.2" not in PAGE
    assert "addEventListener('contextmenu',e=>e.preventDefault())" in PAGE
    assert "-webkit-touch-callout:none" in _rule(PAGE, ".mapwrap")


def test_the_empty_pin_chip_arms_the_map_instead_of_only_explaining():
    i = PAGE.index("const setFor=id=>")
    assert "armPin(" in PAGE[i:i + 120]
    assert "if(PIN_ARM&&tap){ dropPin(" in PAGE
    assert I18N.count("sl_pin_arm:") == 3 and I18N.count("sl_pin_cancel:") == 3


def test_the_language_is_a_drop_down_on_both_pages():
    assert 'id="langsel"' in PAGE and 'id="langsel"' in LIGHT
    assert "<select" in PAGE[PAGE.index('id="disc"'):PAGE.index('id="discok"')]
    assert "function langSelect(" in I18N and "function pickLang(" in I18N


def test_changing_the_language_in_the_notice_shows_the_notice_again():
    """Tapping a language in the opening notice reloaded the page — and the notice, already marked as seen,
    did not come back: it closed before anyone had read it."""
    body = I18N[I18N.index("function pickLang("):I18N.index("function langSelect(")]
    assert "removeItem('disc_seen')" in body
    assert "langSelect(document.getElementById('dlang'),true)" in PAGE
    assert "langSelect($('dmlang'),true)" in LIGHT


def test_i_understand_is_big_and_centred():
    for src, sel in ((PAGE, ".dok"), (LIGHT, "#dm #dmok")):
        r = _rule(src, sel)
        assert "width:100%" in r and "margin:18px auto" in r


def test_light_zooms_the_map_not_the_page():
    r = _rule(LIGHT, "#radar")
    assert "touch-action:pan-y" in r and "user-select:none" in r      # a tap no longer selects a place name
    assert "touch-action:none" in _rule(LIGHT, "#radar.zoomed")
    assert "function zoomAt(" in LIGHT and "gesturestart" in LIGHT and "e.preventDefault(); const [a,b]=e.touches" in LIGHT


def test_light_opens_a_mark_on_a_tap():
    assert "function tapAt(" in LIGHT and "RT.push({id:m.id,x,y})" in LIGHT
    assert 'id="rcard"' in LIGHT and "row(selR,true)" in LIGHT


def test_the_new_logo_is_the_app_icon_and_the_partner_logos_stay():
    for man in ("manifest.json", "light.webmanifest"):
        icons = json.load(open(os.path.join(ROOT, "static", man), encoding="utf-8"))["icons"]
        for ic in icons:
            assert ic["src"].startswith("/static/logo-cs"), ic
            assert os.path.isfile(os.path.join(ROOT, ic["src"].lstrip("/")))
        assert any(ic["purpose"] == "maskable" for ic in icons)
    assert 'src="/static/logo-64.png" alt="07300"' in PAGE          # NGO 07300 stays in the header
    assert "/static/bf-logo.png" in PAGE                             # Black Flame stays in the credits
    assert '"logo-cs-64.png"' in SRC                                 # the favicon


def test_mark_labels_are_spaced_from_what_is_drawn_not_from_a_copy_of_the_css():
    """25 Sep 2026: "⚠ likely a drone" and "↓ DESCENDING · updated 21:46" written over each other. The spacing
    code had its own copy of the rules saying which label lines show, and that copy did not know "descending"."""
    i = PAGE.index("  // Label de-collision (screen px).")
    body = PAGE[i:PAGE.index("  // edge indicators", i)]
    assert "getComputedStyle(el).display" in body
    assert "classList.contains('inbound')?true" not in body          # the hand-kept copy is gone
    assert "glyphs.filter(" in body and "out(r)" in body             # clear of other marks and of the map's edge
    assert "'nolbl2'" in body and "'nolbl'" in body                  # a label that fits nowhere is shortened, then left out
    assert ".mk.nolbl .lbl,.mk.nolbl .lbl2,.mk.nolbl2 .lbl2{display:none!important}" in PAGE


def test_a_town_name_under_a_threat_label_is_hidden():
    i = PAGE.index("function layoutTownLabels(){")
    assert "const placed=[...LBLBOX.boxes]" in PAGE[i:i + 1500]
