from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://fly_user:fly_password@localhost:5432/fly_db"
    sync_database_url: str = "postgresql://fly_user:fly_password@localhost:5432/fly_db"
    firebase_credentials_path: str = ""
    price_drop_threshold: float = 0.15
    price_check_interval_hours: int = 6
    api_secret_key: str = "changeme"
    environment: str = "development"
    notification_cooldown_hours: int = 48
    notification_queue_batch_size: int = 100
    max_notifications_per_user_per_day: int = 2
    analytics_api_key: str = "analytics-changeme"
    cors_origins: str = "*"  # comma-separated list or "*"

    # Multi-level price-drop thresholds
    price_drop_threshold_soft: float = 0.05    # 5%  → nivel suave
    price_drop_threshold_strong: float = 0.10  # 10% → nivel fuerte
    price_drop_threshold_urgent: float = 0.15  # 15% → nivel urgente

    # Cooldowns por nivel (en horas)
    notification_cooldown_soft_hours: int = 24
    notification_cooldown_strong_hours: int = 12
    notification_cooldown_urgent_hours: int = 6

    # Re-engagement post-búsqueda
    reengagement_check_interval_minutes: int = 5
    reengagement_window_min_minutes: int = 20   # la búsqueda ocurrió hace al menos N min
    reengagement_window_max_minutes: int = 25   # y como máximo N min
    max_reengagements_per_user_per_day: int = 1

    # OpenAI — para generación de templates con IA
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Campaña interna — para webhook n8n y triggers externos
    internal_secret: str = "internal-changeme"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
