from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

path = Path(__file__).resolve().parents[2]
path = path / ".env"


class Settings(BaseSettings):
    DB_HOST: str
    DB_PASSWORD: str
    DB_PORT: str
    DB: str
    DB_SCHEMA: str
    model_config = SettingsConfigDict(
        env_file=path, extra="ignore", env_file_encoding="utf-8", case_sensitive=False
    )


settings = Settings()
__all__ = ["settings"]
