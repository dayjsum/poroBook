from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app import db
from app.lobby_harvester import lobby_harvester_loop
from app.matches import enrich_game_payload, mask_for_user, war_room_phase
from app.riot_client import RiotClient, match_id_for_game, winning_team_color

logger = logging.getLogger("porobook")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    settings = get_settings()
    app.state.spectator_raw_cache = {}
    app.state.lobby_snapshot = {
        "matches": [],
        "notice": "Background lobby harvester is starting…",
        "harvested_at": None,
        "harvester_status": "starting",
        "quota_hit": False,
    }
    async with httpx.AsyncClient() as client:
        app.state.http = client
        app.state.settings = settings
        app.state.riot = RiotClient(settings)
        stop = asyncio.Event()

        async def resolver_loop() -> None:
            while not stop.is_set():
                try:
                    await resolve_pending_predictions(app)
                except Exception:
                    logger.exception("resolver tick failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.poll_resolve_seconds)
                except TimeoutError:
                    continue

        resolver_task = asyncio.create_task(resolver_loop())
        harvest_task = asyncio.create_task(lobby_harvester_loop(app, stop, discover_live_games))
        yield
        stop.set()
        harvest_task.cancel()
        resolver_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await harvest_task
        with contextlib.suppress(asyncio.CancelledError):
            await resolver_task


app = FastAPI(title="PoroBook API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RegisterBody(BaseModel):
    username: str = Field(min_length=2, max_length=24)


class PredictionBody(BaseModel):
    game_id: str = Field(min_length=3, max_length=64)
    prediction: str


def user_id_dep(x_user_id: str | None = Header(default=None, alias="X-User-Id")) -> str:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id header")
    return x_user_id


def _discovery_modes(settings: Settings) -> tuple[bool, bool]:
    m = settings.porobook_discovery_mode.lower()
    use_featured = m in ("featured", "both")
    use_seeders = m in ("seeders", "both")
    return use_featured, use_seeders


def _seeder_tag(puuid: str) -> str:
    """Short label for logs/UI (never show full Puuid)."""
    p = puuid.strip()
    if len(p) >= 8:
        return f"…{p[-8:]}"
    return "…(short)"


def _spectator_cache_get(app: FastAPI, game_id: str) -> dict[str, Any] | None:
    cache: dict[str, tuple[dict[str, Any], float]] = getattr(app.state, "spectator_raw_cache", {})
    ent = cache.get(game_id)
    if not ent:
        return None
    raw, exp = ent
    if time.monotonic() > exp:
        del cache[game_id]
        return None
    return copy.deepcopy(raw)


def _spectator_cache_put_one(app: FastAPI, settings: Settings, game_id: str, raw: dict[str, Any]) -> None:
    cache: dict[str, tuple[dict[str, Any], float]] = getattr(app.state, "spectator_raw_cache", None)
    if cache is None:
        app.state.spectator_raw_cache = cache = {}
    now = time.monotonic()
    ttl = float(max(30, settings.spectator_lobby_cache_seconds))
    cache[game_id] = (copy.deepcopy(raw), now + ttl)


async def discover_live_games(
    settings: Settings, riot: RiotClient, client: httpx.AsyncClient
) -> tuple[dict[str, dict], str | None, list[str], bool]:
    """
    Returns (games_by_id, featured_source_error, seeder_notes, quota_hit).
    quota_hit is True if Riot indicated rate limiting (HTTP 429) on featured or seeders this cycle.
    """
    games: dict[str, dict] = {}
    featured_error: str | None = None
    seeder_notes: list[str] = []
    rate_429 = 0
    use_featured, use_seeders = _discovery_modes(settings)

    if use_featured:
        try:
            featured_rows, featured_error = await riot.featured_games(client)
            for raw in featured_rows:
                try:
                    platform = raw.get("platformId") or settings.riot_platform
                    gid = match_id_for_game(str(platform).upper(), raw["gameId"])
                    games[gid] = raw
                except (KeyError, TypeError, ValueError):
                    continue
        except Exception:
            logger.exception("featured games fetch failed")
            featured_error = featured_error or "Featured games request failed; see server logs."

    if use_seeders:
        full_list = settings.seeder_puuid_list
        cap = max(1, settings.seeder_max_lookups_per_poll)
        puuids = full_list[:cap]
        if len(full_list) > len(puuids):
            seeder_notes.append(
                f"Seeder list truncated to the first {len(puuids)} of {len(full_list)} Puuids "
                f"(SEEDER_MAX_LOOKUPS_PER_POLL={cap}). Put accounts you care about first, or raise the cap "
                "knowing Riot may still 429 on development keys."
            )
        delay = max(0.0, settings.seeder_request_delay_seconds)
        skipped_after_429 = 0
        other_seeder_notes: list[str] = []
        for i, puuid in enumerate(puuids):
            if i > 0 and delay > 0:
                await asyncio.sleep(delay)
            tag = _seeder_tag(puuid)
            try:
                raw = await riot.seeder_active_game(client, puuid)
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code == 429:
                    rate_429 += 1
                    skipped_after_429 = len(puuids) - i - 1
                    break
                elif code == 403:
                    other_seeder_notes.append(
                        f"{tag}: HTTP 403 — this key cannot use spectator-v5 on {settings.riot_platform.upper()} "
                        "(League product / key restrictions)."
                    )
                else:
                    other_seeder_notes.append(
                        f"{tag}: HTTP {code} on {settings.riot_platform.upper()} — check PUUID, RIOT_PLATFORM, "
                        "and server logs."
                    )
                continue
            except Exception:
                logger.exception("seeder active game failed tag=%s", tag)
                other_seeder_notes.append(f"{tag}: active game lookup failed (see server logs).")
                continue
            if not raw:
                other_seeder_notes.append(
                    f"{tag}: no active game (404) — champ select, queue, ended, or wrong shard for this PUUID."
                )
                continue
            platform = raw.get("platformId") or settings.riot_platform
            gid = match_id_for_game(str(platform).upper(), raw["gameId"])
            games[gid] = raw
        if rate_429:
            seeder_notes.append(
                "Riot returned HTTP 429 (rate limit) on a seeder lookup — **stopped further seeder calls this poll**"
                + (
                    f" ({skipped_after_429} Puuids not tried)."
                    if skipped_after_429
                    else "."
                )
                + " Development keys are capped at roughly ~100 requests / 2 minutes per platform (featured + "
                "spectator + other calls share the budget). Trim SEEDER_PUUIDS to a few streamers, raise "
                f"SEEDER_REQUEST_DELAY_SECONDS (now {delay}s), and rely on the slower Games auto-refresh."
            )
        seeder_notes.extend(other_seeder_notes)

    quota_hit = rate_429 > 0 or ("429" in (featured_error or ""))
    return games, featured_error, seeder_notes, quota_hit


async def find_spectator_raw_for_game_id(
    settings: Settings,
    riot: RiotClient,
    client: httpx.AsyncClient,
    game_id: str,
) -> dict[str, Any] | None:
    use_featured, use_seeders = _discovery_modes(settings)

    if use_seeders:
        delay = max(0.0, settings.seeder_request_delay_seconds)
        for i, puuid in enumerate(settings.seeder_puuid_list):
            if i > 0 and delay > 0:
                await asyncio.sleep(delay)
            try:
                raw = await riot.seeder_active_game(client, puuid)
            except httpx.HTTPStatusError:
                continue
            except Exception:
                logger.exception("find_spectator seeder active game failed")
                continue
            if not raw:
                continue
            platform = raw.get("platformId") or settings.riot_platform
            gid = match_id_for_game(str(platform).upper(), raw["gameId"])
            if gid == game_id:
                return raw

    if use_featured:
        featured_rows, _ = await riot.featured_games(client)
        for raw in featured_rows:
            try:
                platform = raw.get("platformId") or settings.riot_platform
                gid = match_id_for_game(str(platform).upper(), raw["gameId"])
                if gid == game_id:
                    return raw
            except (KeyError, TypeError, ValueError):
                continue

    return None


async def resolve_pending_predictions(app: FastAPI) -> None:
    riot: RiotClient = app.state.riot
    client: httpx.AsyncClient = app.state.http
    pending = db.list_pending_predictions()
    if not pending:
        return
    for row in pending:
        match = await riot.match_by_id(client, row["game_id"])
        if not match:
            continue
        winner = winning_team_color(match)
        if not winner:
            continue
        correct = row["prediction"] == winner
        db.mark_prediction_result(row["id"], correct)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/users")
async def register_user(body: RegisterBody):
    try:
        user = db.create_user(body.username.strip())
    except Exception as e:
        if "UNIQUE" in str(e).upper():
            raise HTTPException(status_code=409, detail="Username already taken") from e
        raise
    return user


@app.get("/api/me")
async def me(user_id: str = Depends(user_id_dep)):
    settings = get_settings()
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    predictions = db.list_predictions_for_user(user_id)
    score = db.prediction_score(user_id)
    c, r = score["correct"], score["resolved"]
    min_r = settings.leaderboard_min_resolved
    accuracy_pct = round((c / r) * 100.0, 2) if r else None
    eligible = r >= min_r
    rank = db.user_leaderboard_rank(user_id, min_r) if eligible else None
    return {
        "id": user["id"],
        "username": user["username"],
        "prediction_score": score,
        "accuracy_pct": accuracy_pct,
        "leaderboard_eligible": eligible,
        "leaderboard_rank": rank,
        "leaderboard_min_resolved": min_r,
        "predictions": predictions,
    }


@app.get("/api/leaderboard")
async def leaderboard(
    limit: int = Query(default=50, ge=1, le=200),
):
    settings = get_settings()
    rows = db.prediction_leaderboard(settings.leaderboard_min_resolved, limit)
    return {
        "min_resolved": settings.leaderboard_min_resolved,
        "entries": rows,
    }


@app.get("/api/lobby")
async def lobby():
    """Returns the last background-harvest snapshot only (no Riot calls on this request)."""
    snap = getattr(app.state, "lobby_snapshot", None) or {}
    return {
        "matches": snap.get("matches", []),
        "notice": snap.get("notice"),
        "harvested_at": snap.get("harvested_at"),
        "harvester_status": snap.get("harvester_status", "unknown"),
        "quota_hit": bool(snap.get("quota_hit", False)),
    }


@app.get("/api/matches/{game_id}/war-room")
async def war_room(request: Request, game_id: str, user_id: str = Depends(user_id_dep)):
    settings: Settings = app.state.settings
    riot: RiotClient = app.state.riot
    client: httpx.AsyncClient = app.state.http
    if not settings.riot_api_key:
        raise HTTPException(status_code=500, detail="Server missing RIOT_API_KEY")

    existing = db.get_prediction(user_id, game_id)
    user_has_prediction = existing is not None

    found_raw = _spectator_cache_get(request.app, game_id)
    if not found_raw:
        found_raw = await find_spectator_raw_for_game_id(settings, riot, client, game_id)
    if found_raw:
        _spectator_cache_put_one(request.app, settings, game_id, found_raw)

    if not found_raw:
        raise HTTPException(
            status_code=404,
            detail=(
                "Match not in Riot spectator right now (it may have ended or left featured/seeders). "
                "Open Games and pick a match from a fresh refresh — recent lobbies are cached briefly on the server."
            ),
        )

    payload = await enrich_game_payload(settings, riot, client, found_raw)
    phase = war_room_phase(
        int(payload["game_length_seconds"]),
        settings.war_room_cutoff_seconds,
        user_has_prediction,
    )
    return mask_for_user(payload, phase)


@app.post("/api/predictions")
async def create_prediction(body: PredictionBody, user_id: str = Depends(user_id_dep)):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        prediction = db.place_prediction(user_id, body.game_id, body.prediction.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except LookupError:
        raise HTTPException(status_code=404, detail="User not found") from None
    return {"prediction": prediction, "prediction_score": db.prediction_score(user_id)}


@app.get("/api/config/public")
async def public_config():
    s = get_settings()
    return {
        "war_room_cutoff_seconds": s.war_room_cutoff_seconds,
        "leaderboard_min_resolved": s.leaderboard_min_resolved,
        "leaderboard_default_limit": s.leaderboard_default_limit,
        "discovery_mode": s.porobook_discovery_mode,
    }
