import argparse
import asyncio
import getpass

from sqlalchemy import delete, select, text, update

from pulsegrid.auth.google_migration import authorize_google_migration
from pulsegrid.auth.security import cfg, hasher
from pulsegrid.storage.db import Session, now
from pulsegrid.storage.models import Audit, LoginSession, RecoveryCode, User


async def run(args):
    from email_validator import validate_email

    args.email = validate_email(args.email, check_deliverability=False).normalized.lower()
    if args.command == "allow-google-link":
        try:
            await authorize_google_migration(args.email)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        print("Google migration approved for 15 minutes. Sign in with the matching Google account now.")
        print(
            "Existing monitors/history stay with the account; its password is removed only after Google verification."
        )
        return
    if not cfg.enable_legacy_password_auth:
        raise SystemExit(
            "Password accounts are disabled. Use Google sign-up or allow-google-link for an existing account."
        )
    password = getpass.getpass("New password (12+ characters): ")
    if len(password) < 12 or password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords must match and contain at least 12 characters")
    async with Session.begin() as s:
        await s.execute(text("SELECT pg_advisory_xact_lock(829715)"))
        user = await s.scalar(select(User).where(User.email == args.email.lower()).with_for_update())
        if args.command == "bootstrap":
            if await s.scalar(select(User.id).limit(1)):
                raise SystemExit("Owner exists; registration is closed")
            user = User(email=args.email.lower(), password_hash=hasher.hash(password))
            s.add(user)
            await s.flush()
            s.add(Audit(user_id=user.id, kind="owner_bootstrapped"))
        else:
            if not user:
                raise SystemExit("Account not found")
            user.password_hash, user.totp_secret, user.last_totp_step = hasher.hash(password), None, None
            await s.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
            await s.execute(update(LoginSession).where(LoginSession.user_id == user.id).values(revoked_at=now()))
            s.add(Audit(user_id=user.id, kind="operator_recovery"))
    print("Account ready; password was not logged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local operator account management")
    parser.add_argument("command", choices=["allow-google-link", "bootstrap", "recover"])
    parser.add_argument("email")
    asyncio.run(run(parser.parse_args()))
