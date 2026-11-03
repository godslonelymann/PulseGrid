from sqlalchemy import select

from pulsegrid.auth.security import encrypt
from pulsegrid.storage.models import Channel


async def email_channels(session, user_id):
    return list(
        await session.scalars(
            select(Channel).where(Channel.owner_id == user_id, Channel.kind == "email").order_by(Channel.id)
        )
    )


def new_email_channel(user, enabled=True):
    return Channel(owner_id=user.id, kind="email", name="Email alerts", config=encrypt(user.email), enabled=enabled)
