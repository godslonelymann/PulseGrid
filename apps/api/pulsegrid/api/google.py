"""Google's authorization-code flow; provider credentials stay on the API server."""

import base64
import hashlib
import hmac
import json
import secrets
from urllib.parse import urlencode

import httpx
import jwt
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pulsegrid.auth.security import audit, cfg, challenge, csrf, digest, issue, rate, redis
from pulsegrid.storage.db import db
from pulsegrid.storage.models import User

router = APIRouter()
COOKIE = "pg_google_state"
COOKIE_PATH = "/api/v1/auth/google"
TTL = 600


def enabled():
    return bool(cfg.google_client_id.strip() and cfg.google_client_secret.strip())


def redirect_uri():
    return cfg.public_origin.rstrip("/") + COOKIE_PATH + "/callback"


def result(**values):
    # Fragments keep errors and MFA challenges out of HTTP request URLs and referrers.
    response = RedirectResponse("/#" + urlencode(values), status_code=303)
    response.delete_cookie(COOKIE, path=COOKIE_PATH, secure=cfg.secure_cookies, httponly=True, samesite="lax")
    return response


@router.get("/config")
async def config():
    return {"enabled": enabled()}


@router.post("/start")
async def start(request: Request, response: Response):
    if not enabled():
        raise HTTPException(503, "Google sign-in is not configured yet")
    await rate(f"google-start:{request.client.host}", 30)
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    await redis.set(
        f"google-state:{digest(state)}",
        json.dumps({"nonce": nonce, "verifier": verifier}),
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
    state: str = Query(default="", max_length=100),
    code: str = Query(default="", max_length=4096),
    error: str = Query(default="", max_length=200),
    session: AsyncSession = Depends(db),
):
    if not enabled():
        return result(google_error="not_configured")
    browser_state = request.cookies.get(COOKIE, "")
    if not state or not browser_state or not hmac.compare_digest(digest(state), digest(browser_state)):
        return result(google_error="expired")
    transaction = await redis.getdel(f"google-state:{digest(state)}")
    if not transaction:
        return result(google_error="expired")
    if error or not code:
        return result(google_error="cancelled" if error == "access_denied" else "failed")
    try:
        sub, email = await identity(code, json.loads(transaction))
    except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError, TypeError, EmailNotValidError):
        return result(google_error="failed")

    user = await session.scalar(select(User).where(User.google_sub == sub).with_for_update())
    if not user:
        # Email alone never links Google to an existing password account.
        if await session.scalar(select(User.id).where(User.email == email)):
            return result(google_error="email_exists")
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
        response = result(google_challenge=await challenge(user))
        csrf(response)
        return response
    response = result(google_success="1")
    await issue(session, response, user, label=request.headers.get("user-agent", "Browser"))
    audit(session, user, "google_login")
    await session.commit()
    return response
