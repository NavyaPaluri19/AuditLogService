from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql+asyncpg://audit:audit_pass@localhost:5432/audit_log"

    # App
    app_env: str = "development"
    app_title: str = "Audit Log Service"
    app_version: str = "0.1.0"

    @property
    def is_testing(self) -> bool:
        return self.app_env == "testing"


# Single instance — import this everywhere
settings = Settings()
