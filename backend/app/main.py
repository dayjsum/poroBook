from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app import db
from app.matches import enrich_game_payload, mask_for_user, war_room_phase
from app.riot_client import RiotClient, match_id_for_game, winning_team_color

logger = logging.getLogger("porobook")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    settings = get_settings()
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

        task = asyncio.create_task(resolver_loop())
        yield
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


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


async def discover_live_games(settings: Settings, riot: RiotClient, client: httpx.AsyncClient) -> dict[str, dict]:
    games: dict[str, dict] = {}
    use_featured, use_seeders = _discovery_modes(settings)

    if use_featured:
        try:
            for raw in await riot.featured_games(client):
                try:
                    platform = raw.get("platformId") or settings.riot_platform
                    gid = match_id_for_game(str(platform).upper(), raw["gameId"])
                    games[gid] = raw
                except (KeyError, TypeError, ValueError):
                    continue
        except Exception:
            logger.exception("featured games fetch failed")

    if use_seeders:
        for puuid in settings.seeder_puuid_list:
            summoner_id = await riot.summoner_id_by_puuid(client, puuid)
            if not summoner_id:
                continue
            raw = await riot.active_game(client, summoner_id)
            if not raw:
                continue
            platform = raw.get("platformId") or settings.riot_platform
            gid = match_id_for_game(str(platform).upper(), raw["gameId"])
            games[gid] = raw

    return games


async def find_spectator_raw_for_game_id(
    settings: Settings,
    riot: RiotClient,
    client: httpx.AsyncClient,
    game_id: str,
) -> dict[str, Any] | None:
    use_featured, use_seeders = _discovery_modes(settings)

    if use_seeders:
        for puuid in settings.seeder_puuid_list:
            summoner_id = await riot.summoner_id_by_puuid(client, puuid)
            if not summoner_id:
                continue
            raw = await riot.active_game(client, summoner_id)
            if not raw:
                continue
            platform = raw.get("platformId") or settings.riot_platform
            gid = match_id_for_game(str(platform).upper(), raw["gameId"])
            if gid == game_id:
                return raw

    if use_featured:
        for raw in await riot.featured_games(client):
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
    settings: Settings = app.state.settings
    riot: RiotClient = app.state.riot
    client: httpx.AsyncClient = app.state.http
    if not settings.riot_api_key:
        raise HTTPException(status_code=500, detail="Server missing RIOT_API_KEY")

    use_featured, use_seeders = _discovery_modes(settings)
    if use_seeders and not settings.seeder_puuid_list and not use_featured:
        return {
            "matches": [],
            "notice": "Set POROBOOK_DISCOVERY_MODE=featured or add SEEDER_PUUIDS for seeders/both mode.",
        }

    raw_games = await discover_live_games(settings, riot, client)
    if not raw_games:
        notice = (
            "No live games right now. Featured list may be empty, or seeders are offline — try again in a minute."
            if use_featured
            else "No matches from seeders (they may be in queue or offline)."
        )
        return {"matches": [], "notice": notice}

    shuffled = list(raw_games.values())
    random.shuffle(shuffled)
    cards: list[dict[str, Any]] = []
    for raw in shuffled:
        cards.append(await enrich_game_payload(settings, riot, client, raw))
    return {"matches": cards}


@app.get("/api/matches/{game_id}/war-room")
async def war_room(game_id: str, user_id: str = Depends(user_id_dep)):
    settings: Settings = app.state.settings
    riot: RiotClient = app.state.riot
    client: httpx.AsyncClient = app.state.http
    if not settings.riot_api_key:
        raise HTTPException(status_code=500, detail="Server missing RIOT_API_KEY")

    existing = db.get_prediction(user_id, game_id)
    user_has_prediction = existing is not None

    found_raw = await find_spectator_raw_for_game_id(settings, riot, client, game_id)

    if not found_raw:
        raise HTTPException(
            status_code=404,
            detail="Match not in featured games or seeders (it may have ended). Refresh the games list.",
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
