from typing import List

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    api_key: SecretStr
    api_secret: SecretStr
    telegram_bot_token: SecretStr
    telegram_owner_ids: str
    db_path: str = "data/portfoliopi.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def owner_ids_list(self) -> List[int]:
        if not self.telegram_owner_ids:
            return []
        return [int(x.strip()) for x in self.telegram_owner_ids.split(',')]

settings = Settings()
