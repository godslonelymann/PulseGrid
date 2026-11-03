"""Email confirmation, password recovery and mailbox proof for account linking."""

import json
import logging
import secrets
import uuid
from email.message import EmailMessage
from typing import Literal
from urllib.parse import urlencode

import aiosmtplib
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from pulsegrid.api.email_alerts import new_email_channel
from pulsegrid.auth.security import audit, cfg, digest, hash_password, principal, rate, redis, verify_factor
from pulsegrid.storage.db import db, now
from pulsegrid.storage.models import LoginSession, User

router = APIRouter()
logger = logging.getLogger(__name__)
Purpose = Literal["confirm", "reset", "add-password"]
LINKS = {
    "confirm": ("Confirm your PulseGrid email", 3600),
    "reset": ("Reset your PulseGrid password", 1800),
    "add-password": ("Add a password to your PulseGrid account", 1800),
}


class Address(BaseModel):
    email: EmailStr


class NewPassword(BaseModel):
    token: str = Field(min_length=43, max_length=43)
    password: str = Field(min_length=12, max_length=256)


class AddPassword(NewPassword):
    code: str = Field(default="", max_length=64)


def mail_enabled():
    return bool(cfg.smtp_host.strip() and cfg.smtp_from.strip())


def require_mail():
    if not mail_enabled():
        raise HTTPException(503, "Account email is temporarily unavailable")


async def deliver_link(email, subject, url, minutes):
    try:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = cfg.smtp_from, email, subject
        message.set_content(
            f"{subject} using the link below. The link expires in {minutes} minutes.\n\n"
            f"{url}\n\nIf you did not request this, you can ignore this email.\n"
        )
        await aiosmtplib.send(
            message,
            hostname=cfg.smtp_host,
            port=cfg.smtp_port,
            username=cfg.smtp_user or None,
            password=cfg.smtp_password or None,
            start_tls=cfg.smtp_starttls,
            timeout=15,
        )
    except (aiosmtplib.SMTPException, OSError, ValueError) as exc:
        # SMTP errors may contain addresses or credentials.
        logger.warning("Account email delivery failed (%s)", type(exc).__name__)


async def send_link(tasks: BackgroundTasks, purpose: Purpose, email, **claims):
    subject, ttl = LINKS[purpose]
    token = secrets.token_urlsafe(32)
    await redis.set(f"email-auth:{purpose}:{digest(token)}", json.dumps({"email": email, **claims}), ex=ttl)
    # Fragments stay out of HTTP request URLs and referrers.
    url = cfg.public_origin.rstrip("/") + "/auth/email#" + urlencode({"flow": purpose, "token": token})
    tasks.add_task(deliver_link, email, subject, url, ttl // 60)


async def read_link(request, purpose: Purpose, token):
    await rate(f"email-redeem:{request.client.host}", 30)
    key = f"email-auth:{purpose}:{digest(token)}"
    value = await redis.get(key)
    if value is None:
        raise invalid_link()
    return key, json.loads(value)


def invalid_link():
    return HTTPException(400, "This link is invalid, expired, or already used. Request a new link.")


async def consume_link(key):
    # Called after validation, immediately before committing account changes.
    if await redis.getdel(key) is None:
        raise invalid_link()


async def request_limit(request, purpose, email):
    await rate(f"email-request:{purpose}:{request.client.host}", 10, 900)
    await rate(f"email-address:{purpose}:{digest(email)}", 3, 900)


@router.post("/signup", status_code=202)
async def signup(
    data: Address, request: Request, tasks: BackgroundTasks, session: AsyncSession = Depends(db, scope="function")
):
    require_mail()
    email = data.email.lower()
    await request_limit(request, "confirm", email)
    if not await session.scalar(select(User.id).where(User.email == email)):
        await send_link(tasks, "confirm", email)
    return {"detail": "If you can create an account with this address, you’ll receive a confirmation link."}


@router.post("/confirm")
async def confirm(data: NewPassword, request: Request, session: AsyncSession = Depends(db)):
    key, claims = await read_link(request, "confirm", data.token)
    uid = await session.scalar(
        insert(User)
        .values(email=claims["email"], password_hash=await hash_password(data.password), email_verified_at=now())
        .on_conflict_do_nothing(index_elements=[User.email])
        .returning(User.id)
    )
    if uid is None:
        raise invalid_link()
    user = await session.get(User, uid)
    session.add(new_email_channel(user))
    audit(session, user, "email_signup")
    await consume_link(key)
    await session.commit()
    return {"ok": True}


@router.post("/forgot-password", status_code=202)
async def forgot_password(
    data: Address, request: Request, tasks: BackgroundTasks, session: AsyncSession = Depends(db, scope="function")
):
    require_mail()
    email = data.email.lower()
    await request_limit(request, "reset", email)
    user = await session.scalar(select(User).where(User.email == email))
    if user and user.password_hash and user.email_verified_at:
        await send_link(tasks, "reset", email, user_id=str(user.id), password_version=digest(user.password_hash))
    return {"detail": "If this address has a confirmed password account, you’ll receive a reset link."}


@router.post("/reset-password")
async def reset_password(data: NewPassword, request: Request, session: AsyncSession = Depends(db)):
    key, claims = await read_link(request, "reset", data.token)
    user = await session.get(User, uuid.UUID(claims["user_id"]), with_for_update=True)
    if (
        not user
        or user.email != claims["email"]
        or not user.email_verified_at
        or not user.password_hash
        or digest(user.password_hash) != claims["password_version"]
    ):
        raise invalid_link()
    user.password_hash = await hash_password(data.password)
    await session.execute(update(LoginSession).where(LoginSession.user_id == user.id).values(revoked_at=now()))
    audit(session, user, "password_reset")
    await consume_link(key)
    await session.commit()
    return {"ok": True}


@router.post("/add-password")
async def add_password(
    data: AddPassword, request: Request, auth=Depends(principal), session: AsyncSession = Depends(db)
):
    key, claims = await read_link(request, "add-password", data.token)
    if str(auth[1].id) != claims["session_id"]:
        raise HTTPException(403, "Open this link in the browser where you started adding a password")
    user = await session.get(User, auth[0].id, populate_existing=True, with_for_update=True)
    family = await session.get(LoginSession, auth[1].id, populate_existing=True, with_for_update=True)
    if (
        not user
        or user.email != claims["email"]
        or user.password_hash
        or user.google_sub != claims["google_sub"]
        or not family
        or family.revoked_at
        or family.expires_at <= now()
    ):
        raise invalid_link()
    if not await verify_factor(session, user, data.code):
        raise HTTPException(403, "Invalid or already used authentication code")
    user.password_hash = await hash_password(data.password)
    user.email_verified_at = now()
    audit(session, user, "password_added")
    await consume_link(key)
    await session.commit()
    return {"ok": True}
