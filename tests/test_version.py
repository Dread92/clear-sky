"""What is actually running should be answerable by a URL, not by reading a screenshot.

Twice now a conversation has stalled on "is production on this build or not", and both times the evidence was
a rendered page and a guess. The version lives in three places — the footer of the app, the constant the
server reports, and the top of the changelog — and the only way that is useful is if they cannot drift apart.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import server  # noqa: E402


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_the_page_and_the_server_agree_on_the_version():
    m = re.search(r"const APP_VERSION\s*=\s*'([\d.]+)'", _read("static/kyiv.html"))
    assert m, "static/kyiv.html no longer declares APP_VERSION"
    assert m.group(1) == server.APP_VERSION, (
        f"the page says {m.group(1)} and the server says {server.APP_VERSION}")


def test_the_static_footer_matches_too():
    """The div is what a reader sees before any JavaScript runs, so it has to be right on its own."""
    m = re.search(r'id="buildtag"[^>]*>v([\d.]+)<', _read("static/kyiv.html"))
    assert m, "the build tag div is gone"
    assert m.group(1) == server.APP_VERSION


def test_the_changelog_leads_with_this_version():
    m = re.search(r"^##\s*([\d.]+)", _read("CHANGELOG.md"), re.M)
    assert m, "no version heading in CHANGELOG.md"
    assert m.group(1).startswith(server.APP_VERSION), (
        f"the changelog leads with {m.group(1)} but the app is {server.APP_VERSION}")


def test_the_build_id_is_content_not_timestamps():
    """Two machines holding identical code must report the same build id, or it answers nothing. mtime-based
    ids differ on every container build and between any two checkouts, which is how 'same code?' became
    unanswerable in the first place."""
    a = server.build_id()
    for rel in server.BUILD_FILES:
        p = os.path.join(ROOT, rel)
        st = os.stat(p)
        os.utime(p, (st.st_atime + 1000, st.st_mtime + 1000))   # same bytes, different timestamp
    try:
        assert server.build_id() == a, "the build id moved when only the timestamps did"
    finally:
        for rel in server.BUILD_FILES:
            p = os.path.join(ROOT, rel)
            st = os.stat(p)
            os.utime(p, (st.st_atime - 1000, st.st_mtime - 1000))


def test_the_build_id_moves_when_the_code_does(tmp_path):
    a = server.build_id()
    p = os.path.join(ROOT, "static", "sw.js")
    original = _read("static/sw.js")
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(original + "\n// touched by a test\n")
        assert server.build_id() != a
    finally:
        with open(p, "w", encoding="utf-8") as f:
            f.write(original)
    assert server.build_id() == a
