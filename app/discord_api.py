"""Petit client HTTP pour l'API Discord (cote utilisateur et cote bot)."""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from .config import settings

log = logging.getLogger("verif.discord")

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/zxch-0/bottoken, 1.0.0)"

_client: Optional[httpx.AsyncClient] = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _bot_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bot {settings.discord_bot_token}",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
    }


async def _user_get(token: str, path: str) -> httpx.Response:
    """Les tokens utilisateur n'acceptent pas le prefixe `Bearer`, les tokens OAuth2 si.
    On tente donc le format brut puis le format Bearer."""
    c = client()
    headers = {"Authorization": token, "User-Agent": USER_AGENT}
    response = await c.get(f"{API}{path}", headers=headers)
    if response.status_code == 401:
        headers["Authorization"] = f"Bearer {token}"
        response = await c.get(f"{API}{path}", headers=headers)
    return response


async def fetch_me(token: str) -> Optional[dict[str, Any]]:
    try:
        response = await _user_get(token, "/users/@me")
    except httpx.HTTPError as exc:
        log.warning("fetch_me a echoue: %s", exc)
        return None
    if response.status_code != 200:
        return None
    return response.json()


async def fetch_user_guilds(token: str) -> list[dict[str, Any]]:
    try:
        response = await _user_get(token, "/users/@me/guilds")
    except httpx.HTTPError:
        return []
    if response.status_code != 200:
        return []
    return response.json()


async def fetch_guild(guild_id: int) -> Optional[dict[str, Any]]:
    if not settings.discord_bot_token:
        return None
    try:
        response = await client().get(f"{API}/guilds/{guild_id}", headers=_bot_headers())
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    return response.json()


async def add_guild_member(guild_id: int, user_id: int, access_token: str) -> tuple[bool, str]:
    """Force l'arrivee du membre sur le serveur via le scope `guilds.join`."""
    if not settings.discord_bot_token:
        return False, "DISCORD_BOT_TOKEN manquant"
    try:
        response = await client().put(
            f"{API}/guilds/{guild_id}/members/{user_id}",
            headers=_bot_headers(),
            json={"access_token": access_token},
        )
    except httpx.HTTPError as exc:
        return False, f"erreur reseau: {exc}"
    if response.status_code == 201:
        return True, "membre ajoute au serveur"
    if response.status_code == 204:
        return True, "deja membre"
    return False, f"{response.status_code}: {response.text[:180]}"


async def add_role(guild_id: int, user_id: int, role_id: int) -> tuple[bool, str]:
    if not settings.discord_bot_token:
        return False, "DISCORD_BOT_TOKEN manquant"
    try:
        response = await client().put(
            f"{API}/guilds/{guild_id}/members/{user_id}/roles/{role_id}",
            headers=_bot_headers(),
        )
    except httpx.HTTPError as exc:
        return False, f"erreur reseau: {exc}"
    if response.status_code in (204, 200):
        return True, "role attribue"
    return False, f"{response.status_code}: {response.text[:180]}"


async def remove_role(guild_id: int, user_id: int, role_id: int) -> tuple[bool, str]:
    if not settings.discord_bot_token:
        return False, "DISCORD_BOT_TOKEN manquant"
    try:
        response = await client().delete(
            f"{API}/guilds/{guild_id}/members/{user_id}/roles/{role_id}",
            headers=_bot_headers(),
        )
    except httpx.HTTPError as exc:
        return False, f"erreur reseau: {exc}"
    if response.status_code in (204, 200):
        return True, "role retire"
    return False, f"{response.status_code}: {response.text[:180]}"


async def exchange_code(code: str, redirect_uri: str) -> Optional[dict[str, Any]]:
    """Echange un code OAuth2 contre un token (flux `response_type=code`)."""
    data = {
        "client_id": settings.discord_client_id,
        "client_secret": settings.discord_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    try:
        response = await client().post(
            f"{API}/oauth2/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.HTTPError as exc:
        log.warning("exchange_code a echoue: %s", exc)
        return None
    if response.status_code != 200:
        log.warning("exchange_code %s: %s", response.status_code, response.text[:200])
        return None
    return response.json()


def avatar_url(user_id: int, avatar: Optional[str], discriminator: Optional[str] = None) -> str:
    if avatar:
        ext = "gif" if avatar.startswith("a_") else "png"
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{ext}?size=128"
    index = 0
    if discriminator and discriminator != "0":
        try:
            index = int(discriminator) % 5
        except ValueError:
            index = 0
    return f"https://cdn.discordapp.com/embed/avatars/{index}.png"
