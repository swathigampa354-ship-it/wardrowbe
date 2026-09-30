"""Configuration for the Wardrowbe *trial* build.

Only settings that the trial actually reads are defined here. Anything that
belonged to the stripped subsystems (Redis, arq, OIDC, SMTP, ntfy, Mattermost,
background removal, families, learning, studio) has no entry.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,
    )

    app_name: str = "Wardrowbe Trial"
    debug: bool = False
    # Only used to sign the demo JWT. No auth provider depends on it.
    secret_key: str = Field(default="trial-dev-secret")

    # --- Database -----------------------------------------------------------
    # Empty => SQLite under STORAGE_DIR (single-file demo database).
    # Set to a postgresql:// URL (Render) => asyncpg.
    database_url: str = Field(default="")
    database_echo: bool = False

    # --- Storage ------------------------------------------------------------
    # Uploads live here when no object storage is configured. Render's free
    # filesystem is EPHEMERAL: contents are lost on redeploy/spin-down.
    storage_dir: str = Field(default="/data/wardrobe")

    # Optional S3-compatible object storage (Cloudflare R2, Backblaze B2, ...).
    # When bucket + endpoint are set, images are written there instead of disk.
    storage_s3_endpoint: str | None = None
    storage_s3_bucket: str | None = None
    storage_s3_access_key: str | None = None
    storage_s3_secret_key: str | None = None
    storage_s3_region: str = "auto"

    max_upload_size_mb: int = Field(default=10, gt=0)
    max_bulk_upload_count: int = Field(default=20, gt=0)
    max_image_megapixels: float = Field(default=50.0, gt=0)
    thumbnail_size: int = Field(default=400, gt=0)
    medium_size: int = Field(default=800, gt=0)
    image_quality: int = Field(default=90, gt=0)

    # --- AI (OpenAI-compatible chat completions) ----------------------------
    ai_base_url: str = Field(default="")
    ai_api_key: str | None = None
    ai_vision_model: str = Field(default="gemini-2.5-flash")
    ai_text_model: str = Field(default="gemini-2.5-flash")
    ai_timeout: int = Field(default=90, gt=0)
    ai_max_retries: int = Field(default=2, ge=1)
    ai_max_tokens: int = Field(default=4000, gt=0)
    # Some providers reject `reasoning_effort`; None means never send it.
    ai_reasoning_effort: str | None = None
    # How many images one bulk upload analyzes at once. This is the only AI
    # concurrency bound in the trial: there is no queue to park the rest in.
    ai_upload_concurrency: int = Field(default=3, ge=1, le=8)

    # --- Auth ---------------------------------------------------------------
    # False => one implicit demo user, no login screen, no token needed.
    require_auth: bool = False
    # Optional shared gate so a public URL is not wide open.
    demo_password: str | None = None
    # Optional origin allowlist, e.g. "https://wardrowbe.onrender.com"
    cors_origins: list[str] = Field(default=["*"])

    # --- Weather (optional, keyless provider) -------------------------------
    weather_enabled: bool = True
    openmeteo_url: str = Field(default="https://api.open-meteo.com/v1")
    default_latitude: float | None = None
    default_longitude: float | None = None
    default_location: str | None = None  # e.g. "Hyderabad,Telangana,IN"

    @property
    def ai_vision_models(self) -> list[str]:
        return [m.strip() for m in self.ai_vision_model.split(",") if m.strip()]

    @property
    def ai_text_models(self) -> list[str]:
        return [m.strip() for m in self.ai_text_model.split(",") if m.strip()]

    @property
    def ai_configured(self) -> bool:
        """Vision+text need a base URL; an API key is only required for hosted APIs."""
        return bool(self.ai_base_url)

    @property
    def s3_enabled(self) -> bool:
        return bool(self.storage_s3_bucket and self.storage_s3_endpoint)

    @property
    def uses_sqlite(self) -> bool:
        return not self.database_url or self.database_url.startswith(("sqlite", "file:"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
