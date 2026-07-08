import os
from typing import List
from pydantic_settings import BaseSettings
from pydantic import SecretStr

class Settings(BaseSettings):
    api_key: SecretStr = SecretStr("dummy")
    api_secret: SecretStr = SecretStr("dummy")
    telegram_bot_token: SecretStr = SecretStr("dummy")
    telegram_owner_ids: str = "123" # comma separated if needed, simplifying for now
    db_path: str = "data/portfoliopi.db"

    class Config:
        env_file = ".env"

    @property
    def owner_ids_list(self) -> List[int]:
        if not self.telegram_owner_ids:
            return []
        return [int(x.strip()) for x in self.telegram_owner_ids.split(',')]

settings = Settings()
