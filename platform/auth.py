"""
Clep auth — Firestore-backed accounts + self-serve API keys.

Users collection, doc ID = lowercased email (so Firestore's create() gives us
atomic "email already taken" rejection for free — no separate uniqueness
check needed). Session tokens are stateless (HMAC over user_id + expiry, no
session store) so verifying one never needs a Firestore read; looking up the
*current* user (name/email/api_key) still does, since those can change.

Requires CLEP_AUTH_SECRET (HMAC signing key) and a GCP project with a
Firestore Native database + the runtime service account granted
roles/datastore.user. Locally, Application Default Credentials must be
present (gcloud auth application-default login) or signup/login just fail
loudly — there's no local fallback store, by design, so dev and prod share
one code path.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time

from google.cloud import firestore

_PBKDF2_ITERATIONS = 200_000
_TOKEN_TTL_SECONDS = 30 * 24 * 3600  # 30 days
_API_KEY_CACHE_TTL = 30  # seconds — avoid a Firestore read per /api/* call

_db: firestore.Client | None = None
_api_key_cache: dict[str, tuple[float, dict | None]] = {}


def _client() -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


def _auth_secret() -> bytes:
    secret = os.environ.get("CLEP_AUTH_SECRET", "")
    if not secret:
        raise RuntimeError("CLEP_AUTH_SECRET is not set — required for signup/login")
    return secret.encode()


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return base64.b64encode(salt).decode(), base64.b64encode(digest).decode()


def _verify_password(password: str, salt_b64: str, hash_b64: str) -> bool:
    salt = base64.b64decode(salt_b64)
    _, candidate = _hash_password(password, salt)
    return hmac.compare_digest(candidate, hash_b64)


def _make_token(user_id: str) -> str:
    expiry = int(time.time()) + _TOKEN_TTL_SECONDS
    payload = f"{user_id}:{expiry}"
    sig = hmac.new(_auth_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{user_id}.{expiry}.{sig}"


def _verify_token(token: str) -> str | None:
    try:
        # rsplit, not split: user_id is an email address and always contains
        # its own "." (the domain's TLD) — only expiry/sig are guaranteed
        # dot-free, so splitting from the right is what correctly isolates them.
        user_id, expiry_s, sig = token.rsplit(".", 2)
        expiry = int(expiry_s)
    except (ValueError, AttributeError):
        return None
    if time.time() > expiry:
        return None
    payload = f"{user_id}:{expiry}"
    expected = hmac.new(_auth_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    return user_id


def _new_api_key() -> str:
    return f"clep_live_{secrets.token_hex(20)}"


def _public(doc_id: str, data: dict) -> dict:
    return {"email": doc_id, "name": data.get("name", ""), "api_key": data["api_key"]}


def create_user(email: str, password: str, name: str) -> dict:
    """Returns {token, api_key, name}. Raises ValueError if the email is taken."""
    doc_id = email.strip().lower()
    salt_b64, hash_b64 = _hash_password(password)
    api_key = _new_api_key()
    doc_ref = _client().collection("users").document(doc_id)
    try:
        doc_ref.create({
            "email": doc_id,
            "name": name,
            "password_salt": salt_b64,
            "password_hash": hash_b64,
            "api_key": api_key,
            "created": time.time(),
        })
    except Exception as e:  # noqa: BLE001 — google.cloud.exceptions.Conflict on duplicate create()
        if "already exists" in str(e).lower() or type(e).__name__ == "Conflict":
            raise ValueError("an account with that email already exists") from e
        raise
    return {"token": _make_token(doc_id), "api_key": api_key, "name": name}


def verify_login(email: str, password: str) -> dict | None:
    """Returns {token, api_key, name} or None if the credentials are wrong."""
    doc_id = email.strip().lower()
    snap = _client().collection("users").document(doc_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict()
    if not _verify_password(password, data["password_salt"], data["password_hash"]):
        return None
    return {"token": _make_token(doc_id), "api_key": data["api_key"], "name": data.get("name", "")}


def get_user_by_token(token: str) -> dict | None:
    user_id = _verify_token(token)
    if not user_id:
        return None
    snap = _client().collection("users").document(user_id).get()
    if not snap.exists:
        return None
    return _public(user_id, snap.to_dict())


def get_user_by_api_key(api_key: str) -> dict | None:
    now = time.monotonic()
    cached = _api_key_cache.get(api_key)
    if cached and now - cached[0] < _API_KEY_CACHE_TTL:
        return cached[1]
    q = _client().collection("users").where("api_key", "==", api_key).limit(1).get()
    result = _public(q[0].id, q[0].to_dict()) if q else None
    _api_key_cache[api_key] = (now, result)
    return result
