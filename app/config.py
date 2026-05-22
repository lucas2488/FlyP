from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://fly_user:fly_password@localhost:5432/fly_db"
    sync_database_url: str = "postgresql://fly_user:fly_password@localhost:5432/fly_db"
    firebase_credentials_path: str = ""
    price_drop_threshold: float = 0.15
    price_check_interval_hours: int = 6
    api_secret_key: str = "changeme"
    environment: str = "development"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
