"""Runtime settings from environment variables (never from the strategy config)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Field(default=Path("./data"), alias="BTC_DATA_DIR")
    strategy_config: Path = Field(
        default=Path("config/btc_swing.default.yaml"), alias="BTC_STRATEGY_CONFIG"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
