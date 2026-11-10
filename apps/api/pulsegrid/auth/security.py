import asyncio
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import timedelta

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from pulsegrid.config import settings
from pulsegrid.storage.db import db, now
from pulsegrid.storage.models import Audit, LoginSession, RecoveryCode, RefreshToken, User

cfg = settings()
redis = Redis.from_url(cfg.redis_url, socket_connect_timeout=2, socket_timeout=2, decode_responses=True)
hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
password_slots = asyncio.Semaphore(4)
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))


def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def encrypt(value: str):
    return Fernet(cfg.encryption_key.encode()).encrypt(value.encode()).decode()


def decrypt(value: str):
    return Fernet(cfg.encryption_key.encode()).decrypt(value.encode()).decode()


def password_enabled(user):
    return bool(user.password_hash and (user.email_verified_at or cfg.enable_legacy_password_auth))


GOOGLE_REAUTH_SECONDS = 600


def google_only(user):
    return bool(user.google_sub and not user.password_hash)


def google_reauth_key(session_id):
    return f"google-reauth:{session_id}"


async def google_reauth_fresh(session_id):
    return bool(session_id and await redis.exists(google_reauth_key(session_id)))


async def hash_password(password):
    async with password_slots:
        return await asyncio.to_thread(hasher.hash, password)


async def password_ok(password, encoded):
    async with password_slots:
        try:
            valid = await asyncio.to_thread(hasher.verify, encoded or DUMMY_HASH, password)
            return bool(encoded) and valid
        except VerificationError:
            return False


async def rate(key, limit=10, seconds=300):
    n = await redis.eval(
        "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n",
        1,
        key,
        seconds,
    )
    if n > limit:
        raise HTTPException(429, "Too many attempts; try again later")


def cookie(response, key, value, max_age, httponly=True):
    response.set_cookie(
        key, value, max_age=max_age, httponly=httponly, secure=cfg.secure_cookies, samesite="strict", path="/"
    )


def csrf(response):
    token = secrets.token_urlsafe(32)
    cookie(response, "pg_csrf", token, 86400, False)
    return token


def require_csrf(request):
    if request.headers.get("origin") != cfg.public_origin:
        raise HTTPException(403, "Origin rejected")
    a, b = request.cookies.get("pg_csrf", ""), request.headers.get("x-csrf-token", "")
    if not a or not hmac.compare_digest(a, b):
        raise HTTPException(403, "CSRF token required")


async def principal(request: Request, session: AsyncSession = Depends(db)):
    await redis.ping()
    try:
        claims = jwt.decode(
            request.cookies.get("pg_access", ""),
            cfg.jwt_secret,
            algorithms=["HS256"],
            audience="pulsegrid",
            issuer="pulsegrid",
        )
        session_id, user_id = uuid.UUID(claims["sid"]), uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "Sign in required") from None
    family = await session.get(LoginSession, session_id)
    if not family or family.user_id != user_id or family.revoked_at or family.expires_at <= now():
        raise HTTPException(401, "Session expired or revoked")
    user = await session.get(User, user_id)
    return user, family


async def issue(session, response, user, family=None, label="Browser"):
    if family is None:
        family = LoginSession(user_id=user.id, expires_at=now() + timedelta(days=cfg.session_days), label=label[:200])
        session.add(family)
        await session.flush()
    family.last_activity = now()
    raw = secrets.token_urlsafe(48)
    token = RefreshToken(session_id=family.id, digest=digest(raw), expires_at=family.expires_at)
    session.add(token)
    await session.flush()
    access = jwt.encode(
        {
            "sub": str(user.id),
            "sid": str(family.id),
            "exp": now() + timedelta(seconds=cfg.access_seconds),
            "iat": now(),
            "aud": "pulsegrid",
            "iss": "pulsegrid",
        },
        cfg.jwt_secret,
        algorithm="HS256",
    )
    cookie(response, "pg_access", access, cfg.access_seconds)
    cookie(response, "pg_refresh", raw, max(0, int((family.expires_at - now()).total_seconds())))
    csrf(response)
    return token


async def verify_factor(session, user, code):
    if user.totp_secret is None:
        return True
    await rate(f"factor:{user.id}", 10)
    totp = pyotp.TOTP(decrypt(user.totp_secret))
    step = int(now().timestamp()) // 30
    for tick in (step - 1, step, step + 1):
        if hmac.compare_digest(totp.at(tick * 30), code):
            if not await redis.set(f"totp:{user.id}:{tick}", "used", nx=True, ex=120):
                return False
            consumed = await session.execute(
                update(User)
                .where(User.id == user.id, or_(User.last_totp_step.is_(None), User.last_totp_step < tick))
                .values(last_totp_step=tick)
                .returning(User.id)
            )
            return consumed.scalar_one_or_none() is not None
    used = await session.execute(
        update(RecoveryCode)
        .where(RecoveryCode.user_id == user.id, RecoveryCode.digest == digest(code), RecoveryCode.consumed_at.is_(None))
        .values(consumed_at=now())
        .returning(RecoveryCode.id)
    )
    return used.scalar_one_or_none() is not None


async def reauthenticate(session, user, password, code, *, failure_status=401, session_id=None):
    if user.password_hash and not password_enabled(user):
        raise HTTPException(403, "Password sign-in is disabled for this legacy account")
    await rate(f"reauth:{user.id}")
    if google_only(user):
        # A Google-only account has no password, so a recent Google sign-in from this session proves identity.
        if not await google_reauth_fresh(session_id):
            raise HTTPException(failure_status, "Verify with Google again to continue")
    elif not await password_ok(password, user.password_hash):
        raise HTTPException(failure_status, "Reauthentication failed")
    if not await verify_factor(session, user, code):
        raise HTTPException(failure_status, "Reauthentication failed")


async def recovery_codes(session, user):
    await session.execute(RecoveryCode.__table__.delete().where(RecoveryCode.user_id == user.id))
    codes = [secrets.token_hex(8) for _ in range(10)]
    session.add_all([RecoveryCode(user_id=user.id, digest=digest(c)) for c in codes])
    return codes


def audit(session, user, kind, resource_type="user", resource_id=None):
    session.add(Audit(user_id=user.id, kind=kind, resource_type=resource_type, resource_id=resource_id or user.id))


async def challenge(user):
    raw = secrets.token_urlsafe(32)
    await redis.set(
        f"challenge:{raw}",
        json.dumps(
            {
                "user_id": str(user.id),
                "credential_version": digest((user.password_hash or "") + (user.totp_secret or "")),
            }
        ),
        ex=300,
    )
    return raw
