from __future__ import annotations

import hashlib
import math
from typing import Any

import httpx

from app.config import Settings

QUEUE_NAMES: dict[int, str] = {
    0: "Custom",
    420: "Ranked Solo/Duo",
    440: "Ranked Flex",
    400: "Normal Draft",
    430: "Normal Blind",
    450: "ARAM",
    700: "Clash",
    900: "URF",
    1020: "One for All",
    1300: "Nexus Blitz",
    1400: "Ultimate Spellbook",
    1700: "Arena",
    1900: "Pick URF",
}


def queue_label(queue_id: int | None) -> str:
    if queue_id is None:
        return "Unknown queue"
    return QUEUE_NAMES.get(queue_id, f"Queue {queue_id}")


def rank_bracket_text(queue_id: int | None) -> str:
    if queue_id in (420, 440):
        return "Ranked · skill varies"
    if queue_id in (400, 430, 450):
        return "Normal / ARAM"
    return "Live match"


def match_id_for_game(platform: str, game_id: int | str) -> str:
    p = platform.upper()
    return f"{p}_{game_id}"


class RiotClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._ddragon_version: str | None = settings.ddragon_version
        self._champion_icon: dict[int, str] = {}

    def _headers(self) -> dict[str, str]:
        return {"X-Riot-Token": self.settings.riot_api_key}

    async def _get(self, client: httpx.AsyncClient, url: str) -> httpx.Response:
        return await client.get(url, headers=self._headers(), timeout=20.0)

    async def ensure_ddragon(self, client: httpx.AsyncClient) -> str:
        if self._ddragon_version:
            return self._ddragon_version
        r = await client.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=15.0)
        r.raise_for_status()
        versions = r.json()
        self._ddragon_version = versions[0]
        return self._ddragon_version

    async def champion_icon_url(self, client: httpx.AsyncClient, champion_id: int) -> str:
        if champion_id in self._champion_icon:
            return self._champion_icon[champion_id]
        version = await self.ensure_ddragon(client)
        champ_r = await client.get(
            f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json",
            timeout=20.0,
        )
        champ_r.raise_for_status()
        data = champ_r.json()["data"]
        for _, payload in data.items():
            key = int(payload["key"])
            icon = payload["image"]["full"]
            self._champion_icon[key] = (
                f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/{icon}"
            )
        return self._champion_icon.get(
            champion_id,
            f"https://ddragon.leagueoflegends.com/cdn/{version}/img/champion/Aatrox.png",
        )

    async def summoner_id_by_puuid(self, client: httpx.AsyncClient, puuid: str) -> str | None:
        url = f"{self.settings.platform_host}/lol/summoner/v4/summoners/by-puuid/{puuid}"
        r = await self._get(client, url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return str(r.json()["id"])

    async def active_game(self, client: httpx.AsyncClient, encrypted_summoner_id: str) -> dict | None:
        url = f"{self.settings.platform_host}/lol/spectator/v4/active-games/by-summoner/{encrypted_summoner_id}"
        r = await self._get(client, url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def featured_games(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        """Spectator featured game list (mixed MMR live matches on the shard)."""
        url = f"{self.settings.platform_host}/lol/spectator/v4/featured-games"
        r = await self._get(client, url)
        if r.status_code in (403, 404):
            return []
        r.raise_for_status()
        data = r.json()
        raw_list = data.get("gameList")
        if not isinstance(raw_list, list):
            return []
        return [dict(x) for x in raw_list]

    async def match_by_id(self, client: httpx.AsyncClient, match_id: str) -> dict | None:
        url = f"{self.settings.region_host}/lol/match/v5/matches/{match_id}"
        r = await self._get(client, url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


def _strip_participant(p: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "teamId",
        "championId",
        "spell1Id",
        "spell2Id",
        "perks",
        "profileIconId",
        "bot",
    }
    return {k: p[k] for k in allowed if k in p}


def anonymize_active_game(raw: dict[str, Any]) -> dict[str, Any]:
    participants = [_strip_participant(dict(x)) for x in raw.get("participants", [])]
    out = {
        "gameId": raw.get("gameId"),
        "mapId": raw.get("mapId"),
        "gameMode": raw.get("gameMode"),
        "gameType": raw.get("gameType"),
        "gameQueueConfigId": raw.get("gameQueueConfigId"),
        "gameStartTime": raw.get("gameStartTime"),
        "gameLength": raw.get("gameLength"),
        "platformId": raw.get("platformId"),
        "participants": participants,
    }
    return out


def winning_team_color(match: dict[str, Any]) -> str | None:
    teams = match.get("teams") or []
    for t in teams:
        if t.get("win") is True:
            tid = t.get("teamId")
            if tid == 100:
                return "BLUE"
            if tid == 200:
                return "RED"
    return None


def demo_team_stats(game_id: str, game_length: int) -> tuple[dict[str, int], dict[str, int]]:
    """
    Deterministic pseudo-stats for UI demos when Riot's active snapshot has no economy fields.
    Not used for resolving outcomes.
    """
    seed = int(hashlib.sha256(game_id.encode()).hexdigest()[:8], 16)
    phase = game_length + seed % 120
    blue_gold = 25000 + int(1800 * math.sin(phase / 90.0))
    red_gold = 25000 + int(1700 * math.cos(phase / 85.0))
    blue_kills = max(0, (seed % 4) + (game_length // 120))
    red_kills = max(0, ((seed >> 3) % 4) + (game_length // 130))
    blue_towers = min(11, game_length // 420 + (seed % 2))
    red_towers = min(11, game_length // 450 + ((seed >> 5) % 2))
    blue_dragons = min(4, game_length // 360 + (seed % 2))
    red_dragons = min(4, game_length // 400 + ((seed >> 7) % 2))
    blue = {
        "gold": blue_gold,
        "kills": blue_kills,
        "towers": blue_towers,
        "dragons": blue_dragons,
    }
    red = {
        "gold": red_gold,
        "kills": red_kills,
        "towers": red_towers,
        "dragons": red_dragons,
    }
    return blue, red
