from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str = "dev-insecure-secret-change-me"
    encryption_key: str = ""
    admin_username: str = "admin"
    admin_password: str = "admin123"
    database_url: str = "sqlite:////data/app.db"
    collect_interval_minutes: int = 60
    cors_origins: str = ""
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    jwt_algorithm: str = "HS256"

    @property
    def cors_origin_list(self) -> list[str]:
        if not self.cors_origins.strip():
            # 默认覆盖本地开发与 Docker 访问来源；生产同源反代不依赖 CORS
            return [
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://localhost:8080",
                "http://127.0.0.1:8080",
            ]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
