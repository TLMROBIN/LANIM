from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "School IM"
    database_url: str = "postgresql+psycopg://im:im@db:5432/im"
    media_dir: Path = Path("/data/media")
    session_secret: str = "change-me-in-production"
    oidc_issuer: str = "http://10.50.159.62/auth/realms/school-platform"
    oidc_client_id: str = "im"
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://10.50.159.62/im/api/auth/oidc/callback"
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_encrypt_key: Optional[str] = None
    feishu_verification_token: Optional[str] = None
    dev_auth_enabled: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_prefix="IM_")
