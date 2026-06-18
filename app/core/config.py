from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # APP
    APP_NAME: str = "YESARHA Core"
    APP_VERSION: str = "2.0"
    API_PREFIX: str = "/api/v1"
    ENV: str = "development"

    # DATABASE
    DATABASE_URL: str = "postgresql+psycopg2://yesarha:yesarha@localhost:5432/yesarha_core"
    USE_SQLITE_FALLBACK: bool = True
    SQLITE_PATH: str = "data/yesarha.db"

    # AUTH / JWT
    JWT_SECRET_KEY: str = "CHANGE_ME_SUPER_SECRET_KEY"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24        # 1 day
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 30  # 30 days

    # OLLAMA
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    DEFAULT_MODEL: str = "qwen3:8b"

    # CHROMA (semantic memory - future)
    CHROMA_PERSIST_DIR: str = "data/chroma"

    # CORS
    CORS_ORIGINS: list[str] = ["*"]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
