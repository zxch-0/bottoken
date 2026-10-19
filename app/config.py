"""Configuration centrale, chargee depuis les variables d'environnement."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _normalise_db_url(url: str) -> str:
    """Render fournit parfois des URL `postgres://` qu'asyncpg refuse."""
    if not url:
        return url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    elif url.startswith("postgresql+asyncpg://"):
        url = "postgresql://" + url[len("postgresql+asyncpg://") :]
    return url


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _default_base_url() -> str:
    explicit = _env("PUBLIC_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    render_url = _env("RENDER_EXTERNAL_URL")
    if render_url:
        return render_url.rstrip("/")
    return "http://localhost:8000"


@dataclass(frozen=True)
class Settings:
    database_url: str
    discord_client_id: str
    discord_client_secret: str
    discord_bot_token: str
    public_base_url: str
    admin_password: str
    session_secret: str
    run_bot: bool
    default_scopes: str
    verify_role_name: str

    @property
    def oauth_redirect_uri(self) -> str:
        explicit = _env("OAUTH_REDIRECT_URI")
        if explicit:
            return explicit
        return f"{self.public_base_url}/auth/callback"

    @property
    def bot_invite_url(self) -> str:
        return (
            "https://discord.com/oauth2/authorize"
            f"?client_id={self.discord_client_id}"
            "&permissions=268438544"
            "&scope=bot%20applications.commands"
        )

    @property
    def configured(self) -> bool:
        return bool(self.discord_client_id and self.discord_client_secret)


def load_settings() -> Settings:
    return Settings(
        database_url=_normalise_db_url(_env("DATABASE_URL")),
        discord_client_id=_env("DISCORD_CLIENT_ID"),
        discord_client_secret=_env("DISCORD_CLIENT_SECRET"),
        discord_bot_token=_env("DISCORD_BOT_TOKEN"),
        public_base_url=_default_base_url(),
        admin_password=_env("ADMIN_PASSWORD", "admin"),
        session_secret=_env("SESSION_SECRET", "dev-secret-change-me"),
        run_bot=_env_bool("RUN_BOT", True),
        default_scopes=_env("DEFAULT_SCOPES", "identify email guilds guilds.join"),
        verify_role_name=_env("VERIFY_ROLE_NAME", "Verifie"),
    )


settings = load_settings()
