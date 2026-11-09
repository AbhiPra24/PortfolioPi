from typing import List

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    api_key: SecretStr = SecretStr("mock_api_key")
    api_secret: SecretStr = SecretStr("mock_api_secret")
    telegram_bot_token: SecretStr = SecretStr("mock_telegram_bot_token")
    telegram_owner_ids: str = "12345678"
    db_path: str = "data/portfoliopi.db"
    database_url: SecretStr = SecretStr("")
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "pralabs:latest"
    ollama_fallback_url: str = "http://host.docker.internal:11434"
    ollama_fallback_model: str = "pralabs:edge"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def owner_ids_list(self) -> List[int]:
        if not self.telegram_owner_ids:
            return []
        return [int(x.strip()) for x in self.telegram_owner_ids.split(",")]


settings = Settings()
