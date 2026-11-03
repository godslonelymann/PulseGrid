"""Short-lived operator approval to convert one existing password account to Google."""

import hmac
import json

from sqlalchemy import select, update

from pulsegrid.auth.security import audit, digest, redis
from pulsegrid.storage.db import Session, now
from pulsegrid.storage.models import LoginSession, User

APPROVAL_SECONDS = 900


def approval_key(email):
    return f"google-migration:{digest(email.lower())}"


def approval_value(user):
    # A password or MFA change invalidates approval issued for older credentials.
    version = digest(json.dumps([user.password_hash, user.totp_secret]))
    return json.dumps({"user_id": str(user.id), "credential_version": version}, sort_keys=True)


async def authorize_google_migration(email):
    async with Session.begin() as session:
        user = await session.scalar(select(User).where(User.email == email).with_for_update())
        if user is None:
            raise ValueError("Account not found; new users should sign up with Google")
        if user.google_sub or not user.password_hash:
            raise ValueError("Account already uses Google")
        value = approval_value(user)
        audit(session, user, "google_migration_authorized")
    await redis.set(approval_key(email), value, ex=APPROVAL_SECONDS)


async def migrate_password_account(session, user, sub):
    """Caller holds the account row lock and has verified Google's same-email identity."""
    if user.google_sub or not user.password_hash:
        return False
    key = approval_key(user.email)
    value = await redis.get(key)
    if value is None or not hmac.compare_digest(value, approval_value(user)):
        return False
    consumed = await redis.eval(
        "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end",
        1,
        key,
        value,
    )
    if not consumed:
        return False
    user.google_sub = sub
    user.password_hash = None
    # Keep the user ID, ownership, monitors/history and any existing MFA requirement.
    await session.execute(update(LoginSession).where(LoginSession.user_id == user.id).values(revoked_at=now()))
    audit(session, user, "google_account_migrated")
    return True
