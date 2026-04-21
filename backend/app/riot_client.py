from __future__ import annotations

import hashlib
import math
from typing import Any
from urllib.parse import quote

import logging

import httpx

from app.config import Settings

logger = logging.getLogger("porobook")

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
        """Legacy v4 path by encrypted summoner id (summoner-v4 ``id`` field). Seeders use :meth:`seeder_active_game`."""
        url = f"{self.settings.platform_host}/lol/spectator/v4/active-games/by-summoner/{encrypted_summoner_id}"
        r = await self._get(client, url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def active_game_by_puuid(self, client: httpx.AsyncClient, puuid: str) -> dict | None:
        """
        ``GET /lol/spectator/v5/active-games/by-summoner/{encryptedPUUID}`` (same PUUID string as match-v5).
        """
        p = puuid.strip().strip('"').strip("'").replace("\ufeff", "")
        if not p:
            return None
        pid = quote(p, safe="")
        url = f"{self.settings.platform_host}/lol/spectator/v5/active-games/by-summoner/{pid}"
        r = await self._get(client, url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    async def seeder_active_game(self, client: httpx.AsyncClient, puuid: str) -> dict | None:
        """
        In-progress game for a seeder: spectator-v5 by PUUID first. On **HTTP 400**, fall back to
        summoner-v4 by PUUID + spectator-v4 active game (some Riot responses reject the v5 path for otherwise valid ids).
        """
        p = puuid.strip().strip('"').strip("'").replace("\ufeff", "")
        if not p:
            return None
        pid = quote(p, safe="")
        url = f"{self.settings.platform_host}/lol/spectator/v5/active-games/by-summoner/{pid}"
        r = await self._get(client, url)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        if r.status_code == 400:
            body = (r.text or "")[:240]
            logger.warning(
                "spectator-v5 active-game HTTP 400 puuid …%s — trying summoner-v4 + spectator-v4. body=%s",
                p[-8:],
                body,
            )
            summoner_id = await self.summoner_id_by_puuid(client, p)
            if not summoner_id:
                return None
            return await self.active_game(client, summoner_id)
        r.raise_for_status()
        return r.json()

    async def featured_games(self, client: httpx.AsyncClient) -> tuple[list[dict[str, Any]], str | None]:
        """
        Riot spectator **featured** list (small shard snapshot, not all live games).
        Tries **spectator-v5** first, then **v4** if v5 is not available (e.g. HTTP 404 on route).
        """
        base = self.settings.platform_host
        paths = ["/lol/spectator/v5/featured-games", "/lol/spectator/v4/featured-games"]

        for i, path in enumerate(paths):
            url = f"{base}{path}"
            r = await self._get(client, url)
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    logger.exception("featured-games JSON parse failed url=%s", path)
                    return [], "Riot returned unreadable data for featured games; check server logs."
                raw_list = data.get("gameList")
                if not isinstance(raw_list, list):
                    return [], None
                return [dict(x) for x in raw_list], None
            if r.status_code == 401:
                return [], "Riot returned 401: RIOT_API_KEY is missing, wrong, or expired."
            if r.status_code == 403:
                return [], (
                    "Riot returned 403 on spectator featured games: this developer key cannot use League spectator "
                    "(spectator-v5 / v4). In https://developer.riotgames.com/ open the **same** app your key belongs "
                    "to, confirm **League of Legends** is registered for that app, regenerate the **development** API "
                    "key, and paste it into RIOT_API_KEY (no quotes/spaces). Production keys and dev keys are different."
                )
            if r.status_code == 429:
                return [], "Riot rate limit (429): wait briefly and hit Refresh."
            if r.status_code == 404 and i == 0:
                logger.info("spectator-v5 featured-games returned 404; falling back to v4")
                continue
            if r.status_code == 404:
                return [], None
            logger.warning("featured-games %s HTTP %s: %s", path, r.status_code, (r.text or "")[:400])
            if i == 0:
                continue
            return (
                [],
                f"Riot returned HTTP {r.status_code} for featured games (platform {self.settings.riot_platform!r}). "
                "Check the key, portal product access, and RIOT_PLATFORM.",
            )

        return (
            [],
            "Featured games: spectator-v5 and v4 did not return a usable list (check RIOT_PLATFORM and server logs).",
        )

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
