"""1.29: a notification goes to all its phones at once, over kept-open connections. It used to be one phone at a
time on a new HTTPS connection each: with a thousand subscribers the last phone heard of a ballistic missile
minutes late. A local stand-in for a push service answers each push after 50 ms, like a real one far away."""
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import push  # noqa: E402
import pytest  # noqa: E402

pytestmark = pytest.mark.skipif(not push.AVAILABLE, reason="cryptography not installed")


class FakePushService(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"          # keep-alive, like the real push services
    requests, connections, auth = [0], set(), set()
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        with self.lock:
            self.requests[0] += 1
            self.connections.add(self.client_address)
            self.auth.add(self.headers.get("Authorization"))
        time.sleep(0.05)
        code = 410 if self.path.endswith("/gone") else 201
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()


def _subs(port, n, gone=()):
    out = []
    for i in range(n):
        _, pub = push.generate_vapid()          # a phone's own key pair (only the public half is sent)
        out.append({"endpoint": f"http://127.0.0.1:{port}/wp/{i}" + ("/gone" if i in gone else ""),
                    "keys": {"p256dh": pub, "auth": push.b64u(os.urandom(16))}})
    return out


class Svc(ThreadingHTTPServer):
    request_queue_size = 128          # a real push service does not refuse 32 connections at once


def test_many_phones_at_once_on_kept_open_connections():
    srv = Svc(("127.0.0.1", 0), FakePushService)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        priv, pub = push.generate_vapid()
        sender = push.Sender(priv, pub, workers=32)
        subs = _subs(port, 400, gone={7, 99})
        t0 = time.time()
        res = sender.send_many(subs, {"title": "t", "body": "b", "tag": "threat"})
        took = time.time() - t0
        assert len(res) == 400
        assert sum(1 for _, code, _ in res if code == 201) == 398
        assert [s["endpoint"].split("/")[-2] for s, _, gone in res if gone] == ["7", "99"]
        # one at a time this is 400 x 50 ms = 20 s; 32 at once, on open connections, well under 3 s
        assert took < 3, took
        # connections are kept open and reused: at most one per worker, not one per phone
        assert len(FakePushService.connections) <= 32, len(FakePushService.connections)
        # the VAPID signature is made once per push service, not once per phone
        assert len(FakePushService.auth) == 1
        # a second notification reuses the same connections
        before = set(FakePushService.connections)
        sender.send_many(subs[:64], {"title": "t2", "body": "b", "tag": "alert"})
        assert FakePushService.connections == before
    finally:
        srv.shutdown()


def test_a_closed_idle_connection_is_retried_once_and_a_bad_subscription_does_not_stop_the_rest():
    srv = Svc(("127.0.0.1", 0), FakePushService)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        priv, pub = push.generate_vapid()
        sender = push.Sender(priv, pub, workers=2)
        subs = _subs(port, 4)
        assert all(code == 201 for _, code, _ in sender.send_many(subs, {"title": "a"}))
        # the service drops every idle connection (as FCM / Apple do after a while)
        srv.shutdown(); srv.server_close()
        srv2 = Svc(("127.0.0.1", port), FakePushService)
        srv2.daemon_threads = True
        threading.Thread(target=srv2.serve_forever, daemon=True).start()
        bad = {"endpoint": f"http://127.0.0.1:{port}/wp/x", "keys": {}}
        res = sender.send_many(subs + [bad], {"title": "b"})
        assert [code for _, code, _ in res] == [201, 201, 201, 201, 0]
        srv2.shutdown()
    finally:
        try:
            srv.shutdown()
        except Exception:
            pass


def test_the_server_sends_through_the_pool():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "server.py"), encoding="utf-8").read()
    run = src[src.index("    def _run(self):\n        while True:\n            with self.cond:\n                while not self.q:"):]
    run = run[:run.index("\ndef haversine_km")]
    assert "self.sender.send_many(" in run and "_push.send(" not in run
