"""Signature et verification des `state` OAuth2 et des cookies de session."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Optional


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(txt: str) -> bytes:
    padding = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode(txt + padding)


def _sign(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return _b64e(digest)


def sign_state(payload: dict[str, Any], secret: str, ttl: int = 60 * 60 * 24 * 7) -> str:
    body = {k: v for k, v in payload.items() if v is not None}
    body["exp"] = int(time.time()) + ttl
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{_b64e(raw)}.{_sign(raw, secret)}"


def read_state(token: Optional[str], secret: str) -> Optional[dict[str, Any]]:
    if not token or "." not in token:
        return None
    raw_b64, signature = token.rsplit(".", 1)
    try:
        raw = _b64d(raw_b64)
    except Exception:
        return None
    if not hmac.compare_digest(_sign(raw, secret), signature):
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if int(data.get("exp", 0)) < time.time():
        return None
    return data


def make_session(secret: str, ttl: int = 60 * 60 * 24 * 14) -> str:
    raw = json.dumps({"iat": int(time.time()), "exp": int(time.time()) + ttl}).encode("utf-8")
    return f"{_b64e(raw)}.{_sign(raw, secret)}"


def session_valid(cookie: Optional[str], secret: str) -> bool:
    if not cookie or "." not in cookie:
        return False
    raw_b64, signature = cookie.rsplit(".", 1)
    try:
        raw = _b64d(raw_b64)
    except Exception:
        return False
    if not hmac.compare_digest(_sign(raw, secret), signature):
        return False
    try:
        data = json.loads(raw)
    except Exception:
        return False
    return int(data.get("exp", 0)) >= time.time()
