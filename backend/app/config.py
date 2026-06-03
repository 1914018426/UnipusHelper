from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "UnipusHelper Pro"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "sqlite:///./unipus.db"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # SMTP
    SMTP_HOST: str = "smtp.qq.com"
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    ADMIN_EMAIL: str = ""

    # JWT signing secret — MUST be overridden via env
    UNIPUS_SECRET: str = ""

    # AES key prefix — MUST be overridden via env
    AES_KEY_PREFIX: str = ""

    # Task password encryption key (Fernet, 32-byte base64)
    TASK_ENCRYPTION_KEY: str = ""

    # HTTPS mode toggle
    USE_HTTPS: bool = False

    # Allowed CORS origins (comma-separated)
    ALLOWED_ORIGINS: str = ""

    # Rate limiting
    RATE_LIMIT_ENABLED: bool = True

    class Config:
        env_file = ".env"


settings = Settings()

# Validate critical secrets at startup
if not settings.UNIPUS_SECRET or len(settings.UNIPUS_SECRET) < 32:
    raise RuntimeError(
        "UNIPUS_SECRET must be set to a strong secret (>=32 chars). "
        "Generate one with: openssl rand -hex 32"
    )

if not settings.AES_KEY_PREFIX or len(settings.AES_KEY_PREFIX) < 8:
    raise RuntimeError(
        "AES_KEY_PREFIX must be set to a non-trivial value (>=8 chars)."
    )

if not settings.TASK_ENCRYPTION_KEY:
    raise RuntimeError(
        "TASK_ENCRYPTION_KEY must be set to a valid Fernet key. "
        "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
    )
