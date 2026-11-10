"""Google's authorization-code flow; provider credentials stay on the API server."""

import base64
import hashlib
import hmac
import json
import secrets
import uuid
from urllib.parse import urlencode

import httpx
import jwt
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pulsegrid.api.email_alerts import email_channels, new_email_channel
from pulsegrid.api.email_auth import mail_enabled, require_mail, send_link
from pulsegrid.api.schemas import Reauth
from pulsegrid.auth.google_migration import approval_value, migrate_password_account
from pulsegrid.auth.security import (
    GOOGLE_REAUTH_SECONDS,
    audit,
    cfg,
    challenge,
    csrf,
    digest,
    google_reauth_key,
    issue,
    principal,
    rate,
    reauthenticate,
    redis,
)
from pulsegrid.storage.db import db, now
from pulsegrid.storage.models import LoginSession, User

router = APIRouter()
COOKIE = "pg_google_state"
COOKIE_PATH = "/api/v1/auth/google"
TTL = 600


def enabled():
    return bool(cfg.google_client_id.strip() and cfg.google_client_secret.strip())


def redirect_uri():
    return cfg.public_origin.rstrip("/") + COOKIE_PATH + "/callback"


def result(path="/", **values):
    # Fragments keep errors and MFA challenges out of HTTP request URLs and referrers.
    response = RedirectResponse(path + "#" + urlencode(values), status_code=303)
    response.delete_cookie(COOKIE, path=COOKIE_PATH, secure=cfg.secure_cookies, httponly=True, samesite="lax")
    return response


@router.get("/config")
async def config():
    return {"enabled": enabled()}


@router.post("/start")
async def start(request: Request, response: Response):
    await limit_start(request)
    return await authorization(response)


async def limit_start(request):
    if not enabled():
        raise HTTPException(503, "Google sign-in is not configured yet")
    await rate(f"google-start:{request.client.host}", 30)


@router.post("/link")
async def start_link(
    data: Reauth, request: Request, response: Response, auth=Depends(principal), session: AsyncSession = Depends(db)
):
    await limit_start(request)
    user = await session.get(User, auth[0].id, populate_existing=True, with_for_update=True)
    if user.google_sub:
        raise HTTPException(409, "Google is already connected")
    await reauthenticate(session, user, data.password, data.code, failure_status=403)
    transaction = await authorization(
        response, action="link", session_id=str(auth[1].id), credentials=approval_value(user)
    )
    await session.commit()
    return transaction


@router.post("/reauth")
async def start_reauth(request: Request, response: Response, auth=Depends(principal)):
    # Google-only accounts have no password. A fresh Google sign-in unlocks security settings for a short time.
    await limit_start(request)
    user, family = auth
    if not user.google_sub or user.password_hash:
        raise HTTPException(409, "Use your password to confirm this change")
    await rate(f"google-reauth:{user.id}", 5, 900)
    return await authorization(response, action="reauth", session_id=str(family.id))


@router.post("/add-password")
async def start_add_password(request: Request, response: Response, auth=Depends(principal)):
    await limit_start(request)
    require_mail()
    user, family = auth
    if not user.google_sub or user.password_hash:
        raise HTTPException(409, "This account cannot add a password")
    await rate(f"add-password:{user.id}", 3, 900)
    return await authorization(response, action="add-password", session_id=str(family.id))


async def authorization(response, **intent):
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    await redis.set(
        f"google-state:{digest(state)}",
        json.dumps({"nonce": nonce, "verifier": verifier, **intent}),
        ex=TTL,
    )
    # Only this short-lived cookie uses Lax so it accompanies Google's top-level GET callback.
    response.set_cookie(
        COOKIE, state, max_age=TTL, httponly=True, secure=cfg.secure_cookies, samesite="lax", path=COOKIE_PATH
    )
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return {
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?"
        + urlencode(
            {
                "client_id": cfg.google_client_id,
                "redirect_uri": redirect_uri(),
                "response_type": "code",
                "scope": "openid email",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
    }


async def identity(code, transaction):
    async with httpx.AsyncClient(timeout=10) as client:
        token = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": cfg.google_client_id,
                "client_secret": cfg.google_client_secret,
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
                "code_verifier": transaction["verifier"],
            },
        )
        token.raise_for_status()
        encoded = token.json()["id_token"]
        header = jwt.get_unverified_header(encoded)
        keys = await client.get("https://www.googleapis.com/oauth2/v3/certs")
        keys.raise_for_status()
        jwk = next((key for key in keys.json()["keys"] if key["kid"] == header["kid"]), None)
        if not jwk:
            raise ValueError("Unknown Google signing key")
        claims = jwt.decode(
            encoded,
            jwt.PyJWK.from_dict(jwk, algorithm="RS256").key,
            algorithms=["RS256"],
            audience=cfg.google_client_id,
            issuer=["https://accounts.google.com", "accounts.google.com"],
            options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce", "email", "email_verified"]},
        )
    if (
        not isinstance(claims["nonce"], str)
        or not hmac.compare_digest(claims["nonce"], transaction["nonce"])
        or claims["email_verified"] is not True
        or not isinstance(claims["sub"], str)
        or not 1 <= len(claims["sub"]) <= 255
        or claims.get("azp", cfg.google_client_id) != cfg.google_client_id
    ):
        raise ValueError("Invalid Google identity")
    email = validate_email(claims["email"], check_deliverability=False).normalized.lower()
    if len(email) > 254:
        raise ValueError("Invalid email")
    return claims["sub"], email


@router.get("/callback")
async def callback(
    request: Request,
    tasks: BackgroundTasks,
    state: str = Query(default="", max_length=100),
    code: str = Query(default="", max_length=4096),
    error: str = Query(default="", max_length=200),
    session: AsyncSession = Depends(db, scope="function"),
):
    if not enabled():
        return result(google_error="not_configured")
    browser_state = request.cookies.get(COOKIE, "")
    if not state or not browser_state or not hmac.compare_digest(digest(state), digest(browser_state)):
        return result(google_error="expired")
    transaction = await redis.getdel(f"google-state:{digest(state)}")
    if not transaction:
        return result(google_error="expired")
    transaction = json.loads(transaction)
    link = bool(transaction.get("action"))
    if error or not code:
        status = "cancelled" if error == "access_denied" else "failed"
        return link_result(status) if link else result(google_error=status)
    try:
        sub, email = await identity(code, transaction)
    except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError, TypeError, EmailNotValidError):
        return link_result("failed") if link else result(google_error="failed")
    if link:
        return await finish_link(session, tasks, transaction, sub, email)

    user = await session.scalar(select(User).where(User.google_sub == sub).with_for_update())
    if not user:
        # An email match alone cannot link an existing account.
        existing = await session.scalar(select(User).where(User.email == email).with_for_update())
        if existing:
            if not await migrate_password_account(session, existing, sub):
                return result(google_error="email_exists")
            user = existing
    if not user and not cfg.signups_enabled:
        return result(google_error="signups_closed")
    if not user:
        try:
            async with session.begin_nested():
                user = User(email=email, google_sub=sub)
                session.add(user)
                await session.flush()
                audit(session, user, "google_signup")
        except IntegrityError:
            # Simultaneous first sign-ins may both try to create the same identity.
            user = await session.scalar(select(User).where(User.google_sub == sub).with_for_update())
            if not user:
                return result(google_error="email_exists")

    if user.totp_secret:
        # Persist an approved conversion before issuing a challenge for its new credential version.
        await session.commit()
        response = result(google_challenge=await challenge(user))
        csrf(response)
        return response
    if not await email_channels(session, user.id):
        session.add(new_email_channel(user))
    response = result(google_success="1")
    await issue(session, response, user, label=request.headers.get("user-agent", "Browser"))
    audit(session, user, "google_login")
    await session.commit()
    return response


def link_result(status):
    return result("/?view=settings", google_link=status)


async def finish_link(session, tasks, transaction, sub, email):
    sid = uuid.UUID(transaction["session_id"])
    family = await session.get(LoginSession, sid)
    if not family:
        return link_result("expired")
    # Lock in the same order as password reset and recheck revocation after locking.
    user = await session.get(User, family.user_id, with_for_update=True)
    family = await session.get(LoginSession, sid, populate_existing=True, with_for_update=True)
    if not user or not family or family.revoked_at or family.expires_at <= now():
        return link_result("expired")
    if user.email != email:
        return link_result("wrong_account")

    if transaction["action"] == "reauth":
        if user.google_sub != sub or user.password_hash:
            return link_result("wrong_account")
        await redis.set(google_reauth_key(sid), "1", ex=GOOGLE_REAUTH_SECONDS)
        audit(session, user, "google_reauth")
        await session.commit()
        return link_result("verified")

    if transaction["action"] == "add-password":
        if user.google_sub != sub or user.password_hash:
            return link_result("wrong_account")
        if not mail_enabled():
            return link_result("mail_unavailable")
        await send_link(tasks, "add-password", email, session_id=str(sid), google_sub=sub)
        return link_result("check_email")

    if user.google_sub or not hmac.compare_digest(approval_value(user), transaction["credentials"]):
        return link_result("expired")
    user.google_sub = sub
    audit(session, user, "google_account_linked")
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return link_result("wrong_account")
    return link_result("connected")
