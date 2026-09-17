"""Three languages, one key set.

Every string in this app is added by hand to three blocks, and a key that exists in English but not in
Ukrainian does not fail anywhere: it renders as an empty span, or as the raw key, on the phone of the person
who reads the app in Ukrainian — which is almost everyone who actually depends on it. Nothing else in the
suite would notice. These tests do.
"""
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N = os.path.join(ROOT, "static", "i18n.js")
PAGE = os.path.join(ROOT, "static", "kyiv.html")
LANGS = ("en", "uk", "fr")


def _src(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _blocks():
    """The three language blocks of I18N={...}, as raw text, split on the top-level 'xx:{' markers."""
    s = _src(I18N)
    start = s.index("const I18N={")
    out = {}
    for lang in LANGS:
        m = re.search(r"\n\s*%s\s*:\s*\{" % lang, s[start:])
        assert m, f"no {lang} block in i18n.js"
        i = start + m.end() - 1          # at the '{'
        depth, j, in_str, quote, esc = 0, i, False, "", False
        while j < len(s):
            c = s[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == quote:
                    in_str = False
            elif c in "'\"`":
                in_str, quote = True, c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out[lang] = s[i:j + 1]
    return out


def _strip_comments(src):
    """// and /* */ outside strings. Without this the first key after a comment line is invisible, which is
    exactly the kind of near-miss that makes a linter worth less than no linter."""
    out, i, n, in_str, quote, esc = [], 0, len(src), False, "", False
    while i < n:
        c = src[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                in_str = False
            i += 1
            continue
        if c in "'\"`":
            in_str, quote = True, c
            out.append(c)
        elif c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _keys(block):
    """Top-level keys only: a key starts a line or follows ',' at nesting depth 1."""
    block = _strip_comments(block)
    keys, depth, i, in_str, quote, esc, at_key = set(), 0, 0, False, "", False, True
    while i < len(block):
        c = block[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                in_str = False
            i += 1
            continue
        if c in "'\"`":
            in_str, quote, at_key = True, c, False
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        elif c == ",":
            at_key = depth == 1
        elif at_key and depth == 1 and (c.isalpha() or c == "_"):
            m = re.match(r"[A-Za-z_][A-Za-z0-9_]*\s*:", block[i:])
            if m:
                keys.add(m.group(0)[:-1].strip())
                i += m.end()
                at_key = False
                continue
        elif not c.isspace():
            at_key = at_key and c == ","
        i += 1
    return keys


@pytest.fixture(scope="module")
def keysets():
    return {lang: _keys(b) for lang, b in _blocks().items()}


# Ukrainian has three plural forms where English and French have two, so a tn() family legitimately differs
# between languages: raion_red_1/_2/_5 against raion_red_1/_n. Only families the page actually pluralises
# count — cz_1 and cz_2 are two lines of a caption, not a plural, and guessing from the suffix said otherwise.
_SUF = re.compile(r"_(1|2|5|n)$")


@pytest.fixture(scope="module")
def plural_families():
    return set(re.findall(r"\btn\(\s*'([a-z0-9_]+)'", _src(PAGE)))


def _families(keys, plural):
    return {(_SUF.sub("", k) if _SUF.sub("", k) in plural else k) for k in keys}


def test_every_language_has_every_key(keysets, plural_families):
    en = keysets["en"]
    assert len(en) > 300, "the English block did not parse"
    for lang in ("uk", "fr"):
        missing = sorted(_families(en, plural_families) - _families(keysets[lang], plural_families))
        extra = sorted(_families(keysets[lang], plural_families) - _families(en, plural_families))
        assert not missing, f"{lang} is missing: {missing}"
        assert not extra, f"{lang} has keys English does not: {extra}"


def test_plurals_resolve_in_every_language(keysets, plural_families):
    """A tn() family must give every language something to land on, in that language's own shape."""
    assert plural_families, "the page uses no tn() at all — this test has stopped testing anything"
    for lang, keys in keysets.items():
        for fam in sorted(plural_families):
            forms = {k[len(fam) + 1:] for k in keys if k.startswith(fam + "_") and _SUF.search(k)}
            assert "1" in forms, f"{lang}.{fam} has no singular"
            assert "n" in forms or {"2", "5"} <= forms, f"{lang}.{fam} has no plural form: {sorted(forms)}"


def test_no_key_is_an_empty_string():
    for lang, block in _blocks().items():
        for k, v in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*('')", block):
            raise AssertionError(f"{lang}.{k} is an empty string")


def test_the_page_never_asks_for_a_key_that_does_not_exist(keysets):
    """t('foo') with no foo is a blank label on a map somebody is reading during a raid."""
    page = _src(PAGE)
    used = set(re.findall(r"\bt\(\s*'([a-z0-9_]+)'", page))
    used |= set(re.findall(r"\btn\(\s*'([a-z0-9_]+)'", page))
    used |= set(re.findall(r"data-t(?:-html|-title|-ph)?=\"([a-z0-9_]+)\"", page))
    en = keysets["en"]
    # tn('x', n) resolves to x_1 / x_n, and a few keys are built by concatenation (oq_ + quadrant)
    unknown = sorted(k for k in used - en
                     if f"{k}_1" not in en and f"{k}_n" not in en and not any(e.startswith(k) for e in en))
    assert not unknown, f"the page uses keys that do not exist: {unknown}"


def test_the_new_reading_tolerances_exist_everywhere(keysets):
    """The confidence figures and the oblast-wide wording are what this session added; pin them."""
    for k in ("pa_hi", "pa_md", "pa_lo", "pa_obl", "ha_hi", "ha_md", "ha_lo",
              "obl_somewhere", "obl_noplace", "lb_nearhome", "th_band", "msl_band"):
        for lang in LANGS:
            assert k in keysets[lang], f"{k} missing from {lang}"


def test_json_is_not_how_this_file_is_parsed():
    """Guard the guard: if i18n.js ever becomes JSON, _blocks() must be revisited rather than silently pass."""
    with pytest.raises(json.JSONDecodeError):
        json.loads(_src(I18N)[:200])
