"""
Build a SEEDER_PUUIDS= line by sampling ranked ladder entries across tiers (any ELO).

Uses league-v4 + summoner-v4 on your platform shard. Respect Riot rate limits (sleeps between calls).

Requires backend/.env:
  RIOT_API_KEY
  RIOT_PLATFORM  (e.g. na1, euw1)

Run from repo root:
  python scripts/sample_ladder_puuids.py --max 40

Or from backend:
  python scripts/sample_ladder_puuids.py --max 40

Your API key must allow league-v4 and summoner-v4.

Note: For "random live games" without maintaining a PUUID list, prefer POROBOOK_DISCOVERY_MODE=featured
(spectator featured-games) instead of this script.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

_backend_root = Path(__file__).resolve().parent.parent
_env_backend = _backend_root / ".env"
_env_repo = _backend_root.parent / ".env"
for _env_path in (_env_backend, _env_repo):
    if _env_path.is_file():
        for line in _env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v

QUEUE = "RANKED_SOLO_5x5"
TIERS_DIV = [
    ("IRON", "IV"),
    ("IRON", "III"),
    ("IRON", "II"),
    ("IRON", "I"),
    ("BRONZE", "IV"),
    ("BRONZE", "III"),
    ("BRONZE", "II"),
    ("BRONZE", "I"),
    ("SILVER", "IV"),
    ("SILVER", "III"),
    ("SILVER", "II"),
    ("SILVER", "I"),
    ("GOLD", "IV"),
    ("GOLD", "III"),
    ("GOLD", "II"),
    ("GOLD", "I"),
    ("PLATINUM", "IV"),
    ("PLATINUM", "III"),
    ("PLATINUM", "II"),
    ("PLATINUM", "I"),
    ("EMERALD", "IV"),
    ("EMERALD", "III"),
    ("EMERALD", "II"),
    ("EMERALD", "I"),
    ("DIAMOND", "IV"),
    ("DIAMOND", "III"),
    ("DIAMOND", "II"),
    ("DIAMOND", "I"),
]
TOP_LEAGUES = [
    f"/lol/league/v4/masterleagues/by-queue/{QUEUE}",
    f"/lol/league/v4/grandmasterleagues/by-queue/{QUEUE}",
    f"/lol/league/v4/challengerleagues/by-queue/{QUEUE}",
]


def _get_json(url: str, token: str, verbose: bool) -> dict | list | None:
    req = Request(
        url,
        headers={
            "X-Riot-Token": token,
            "Accept": "application/json",
            "User-Agent": "PoroBook/1.0 (ladder sample script)",
        },
    )
    try:
        with urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        body = e.read().decode() if e.fp else ""
        msg = f"HTTP {e.code} {body[:300]}"
        print(f"WARN {url}\n  {msg}", file=sys.stderr)
        if verbose:
            print(f"WARN {url}\n  {msg}")
        return None


def summoner_puuid(platform_host: str, token: str, summoner_id: str, verbose: bool) -> str | None:
    url = f"{platform_host}/lol/summoner/v4/summoners/{summoner_id}"
    data = _get_json(url, token, verbose)
    if isinstance(data, dict) and data.get("puuid"):
        return str(data["puuid"])
    return None


def _puuid_from_entry(ent: dict, host: str, key: str, sleep_s: float, verbose: bool) -> str | None:
    """League entries often include `puuid` directly; older payloads need summoner-v4 by summonerId."""
    raw = ent.get("puuid")
    if isinstance(raw, str) and len(raw) > 50:
        time.sleep(0.05)
        return raw
    sid = ent.get("summonerId")
    if not sid:
        if verbose:
            print(f"  skip entry (no puuid, no summonerId): keys={list(ent.keys())[:12]}")
        return None
    puuid = summoner_puuid(host, key, str(sid), verbose)
    time.sleep(sleep_s)
    return puuid


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=50, help="Max PUUIDs to collect")
    ap.add_argument("--sleep", type=float, default=1.25, help="Seconds between API calls (after summoner lookup)")
    ap.add_argument("-v", "--verbose", action="store_true", help="Print HTTP warnings to stdout too")
    args = ap.parse_args()

    key = os.environ.get("RIOT_API_KEY", "").strip()
    plat = os.environ.get("RIOT_PLATFORM", "na1").strip().lower()
    if not key:
        sys.exit("Missing RIOT_API_KEY in backend/.env (path must be backend/.env when using repo-root wrapper).")
    host = f"https://{plat}.api.riotgames.com"
    if args.verbose:
        print(f"Using platform host {host} (--max {args.max})")

    seen: set[str] = set()
    out: list[str] = []

    def take_from_entries(entries: list[dict]) -> None:
        nonlocal out
        random.shuffle(entries)
        for ent in entries:
            if len(out) >= args.max:
                return
            puuid = _puuid_from_entry(ent, host, key, args.sleep, args.verbose)
            if puuid and puuid not in seen:
                seen.add(puuid)
                out.append(puuid)

    # Top leagues (master / GM / challenger)
    for path in TOP_LEAGUES:
        if len(out) >= args.max:
            break
        data = _get_json(host + path, key, args.verbose)
        time.sleep(args.sleep)
        if not isinstance(data, dict):
            continue
        entries = data.get("entries")
        if isinstance(entries, list) and entries and args.verbose:
            print(f"  {path}: {len(entries)} entries")
        if isinstance(entries, list):
            take_from_entries([dict(x) for x in entries])

    # Tier / division ladder pages (page 0 only per cell to limit calls)
    cells = list(TIERS_DIV)
    random.shuffle(cells)
    for tier, div in cells:
        if len(out) >= args.max:
            break
        url = f"{host}/lol/league/v4/entries/{QUEUE}/{tier}/{div}"
        data = _get_json(url, key, args.verbose)
        time.sleep(args.sleep)
        if isinstance(data, list) and data and args.verbose:
            print(f"  entries {tier}/{div}: {len(data)} rows")
        if not isinstance(data, list):
            continue
        take_from_entries([dict(x) for x in data])

    if not out:
        sys.exit(
            "No PUUIDs collected.\n"
            "  • On https://developer.riotgames.com/ enable **league-v4** and **summoner-v4** for this product.\n"
            "  • Set RIOT_PLATFORM in backend/.env to your key’s shard (e.g. na1, euw1).\n"
            "  • Run again with **--verbose** and check HTTP codes above (403 = wrong product or Cloudflare).\n"
            "  • For random live games without this list, use POROBOOK_DISCOVERY_MODE=featured instead."
        )

    line = "SEEDER_PUUIDS=" + ",".join(out)
    print("\nPaste into backend/.env (one line):\n")
    print(line)
    print(f"\nCollected {len(out)} PUUID(s). Use with POROBOOK_DISCOVERY_MODE=seeders or both.")


if __name__ == "__main__":
    main()
