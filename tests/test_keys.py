"""Two keys, and confusing them takes the map away from everybody.

ACCESS_KEY was written for a private deployment: it puts a login form in front of the whole app. Told to set
it so the dashboard would work, a public instance locked out every reader of an air-raid map — police,
military, anyone — and the only symptom was a password box where the map used to be.

ADMIN_KEY protects the dashboard and nothing else. These tests pin the difference.
"""
import os
import sys
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import server  # noqa: E402


class _H:
    """Just enough of the request handler to exercise the two gates."""
    def __init__(self, cfg=None, cookie="", key=None):
        self.state = type("S", (), {"cfg": cfg or {}})()
        self.headers = {"Cookie": cookie}
        self.q = {"key": [key]} if key else {}

    _admin_key = server.Handler._admin_key
    _admin_ok = server.Handler._admin_ok
    _authorized = server.Handler._authorized


def test_with_no_key_at_all_the_map_is_public_and_the_dashboard_does_not_exist():
    with mock.patch.dict(os.environ, {}, clear=True):
        h = _H()
        assert h._authorized(None, {}) is True          # the map is open
        assert h._admin_ok({}) is False                 # the dashboard is never open, it is absent


def test_admin_key_leaves_the_map_public():
    """The failure this file exists for: a dashboard key must never cost a single reader the map."""
    with mock.patch.dict(os.environ, {"ADMIN_KEY": "adm"}, clear=True):
        h = _H()
        assert h._authorized(None, {}) is True
        assert _H(key="adm")._admin_ok({"key": ["adm"]}) is True
        assert _H(key="nope")._admin_ok({"key": ["nope"]}) is False


def test_access_key_still_locks_the_whole_app_for_a_private_deployment():
    with mock.patch.dict(os.environ, {"ACCESS_KEY": "priv"}, clear=True):
        assert _H()._authorized(None, {}) is False
        assert _H()._authorized(None, {"key": ["priv"]}) == "set"
        assert _H(cookie="uak=priv")._authorized(None, {}) is True


def test_access_key_alone_still_opens_the_dashboard_so_old_deployments_keep_working():
    with mock.patch.dict(os.environ, {"ACCESS_KEY": "priv"}, clear=True):
        assert _H()._admin_ok({"key": ["priv"]}) is True


def test_admin_key_wins_when_both_are_set():
    with mock.patch.dict(os.environ, {"ACCESS_KEY": "priv", "ADMIN_KEY": "adm"}, clear=True):
        assert _H()._admin_ok({"key": ["adm"]}) is True
        assert _H()._admin_ok({"key": ["priv"]}) is False


def test_the_admin_cookie_never_unlocks_the_app():
    """Opening the dashboard sets `uadm`. That cookie must not become a way past the app-wide gate."""
    with mock.patch.dict(os.environ, {"ACCESS_KEY": "priv"}, clear=True):
        assert _H(cookie="uadm=priv")._authorized(None, {}) is False
