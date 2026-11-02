import uuid

from conftest import seed
from pulsegrid.auth.security import decrypt, encrypt
from pulsegrid.storage.db import Session
from pulsegrid.storage.models import Channel, Outbox
from pulsegrid.worker import runner


async def test_email_recipient_is_account_email_in_api_and_delivery(owner, monkeypatch):
    created = await owner.post(
        "/api/v1/notification-channels",
        json={
            "name": "Email alerts",
            "kind": "email",
        },
    )
    assert created.status_code == 201, created.text
    channel = created.json()
    assert channel["email"] == "owner@example.com"
    # A modified client cannot redirect the account's email alerts elsewhere.
    edited = await owner.put(
        f"/api/v1/notification-channels/{channel['id']}",
        json={
            "name": "Email alerts",
            "kind": "email",
            "destination": "someone-else@example.com",
        },
    )
    assert edited.status_code == 200 and edited.json()["email"] == "owner@example.com"
    async with Session.begin() as session:
        stored = await session.get(Channel, uuid.UUID(channel["id"]))
        assert decrypt(stored.config) == "owner@example.com"
        # Existing databases can contain addresses saved before this change.
        stored.config = encrypt("legacy-address@example.com")
    listed = (await owner.get("/api/v1/notification-channels")).json()
    assert listed[0]["email"] == "owner@example.com"

    assert (await owner.post(f"/api/v1/notification-channels/{channel['id']}/test")).status_code == 200
    first_job = await runner.claim_delivery()
    assert first_job["destination"] == "owner@example.com"
    second_id = await seed("second@example.com")
    async with Session.begin() as session:
        other = Channel(
            owner_id=second_id,
            name="Other account alerts",
            kind="email",
            config=encrypt("owner@example.com"),
        )
        session.add(other)
        await session.flush()
        session.add(Outbox(channel_id=other.id, event_kind="test", payload={"event_kind": "test"}))
    second_job = await runner.claim_delivery()
    assert second_job["destination"] == "second@example.com"

    deliveries = []

    async def smtp_send(message, **kwargs):
        deliveries.append((message["From"], message["To"]))

    monkeypatch.setattr(runner.cfg, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(runner.cfg, "smtp_from", "pulsegrid.sender@gmail.com")
    monkeypatch.setattr(runner.aiosmtplib, "send", smtp_send)
    await runner.send_delivery(first_job)
    await runner.send_delivery(second_job)
    assert deliveries == [
        ("pulsegrid.sender@gmail.com", "owner@example.com"),
        ("pulsegrid.sender@gmail.com", "second@example.com"),
    ]


async def test_webhook_still_requires_a_destination(owner):
    response = await owner.post("/api/v1/notification-channels", json={"name": "Webhook", "kind": "webhook"})
    assert response.status_code == 422
