import argparse
import asyncio
import getpass

from sqlalchemy import delete, select, text, update

from pulsegrid.api.email_alerts import new_email_channel
from pulsegrid.auth.google_migration import authorize_google_migration
from pulsegrid.auth.security import hasher
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
    password = getpass.getpass("New password (12+ characters): ")
    if len(password) < 12 or password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords must match and contain at least 12 characters")
    async with Session.begin() as s:
        await s.execute(text("SELECT pg_advisory_xact_lock(829715)"))
        user = await s.scalar(select(User).where(User.email == args.email.lower()).with_for_update())
        # The operator vouches for the address, so the account counts as email-confirmed
        # and can use normal password sign-in without ENABLE_LEGACY_PASSWORD_AUTH.
        if args.command == "bootstrap":
            if await s.scalar(select(User.id).limit(1)):
                raise SystemExit("Owner exists; use 'recover' to reset an existing account")
            user = User(email=args.email.lower(), password_hash=hasher.hash(password), email_verified_at=now())
            s.add(user)
            await s.flush()
            s.add(new_email_channel(user))
            s.add(Audit(user_id=user.id, kind="owner_bootstrapped"))
        else:
            if not user:
                raise SystemExit("Account not found")
            user.password_hash, user.totp_secret, user.last_totp_step = hasher.hash(password), None, None
            user.email_verified_at = user.email_verified_at or now()
            await s.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
            await s.execute(update(LoginSession).where(LoginSession.user_id == user.id).values(revoked_at=now()))
            s.add(Audit(user_id=user.id, kind="operator_recovery"))
    print("Account ready; password was not logged")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local operator account management")
    parser.add_argument("command", choices=["allow-google-link", "bootstrap", "recover"])
    parser.add_argument("email")
    asyncio.run(run(parser.parse_args()))
