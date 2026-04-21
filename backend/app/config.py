from functools import lru_cache
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    riot_api_key: str = ""
    riot_platform: str = "na1"
    riot_region: str = "americas"
    seeder_puuids: str = ""
    # Development keys rate-limit hard; huge SEEDER_PUUIDS lists will 429 without a cap + delay.
    seeder_max_lookups_per_poll: int = 10
    seeder_request_delay_seconds: float = 0.18
    # featured = Riot spectator featured games (mixed skill, no seeders required).
    # seeders = only SEEDER_PUUIDS. both = merge featured + seeders.
    porobook_discovery_mode: str = "featured"
    ddragon_version: str | None = None
    porobook_demo_stats: bool = False
    database_path: str = "porobook.db"
    war_room_cutoff_seconds: int = 301  # 5:01 on spectator clock
    # Keep last lobby spectator payloads so war-room can open a card after Riot drops it from featured/active.
    spectator_lobby_cache_seconds: int = 300
    poll_resolve_seconds: int = 45
    # Background lobby harvest (Riot calls only here, not on GET /api/lobby).
    lobby_harvest_interval_seconds: int = 75
    lobby_harvest_jitter_seconds: int = 15
    # Min resolved predictions to appear on leaderboard (reduces tiny-sample luck).
    leaderboard_min_resolved: int = 10
    leaderboard_default_limit: int = 50

    @property
    def platform_host(self) -> str:
        return f"https://{self.riot_platform.lower()}.api.riotgames.com"

    @property
    def region_host(self) -> str:
        return f"https://{self.riot_region.lower()}.api.riotgames.com"

    @property
    def seeder_puuid_list(self) -> list[str]:
        raw = (self.seeder_puuids or "").replace("\r\n", ",").replace("\n", ",")
        out: list[str] = []
        for part in raw.split(","):
            p = part.strip().strip('"').strip("'").replace("\ufeff", "")
            if p:
                out.append(p)
        return out

    @field_validator("porobook_discovery_mode", mode="before")
    @classmethod
    def _normalize_discovery_mode(cls, v: Any) -> str:
        if v is None:
            return "featured"
        s = str(v).strip().lower()
        if s not in ("featured", "seeders", "both"):
            return "featured"
        return s

    @field_validator("porobook_demo_stats", mode="before")
    @classmethod
    def _coerce_demo_stats(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(v, int):
            return v != 0
        return bool(v)


@lru_cache
def get_settings() -> Settings:
    return Settings()
