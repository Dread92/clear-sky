"""Three display modes, for three different problems — none of which is "make it dimmer".

Daylight is a phone held at arm's length in direct sun, where a dark screen is a mirror. Luminance is what
survives that, so it inverts: near-white ground, near-black ink, no translucency, no soft glows. A 7% fill
that reads beautifully at 2 a.m. is simply not there outdoors.

Night is somebody in a shelter or a dark room who needs to keep their night vision and not light a window.
Everything dims, nothing pulses, the silhouettes drop to arrows.

The rule both share: "no glare" must never become "no warning". A ballistic mark stays unmistakable in every
mode — in night mode as a hard outline rather than a blinking glow.

Blackout stays a separate switch. It is about bytes and battery, not light, and somebody on 2G in bright sun
needs both at once.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the page and the silhouettes it draws (static/glyphs.js, shared with the Light page)
PAGE = (open(os.path.join(ROOT, "static", "kyiv.html"), encoding="utf-8").read()
        + open(os.path.join(ROOT, "static", "glyphs.js"), encoding="utf-8").read())
I18N = open(os.path.join(ROOT, "static", "i18n.js"), encoding="utf-8").read()


def _decl(selector):
    """The declaration block for an exact selector, as written."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", PAGE)
    return m.group(1) if m else None


def _hex(c):
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _lum(rgb):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = _lum(_hex(a)), _lum(_hex(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _var(block, name):
    m = re.search(rf"{name}\s*:\s*(#[0-9a-fA-F]{{3,6}})", block)
    return m.group(1) if m else None


# ── the three modes exist and are distinct ─────────────────────────────────────────────────────────────
def test_there_are_exactly_three_modes():
    m = re.search(r"const MODES=\[(.*?)\]", PAGE)
    assert m
    assert [x.strip().strip("'\"") for x in m.group(1).split(",")] == ["standard", "day", "night"]


def test_standard_adds_no_class_so_it_is_genuinely_the_untouched_default():
    body = PAGE[PAGE.index("function applyMode(){"):]
    body = body[:body.index("\n}")]
    assert "m!=='standard'" in body.replace(" ", ""), (
        "standard is a theme of its own rather than the absence of one, so it will drift from the default")


# ── daylight is actually readable in daylight ──────────────────────────────────────────────────────────
def test_daylight_inverts_rather_than_brightens():
    block = _decl("body.m-day")
    assert block, "there is no daylight theme"
    bg, ink = _var(block, "--bg"), _var(block, "--ink")
    assert _lum(_hex(bg)) > 0.7, f"the daylight background is not light ({bg})"
    assert _lum(_hex(ink)) < 0.1, f"the daylight ink is not dark ({ink})"


def test_daylight_text_clears_the_contrast_bar_for_small_text():
    """WCAG AA for body text is 4.5:1. Outdoors in sun the effective contrast is far lower than the number,
    so clearing it on paper is the floor, not the target."""
    block = _decl("body.m-day")
    for name in ("--ink", "--ink2"):
        c = _contrast(_var(block, "--bg"), _var(block, name))
        assert c >= 4.5, f"{name} on --bg is only {c:.1f}:1 in daylight mode"


def test_the_threat_colours_stay_distinguishable_against_white():
    """Which weapon it is has to survive the theme. The dark-mode reds and oranges wash out on white."""
    block = _decl("body.m-day")
    for name in ("--red", "--orange", "--green", "--blue"):
        v = _var(block, name)
        assert v, f"{name} is not restated for daylight"
        c = _contrast(_var(block, "--bg"), v)
        assert c >= 3.0, f"{name} ({v}) is only {c:.1f}:1 against the daylight background"


def test_daylight_removes_the_effects_that_do_not_survive_sunlight():
    """A glow and a 7% fill are invisible outdoors, so anything relying on them stops communicating."""
    block = _decl("body.m-day .mk .glyph")
    assert block and "filter:none" in block.replace(" ", "") and "animation:none" in block.replace(" ", "")


def test_daylight_retones_the_map_surface_and_not_only_the_chrome():
    """A light interface wrapped around a black map is the worst of both — the glare is still there and the
    contrast is gone. This was the first version of this mode."""
    for sel in ("body.m-day .mapwrap", "body.m-day .nb", "body.m-day .rn", "body.m-day #tiles"):
        assert _decl(sel), f"{sel} is not re-themed, so the map stays dark under a light interface"


def test_daylight_fixes_the_overlays_that_hardcode_a_dark_panel():
    """They were dark boxes with dark text in them. For a warning panel that is worse than not drawing it."""
    assert "body.m-day .tip" in PAGE and "body.m-day #toast" in PAGE
    assert "body.m-day .mk .lbl{" in PAGE, "marker labels keep their black halo under light text"


# ── night keeps night vision, and keeps the warning ────────────────────────────────────────────────────
def test_night_is_dimmer_than_standard():
    root = _decl(":root")
    night = _decl("body.m-night")
    assert _lum(_hex(_var(night, "--bg"))) < _lum(_hex(_var(root, "--bg")))
    assert _lum(_hex(_var(night, "--ink"))) < _lum(_hex(_var(root, "--ink")))


def test_night_emits_no_pulses_and_no_glows():
    block = _decl("body.m-night .mk .glyph")
    assert "filter:none" in block.replace(" ", "") and "animation:none" in block.replace(" ", "")
    assert _decl("body.m-night .imp .ig"), "impact marks still burn at full brightness"


def test_night_swaps_silhouettes_for_arrows():
    i = PAGE.index("const glyphKey=")
    body = PAGE[i:PAGE.index(";", PAGE.index("GLYPH_BY_TYPE[m.type]", i))]
    assert "MODE==='night'" in body.replace(" ", "")
    assert "G.arrow=" in PAGE, "night mode points at a glyph that does not exist"


def test_the_night_arrow_obeys_the_same_nose_up_rule():
    """It is rotated by the same code as every other mark, so a chevron drawn pointing down points 180°
    wrong at every target on the map at once."""
    m = re.search(r"G\.arrow='<path class=\"glyph\" d=\"M\s*(-?\d*\.?\d+)\s*,?\s*(-?\d*\.?\d+)", PAGE)
    assert m, "the arrow is not a path starting at its nose"
    assert abs(float(m.group(1))) < 0.01 and float(m.group(2)) < -5


def test_no_glare_never_becomes_no_warning():
    """The single thing night mode is not allowed to dim away."""
    block = _decl("body.m-night .mk.ballistic_missiles .glyph,body.m-night .mk.supersonic_missiles .glyph")
    assert block, "ballistic and supersonic marks are dimmed like everything else at night"
    assert "stroke:#ff6b60" in block.replace(" ", ""), "there is no high-contrast outline to replace the glow"
    assert "stroke-width:2.2px" in block.replace(" ", "")
    assert re.search(r"IMMEDIATE\.has\(m\.type\)", PAGE[PAGE.index("MODE==='night'") - 60:PAGE.index("MODE==='night'") + 120]), (
        "the immediate weapons are reduced to a generic arrow along with everything else")


# ── the mode is not the blackout switch ────────────────────────────────────────────────────────────────
def test_blackout_stays_a_separate_switch():
    """Somebody on 2G in bright sun needs both at once, and blackout is about bytes, not light."""
    assert "let LITE=" in PAGE and "let MODE=" in PAGE
    assert "ls('lite'" in PAGE and "ls('mode'" in PAGE
    body = PAGE[PAGE.index("function applyMode(){"):]
    assert "LITE" not in body[:body.index("\n}")], "changing display mode touches the blackout setting"


def test_switching_mode_rebuilds_the_marks():
    """The silhouettes differ between modes, and a built mark caches which glyph it drew."""
    body = PAGE[PAGE.index("function applyMode(){"):]
    body = body[:body.index("\n}")]
    assert "b.gk=null" in body, "marks keep the glyph from the previous mode until they happen to change type"


def test_the_choice_survives_a_reload():
    assert re.search(r"ls\('mode',\s*m\)", PAGE)


def test_every_mode_string_exists_in_all_three_languages():
    for k in ("m_mode", "m_md_std", "m_md_day", "m_md_night", "md_set", "md_tag_day", "md_tag_night"):
        assert I18N.count(f"{k}:") == 3, f"{k} is missing from a language"
