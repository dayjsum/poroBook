"""
Background harvest of live spectator games into app.state.lobby_snapshot.

GET /api/lobby reads only this snapshot so the UI and user clicks never trigger Riot directly.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx
from fastapi import FastAPI

from app.config import Settings
from app.matches import enrich_game_payload
from app.riot_client import RiotClient

logger = logging.getLogger("porobook")

DiscoverFn = Callable[
    [Settings, RiotClient, httpx.AsyncClient],
    Awaitable[tuple[dict[str, dict[str, Any]], str | None, list[str], bool]],
]


def _discovery_modes(settings: Settings) -> tuple[bool, bool]:
    m = settings.porobook_discovery_mode.lower()
    use_featured = m in ("featured", "both")
    use_seeders = m in ("seeders", "both")
    return use_featured, use_seeders


def _build_empty_notice(
    settings: Settings,
    use_featured: bool,
    use_seeders: bool,
    featured_err: str | None,
    seeder_notes: list[str],
) -> str:
    chunks: list[str] = []
    if settings.seeder_puuid_list and not use_seeders:
        chunks.append(
            "SEEDER_PUUIDS is set but POROBOOK_DISCOVERY_MODE is 'featured' only, so those Puuids are never queried. "
            "Set it to 'both' or 'seeders'."
        )
    if use_featured:
        if featured_err:
            chunks.append(featured_err)
        else:
            chunks.append(
                "Riot returned an empty featured spectator list for this platform — that is normal at quiet times. "
                "It is not a catalog of every live match in the world, only a short snapshot for your shard "
                f"({settings.riot_platform}). Try peak hours or another RIOT_PLATFORM if you meant a different region."
            )
    elif use_seeders:
        chunks.append("No matches from seeders (they may be in queue or offline).")
    notice = "\n\n".join(chunks) if chunks else "No games found."
    if seeder_notes:
        bullets = "\n".join(f"• {line}" for line in seeder_notes)
        notice = f"{notice}\n\nSeeder checks:\n{bullets}"
    return notice


def _merge_spectator_raw_cache(
    app: FastAPI, settings: Settings, raw_games: dict[str, dict[str, Any]]
) -> None:
    cache: dict[str, tuple[dict[str, Any], float]] = getattr(app.state, "spectator_raw_cache", None)
    if cache is None:
        app.state.spectator_raw_cache = cache = {}
    now = time.monotonic()
    ttl = float(max(30, settings.spectator_lobby_cache_seconds))
    for k in list(cache.keys()):
        if cache[k][1] <= now:
            del cache[k]
    for gid, raw in raw_games.items():
        cache[gid] = (copy.deepcopy(raw), now + ttl)
    while len(cache) > 150:
        oldest = min(cache.items(), key=lambda kv: kv[1][1])[0]
        del cache[oldest]


async def run_lobby_harvest_once(app: FastAPI, discover_fn: DiscoverFn) -> None:
    settings: Settings = app.state.settings
    riot: RiotClient = app.state.riot
    client: httpx.AsyncClient = app.state.http

    if not settings.riot_api_key:
        app.state.lobby_snapshot = {
            "matches": [],
            "notice": "Server missing RIOT_API_KEY.",
            "harvested_at": datetime.now(timezone.utc).isoformat(),
            "harvester_status": "misconfigured",
            "quota_hit": False,
        }
        return

    use_featured, use_seeders = _discovery_modes(settings)
    if use_seeders and not settings.seeder_puuid_list and not use_featured:
        app.state.lobby_snapshot = {
            "matches": [],
            "notice": "Set POROBOOK_DISCOVERY_MODE=featured or add SEEDER_PUUIDS for seeders/both mode.",
            "harvested_at": datetime.now(timezone.utc).isoformat(),
            "harvester_status": "misconfigured",
            "quota_hit": False,
        }
        return

    try:
        raw_games, featured_err, seeder_notes, quota_hit = await discover_fn(settings, riot, client)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429:
            logger.warning("Lobby harvest: Riot HTTP 429 — backing off until next scheduled cycle.")
            prev = dict(getattr(app.state, "lobby_snapshot", {}) or {})
            app.state.lobby_snapshot = {
                "matches": prev.get("matches", []),
                "notice": prev.get("notice"),
                "harvested_at": datetime.now(timezone.utc).isoformat(),
                "harvester_status": "rate_limited",
                "quota_hit": True,
            }
            return
        logger.exception("Lobby harvest: HTTP error from Riot")
        raise

    if featured_err and "403" in featured_err and "spectator" in featured_err.lower():
        logger.warning(
            "Lobby harvest: Riot returned 403 on spectator featured games — "
            "development API key likely missing League product, expired, or wrong app. Check developer.riotgames.com."
        )

    if not raw_games:
        notice = _build_empty_notice(settings, use_featured, use_seeders, featured_err, seeder_notes)
        app.state.lobby_snapshot = {
            "matches": [],
            "notice": notice,
            "harvested_at": datetime.now(timezone.utc).isoformat(),
            "harvester_status": "empty",
            "quota_hit": quota_hit,
        }
        return

    _merge_spectator_raw_cache(app, settings, raw_games)
    shuffled = list(raw_games.values())
    random.shuffle(shuffled)
    cards: list[dict[str, Any]] = []
    for raw in shuffled:
        cards.append(await enrich_game_payload(settings, riot, client, raw))

    app.state.lobby_snapshot = {
        "matches": cards,
        "notice": None,
        "harvested_at": datetime.now(timezone.utc).isoformat(),
        "harvester_status": "ok",
        "quota_hit": quota_hit,
    }


async def lobby_harvester_loop(app: FastAPI, stop: asyncio.Event, discover_fn: DiscoverFn) -> None:
    settings: Settings = app.state.settings
    base = max(30.0, float(getattr(settings, "lobby_harvest_interval_seconds", 75)))
    jitter = max(0.0, float(getattr(settings, "lobby_harvest_jitter_seconds", 15)))

    while not stop.is_set():
        try:
            await run_lobby_harvest_once(app, discover_fn)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning("Lobby harvest cycle hit HTTP 429 — waiting for next interval.")
            else:
                logger.exception("Lobby harvest cycle HTTP error")
        except Exception:
            logger.exception("Lobby harvest cycle failed")

        snap = getattr(app.state, "lobby_snapshot", {}) or {}
        featured_err = (snap.get("notice") or "") if isinstance(snap.get("notice"), str) else ""
        spectator403 = "403" in featured_err and "spectator" in featured_err.lower()
        quota = bool(snap.get("quota_hit"))

        if spectator403:
            sleep_s = 300.0
        elif quota:
            sleep_s = max(base + random.uniform(-jitter, jitter), 120.0)
        else:
            sleep_s = base + random.uniform(-jitter, jitter)
        sleep_s = max(15.0, sleep_s)

        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_s)
        except TimeoutError:
            continue
