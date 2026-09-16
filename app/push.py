"""Web Push (RFC 8291 aes128gcm + RFC 8292 VAPID) with only the `cryptography` package.
Used by server.py to wake the phone even when the page is closed (Android Chrome / desktop; iOS needs the
page added to the home screen). If `cryptography` is missing, push is simply disabled."""
import base64
import json
import os
import struct
import time
import urllib.error
import urllib.request

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    AVAILABLE = True
except Exception:   # pragma: no cover
    AVAILABLE = False


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s):
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def generate_vapid():
    """Returns (private_key_b64url_raw32, public_key_b64url_uncompressed65)."""
    k = ec.generate_private_key(ec.SECP256R1())
    priv = k.private_numbers().private_value.to_bytes(32, "big")
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64u(priv), b64u(pub)


def _load_priv(priv_b64):
    return ec.derive_private_key(int.from_bytes(b64u_dec(priv_b64), "big"), ec.SECP256R1())


def vapid_headers(endpoint, priv_b64, pub_b64, subject):
    from urllib.parse import urlparse
    u = urlparse(endpoint)
    aud = f"{u.scheme}://{u.netloc}"
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = b64u(json.dumps({"aud": aud, "exp": int(time.time()) + 12 * 3600, "sub": subject}).encode())
    signing = f"{header}.{claims}".encode()
    der = _load_priv(priv_b64).sign(signing, ec.ECDSA(hashes.SHA256()))
    r, s_ = decode_dss_signature(der)
    sig = b64u(r.to_bytes(32, "big") + s_.to_bytes(32, "big"))
    return {"Authorization": f"vapid t={header}.{claims}.{sig}, k={pub_b64}"}


def encrypt(payload, p256dh_b64, auth_b64):
    """RFC 8291 aes128gcm content encoding. Returns the body bytes (salt|rs|idlen|pubkey|ciphertext)."""
    ua_pub = b64u_dec(p256dh_b64)
    auth = b64u_dec(auth_b64)
    as_key = ec.generate_private_key(ec.SECP256R1())
    as_pub = as_key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = as_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = HKDF(hashes.SHA256(), 32, salt=auth, info=b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    salt = os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    rs = 4096
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)     # single record, padding delimiter 0x02
    return salt + struct.pack(">I", rs) + bytes([len(as_pub)]) + as_pub + ct


def send(subscription, payload, priv_b64, pub_b64, subject="mailto:clear-sky@example.com", ttl=300, urgency="high"):
    """subscription: the browser's PushSubscription JSON. Returns (status_code, gone) — gone=True when the
    subscription is dead (404/410) and should be deleted."""
    endpoint = subscription["endpoint"]
    keys = subscription.get("keys") or {}
    body = encrypt(json.dumps(payload).encode("utf-8"), keys["p256dh"], keys["auth"])
    headers = {"Content-Type": "application/octet-stream", "Content-Encoding": "aes128gcm", "TTL": str(ttl), "Urgency": urgency}
    headers.update(vapid_headers(endpoint, priv_b64, pub_b64, subject))
    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, False
    except urllib.error.HTTPError as e:
        return e.code, e.code in (404, 410)
    except Exception:
        return 0, False


if __name__ == "__main__":
    priv, pub = generate_vapid()
    print("VAPID public:", pub)
    print("encrypt ok:", len(encrypt(b'{"a":1}', b64u(ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)), b64u(os.urandom(16)))))
