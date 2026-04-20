from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings
from app.riot_client import (
    RiotClient,
    anonymize_active_game,
    demo_team_stats,
    match_id_for_game,
    queue_label,
    rank_bracket_text,
)


def _platform_for_game(settings: Settings, raw: dict[str, Any]) -> str:
    pid = raw.get("platformId")
    if isinstance(pid, str) and pid:
        return pid.upper()
    return settings.riot_platform.upper()


async def enrich_game_payload(
    settings: Settings,
    riot: RiotClient,
    client: httpx.AsyncClient,
    raw: dict[str, Any],
) -> dict[str, Any]:
    platform = _platform_for_game(settings, raw)
    game_id = match_id_for_game(platform, raw["gameId"])
    anon = anonymize_active_game(raw)
    queue_id = raw.get("gameQueueConfigId")
    game_length = int(raw.get("gameLength") or 0)

    blue_champs: list[dict[str, Any]] = []
    red_champs: list[dict[str, Any]] = []
    for p in raw.get("participants", []):
        cid = int(p.get("championId") or 0)
        icon = await riot.champion_icon_url(client, cid)
        entry = {"championId": cid, "iconUrl": icon}
        tid = int(p.get("teamId") or 0)
        if tid == 100:
            blue_champs.append(entry)
        elif tid == 200:
            red_champs.append(entry)

    live = extract_public_live_stats(raw.get("participants", []))
    if live is None and settings.porobook_demo_stats:
        b_stats, r_stats = demo_team_stats(game_id, game_length)
        live = {"blue": b_stats, "red": r_stats, "source": "demo_synthetic"}

    if live is None:
        live_stats_note = (
            "Riot’s Spectator snapshot did not publish team totals for this queue. "
            "Enable POROBOOK_DEMO_STATS on the server for deterministic demo numbers while you wire UI."
        )
    elif live.get("source") == "demo_synthetic":
        live_stats_note = "Synthetic numbers for UI polish only; outcomes still resolve from Match-v5."
    else:
        live_stats_note = None

    return {
        "game_id": game_id,
        "queue": queue_label(queue_id if isinstance(queue_id, int) else None),
        "rank_bracket": rank_bracket_text(queue_id if isinstance(queue_id, int) else None),
        "game_length_seconds": game_length,
        "game_start_time": raw.get("gameStartTime"),
        "map_id": raw.get("mapId"),
        "blue": {"champions": blue_champs},
        "red": {"champions": red_champs},
        "snapshot": anon,
        "live_stats": live,
        "live_stats_note": live_stats_note,
    }


def extract_public_live_stats(participants: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Attempt to aggregate any non-identifying numeric stats Riot includes on participants.
    If nothing is found, returns None (caller may enable demo synthesis).
    """
    blue = {"gold": 0, "kills": 0, "towers": 0, "dragons": 0}
    red = {"gold": 0, "kills": 0, "towers": 0, "dragons": 0}
    found = False

    def add(team: dict[str, int], payload: dict[str, Any]) -> None:
        nonlocal found
        mapping = (
            ("gold", ("goldEarned", "totalGold", "gold")),
            ("kills", ("kills", "championKills")),
            ("towers", ("turretKills", "towerKills")),
            ("dragons", ("dragonKills",)),
        )
        for out_key, candidates in mapping:
            for c in candidates:
                if c in payload and isinstance(payload[c], (int, float)):
                    team[out_key] += int(payload[c])
                    found = True
                    break

    for p in participants:
        tid = int(p.get("teamId") or 0)
        team = blue if tid == 100 else red if tid == 200 else None
        if team is None:
            continue
        for key in ("scores", "gameStats", "stats"):
            nested = p.get(key)
            if isinstance(nested, dict):
                add(team, nested)
    if not found:
        return None
    return {"blue": blue, "red": red, "source": "riot_snapshot"}


def war_room_phase(game_length: int, cutoff: int, user_has_prediction: bool) -> str:
    if user_has_prediction:
        return "LOCKED"
    if game_length >= cutoff:
        return "LOCKOUT"
    return "ANALYSIS"


def mask_for_user(payload: dict[str, Any], phase: str) -> dict[str, Any]:
    base = {
        "game_id": payload["game_id"],
        "queue": payload["queue"],
        "rank_bracket": payload["rank_bracket"],
        "game_length_seconds": payload["game_length_seconds"],
        "game_start_time": payload.get("game_start_time"),
        "blue": {"champions": payload["blue"]["champions"]},
        "red": {"champions": payload["red"]["champions"]},
        "phase": phase,
        "spectator_delay_note": (
            "Spectator data is delayed ~3 minutes vs the live client. "
            "The 5:00 analysis window uses the delayed game clock from Riot."
        ),
    }
    if phase == "ANALYSIS":
        base["live_stats"] = payload.get("live_stats")
        base["live_stats_note"] = payload.get("live_stats_note")
    else:
        base["live_stats"] = None
        base["live_stats_note"] = (
            "Live totals are hidden after 5:01 on the delayed clock or once you lock a prediction."
        )
    return base
