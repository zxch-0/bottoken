"""Petit script a lancer en local ou dans un cron pour empecher le service
gratuit Render de s'endormir (et donc le bot Discord de se deconnecter).

Usage :

    python scripts/keepalive.py https://mon-service.onrender.com
"""

from __future__ import annotations

import sys
import time

import httpx

INTERVAL_SECONDS = 240


def ping(url: str) -> bool:
    target = url.rstrip("/") + "/healthz"
    try:
        response = httpx.get(target, timeout=30)
    except httpx.HTTPError as exc:
        print(f"[keepalive] erreur {target}: {exc}")
        return False
    ok = response.status_code == 200
    print(f"[keepalive] {target} -> {response.status_code}")
    return ok


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/keepalive.py <base_url>")
        return 1
    url = sys.argv[1]
    failures = 0
    while True:
        if ping(url):
            failures = 0
        else:
            failures += 1
        time.sleep(INTERVAL_SECONDS if failures < 3 else 60)


if __name__ == "__main__":
    main()
