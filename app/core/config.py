from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET_KEY = "change-this-secret-key-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    PROJECT_NAME: str = "NUSAWIS API"
    API_V1_PREFIX: str = "/api/v1"

    DATABASE_URL: str = "sqlite:///./nusawis.db"

    # --- JWT ---
    SECRET_KEY: str = DEFAULT_SECRET_KEY
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    JWT_ISSUER: str = "nusawis-api"
    JWT_AUDIENCE: str = "nusawis-client"

    # --- Password hashing ---
    BCRYPT_ROUNDS: int = 12

    # --- Redis ---
    REDIS_URL: str = "redis://localhost:6379/0"
    # False = kalau Redis mati, rate limiter & pengecekan token ditolak (503).
    # Lebih aman untuk produksi. Set True hanya untuk development.
    REDIS_FAIL_OPEN: bool = False
    USER_CACHE_TTL_SECONDS: int = 300

    # --- Rate limit (per IP, sliding window) ---
    LOGIN_RATE_LIMIT: int = 10
    LOGIN_RATE_WINDOW_SECONDS: int = 60
    REGISTER_RATE_LIMIT: int = 5
    REGISTER_RATE_WINDOW_SECONDS: int = 3600
    REFRESH_RATE_LIMIT: int = 30
    REFRESH_RATE_WINDOW_SECONDS: int = 60

    # --- Lockout per akun (brute force ke satu email) ---
    LOGIN_MAX_FAILED_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_SECONDS: int = 900

    # Aktifkan hanya jika app berada di belakang reverse proxy terpercaya (nginx, dll)
    TRUST_PROXY_HEADERS: bool = False

    CORS_ORIGINS: str = "*"

    @property
    def cors_origins(self) -> list[str]:
        if self.CORS_ORIGINS.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
