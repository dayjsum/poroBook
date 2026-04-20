"""
Look up your League PUUID from Riot ID (GameName + tag) for SEEDER_PUUIDS.

Requires in backend/.env:
  RIOT_API_KEY
  RIOT_REGION   (americas | europe | sea | asia — same as Match-v5 routing)

From repo root (poroBook):
  python scripts/fetch_puuid.py "YourGameName" "TAG"

From backend folder:
  python scripts/fetch_puuid.py "YourGameName" "TAG"

Or set in .env temporarily:
  RIOT_GAME_NAME=YourGameName
  RIOT_TAGLINE=TAG
  python scripts/fetch_puuid.py

Your dev key must allow GET /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Load backend/.env when run as: python scripts/fetch_puuid.py
_backend_root = Path(__file__).resolve().parent.parent
_env_path = _backend_root / ".env"
if _env_path.is_file():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def region_host(region: str) -> str:
    r = region.strip().lower()
    hosts = {
        "americas": "americas.api.riotgames.com",
        "europe": "europe.api.riotgames.com",
        "sea": "sea.api.riotgames.com",
        "asia": "asia.api.riotgames.com",
    }
    if r not in hosts:
        raise SystemExit(f"Unknown RIOT_REGION={region!r}. Use one of: {', '.join(hosts)}")
    return hosts[r]


def main() -> None:
    p = argparse.ArgumentParser(description="Resolve PUUID from Riot ID (for SEEDER_PUUIDS).")
    p.add_argument("game_name", nargs="?", default=os.environ.get("RIOT_GAME_NAME", "").strip())
    p.add_argument("tag_line", nargs="?", default=os.environ.get("RIOT_TAGLINE", "").strip())
    args = p.parse_args()

    key = os.environ.get("RIOT_API_KEY", "").strip()
    region = os.environ.get("RIOT_REGION", "americas").strip()

    if not key:
        sys.exit("Missing RIOT_API_KEY in backend/.env")
    if not args.game_name or not args.tag_line:
        sys.exit(
            'Usage:\n  python scripts/fetch_puuid.py "GameName" "TAG"\n'
            "or set RIOT_GAME_NAME and RIOT_TAGLINE in backend/.env\n"
            'Example: Name from "Name#NA1" and tag NA1'
        )

    host = region_host(region)
    gn, tg = quote(args.game_name, safe=""), quote(args.tag_line, safe="")
    url = f"https://{host}/riot/account/v1/accounts/by-riot-id/{gn}/{tg}"
    # Default Python urllib User-Agent is often blocked by Cloudflare (403 + error code 1010).
    headers = {
        "X-Riot-Token": key,
        "Accept": "application/json",
        "User-Agent": "PoroBook/1.0 (local PUUID helper; Riot developer API)",
    }
    req = Request(url, headers=headers)

    try:
        with urlopen(req, timeout=20) as resp:
            body = resp.read().decode()
            data = json.loads(body)
    except HTTPError as e:
        detail = (e.read().decode() if e.fp else str(e))[:800]
        extra = ""
        if e.code == 403 and "1010" in detail:
            extra = (
                "\n\nNote: 'error code: 1010' is usually Cloudflare blocking the request (not Riot's JSON body). "
                "Try again after this script update (custom User-Agent), or run the same URL from the "
                "Riot Developer Portal 'Try it out', or use curl/PowerShell from home internet (not a blocked VPN/datacenter IP).\n"
            )
        sys.exit(
            f"HTTP {e.code}: {detail}{extra}\n"
            "Also verify: RIOT_REGION (americas for NA), Riot ID spelling, and account-v1 enabled on your API key."
        )
    except URLError as e:
        sys.exit(f"Network error: {e.reason}")

    puuid = data.get("puuid")
    if not puuid:
        sys.exit(f"Unexpected response (no puuid): {body[:500]}")

    print("PUUID (copy into SEEDER_PUUIDS in .env):\n")
    print(puuid)
    print("\nExample .env line:")
    print(f"SEEDER_PUUIDS={puuid}")
    print("\nKeep RIOT_PLATFORM set to the shard where this account plays (e.g. na1, euw1).")


if __name__ == "__main__":
    main()
