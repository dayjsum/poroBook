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
    # featured = Riot spectator featured games (mixed skill, no seeders required).
    # seeders = only SEEDER_PUUIDS. both = merge featured + seeders.
    porobook_discovery_mode: str = "featured"
    ddragon_version: str | None = None
    porobook_demo_stats: bool = False
    database_path: str = "porobook.db"
    war_room_cutoff_seconds: int = 301  # 5:01 on spectator clock
    poll_resolve_seconds: int = 45
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
        return [p.strip() for p in self.seeder_puuids.split(",") if p.strip()]

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
