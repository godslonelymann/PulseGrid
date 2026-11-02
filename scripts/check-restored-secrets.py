"""Run with the selected staging environment after restoring its encrypted data."""

import asyncio
import json

import pyotp
from pulsegrid.auth.security import decrypt
from pulsegrid.storage.db import Session, engine
from pulsegrid.storage.models import Channel, User
from sqlalchemy import select


async def main():
    async with Session() as s:
        user = await s.scalar(select(User).where(User.email == "staging@example.com"))
        channel = await s.scalar(select(Channel).where(Channel.name == "Encrypted restore fixture").limit(1))
        assert user and user.totp_secret and channel
        secret = decrypt(user.totp_secret)
        assert pyotp.TOTP(secret).verify(pyotp.TOTP(secret).now())
        assert decrypt(channel.config) in (user.email, "restore@example.com")
        print(
            json.dumps(
                {"restored_totp_decrypts": True, "restored_destination_decrypts": True, "secret_values_logged": False}
            )
        )
    await engine.dispose()


asyncio.run(main())
