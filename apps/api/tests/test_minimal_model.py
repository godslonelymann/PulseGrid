import uuid
from datetime import timedelta

import pytest
from pulsegrid.storage.db import Base, Session, now
from pulsegrid.storage.models import (
    Channel,
    Heartbeat,
    Incident,
    LoginSession,
    Monitor,
    MonitorVersion,
    Outbox,
    RefreshToken,
    Result,
)
from pulsegrid.worker.runner import claim, retention_once
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from test_monitoring import sample, setup_monitor


async def test_results_keep_their_original_configuration(owner, fixture_server):
    service, mid = await setup_monitor(owner)
    await sample(mid, True)
    changed = await owner.put(
        f"/api/v1/monitors/{mid}",
        json={"url": "http://127.0.0.1:18080/new-target", "name": "New endpoint", "interval": 60, "timeout": 2},
    )
    assert changed.status_code == 200, changed.text
    await sample(mid, True)
    versions = (await owner.get(f"/api/v1/monitors/{mid}/versions")).json()
    checks = (await owner.get(f"/api/v1/monitors/{mid}/results")).json()
    by_id = {v["id"]: v for v in versions}
    assert len(versions) == len(checks) == 2
    assert by_id[checks[0]["monitor_version_id"]]["url"].endswith("/new-target")
    assert by_id[checks[1]["monitor_version_id"]]["url"] == service["url"]
    assert by_id[checks[1]["monitor_version_id"]]["interval"] == 30
    assert all(v["settings_known"] for v in versions)


async def test_archive_preserves_history_and_restore_resumes(owner, fixture_server):
    _, mid = await setup_monitor(owner)
    await sample(mid, True)
    assert (await owner.post(f"/api/v1/monitors/{mid}/archive")).status_code == 200
    assert (await owner.get("/api/v1/dashboard")).json()["monitors"] == []
    archived = (await owner.get("/api/v1/dashboard?archived=true")).json()["monitors"]
    assert len(archived) == 1 and archived[0]["archived_at"]
    assert len((await owner.get(f"/api/v1/monitors/{mid}/results")).json()) == 1
    assert (await owner.post(f"/api/v1/monitors/{mid}/resume")).status_code == 409
    assert await claim(10) == []
    assert (await owner.post(f"/api/v1/monitors/{mid}/restore")).status_code == 200
    assert len((await owner.get("/api/v1/dashboard")).json()["monitors"]) == 1
    await sample(mid, True)
    assert len((await owner.get(f"/api/v1/monitors/{mid}/results")).json()) == 2
    assert (await owner.delete(f"/api/v1/monitors/{mid}")).status_code == 409
    assert (await owner.delete(f"/api/v1/monitors/{mid}?confirm_history=true")).status_code == 200
    assert (await owner.get(f"/api/v1/monitors/{mid}/results")).status_code == 404


@pytest.mark.parametrize(
    "values",
    [
        {"interval": 0},
        {"timeout": 30},
        {"method": "POST"},
        {"statuses": []},
        {"statuses": [99]},
        {"statuses": [200, None]},
    ],
)
async def test_database_rejects_invalid_monitor_settings(owner, fixture_server, values):
    _, mid = await setup_monitor(owner)
    with pytest.raises(IntegrityError):
        async with Session.begin() as session:
            await session.execute(update(Monitor).where(Monitor.id == mid).values(**values))


async def test_database_rejects_check_linked_to_another_monitors_version(owner, fixture_server):
    _, first = await setup_monitor(owner)
    _, second = await setup_monitor(owner)
    async with Session() as session:
        other_version = await session.scalar(select(MonitorVersion.id).where(MonitorVersion.monitor_id == second))
    with pytest.raises(IntegrityError):
        async with Session.begin() as session:
            session.add(
                Result(
                    monitor_id=first,
                    monitor_version_id=other_version,
                    scheduled_for=now(),
                    observed_at=now(),
                    success=True,
                    status_code=200,
                    latency_ms=1,
                    lag_ms=0,
                )
            )


async def test_retention_keeps_live_token_replay_records_and_pending_alerts(owner, fixture_server):
    _, mid = await setup_monitor(owner)
    before = (await owner.get("/api/v1/me")).json()
    assert (await owner.post("/api/v1/auth/refresh")).status_code == 200
    owner.headers["x-csrf-token"] = owner.cookies["pg_csrf"]
    async with Session.begin() as session:
        expired = LoginSession(
            user_id=uuid.UUID(before["id"]), expires_at=now() - timedelta(days=1), label="Expired fixture"
        )
        channel = Channel(owner_id=uuid.UUID(before["id"]), name="Fixture", kind="email", config="fixture-not-sent")
        old_incident = Incident(
            monitor_id=mid, opened_at=now() - timedelta(days=402), closed_at=now() - timedelta(days=401)
        )
        session.add_all([expired, channel, old_incident])
        await session.flush()
        expired_id = expired.id
        session.add(RefreshToken(session_id=expired.id, digest="f" * 64, expires_at=expired.expires_at))
        pending = Outbox(
            channel_id=channel.id,
            incident_id=old_incident.id,
            event_kind="outage",
            payload={"incident_id": str(old_incident.id)},
        )
        session.add_all(
            [
                pending,
                Outbox(channel_id=channel.id, event_kind="test", payload={}, delivered_at=now() - timedelta(days=100)),
            ]
        )
        session.add_all(
            [
                Heartbeat(id="old", seen_at=now() - timedelta(days=2), inflight=0),
                Heartbeat(id="live", seen_at=now(), inflight=0),
            ]
        )
        await session.flush()
        pending_id = pending.id
    await retention_once()
    async with Session() as session:
        assert await session.get(LoginSession, expired_id) is None
        assert (
            await session.scalar(
                select(func.count(RefreshToken.id)).where(RefreshToken.session_id == uuid.UUID(before["session_id"]))
            )
            == 2
        )
        alert = await session.get(Outbox, pending_id)
        assert alert is not None and alert.incident_id is None and not alert.exhausted
        assert await session.scalar(select(func.count(Outbox.id))) == 1
        assert await session.get(Heartbeat, "old") is None
        assert await session.get(Heartbeat, "live") is not None
    assert len(Base.metadata.tables) == 13
    assert not {"buckets", "notification_rules", "monitor_activity", "session_families"} & Base.metadata.tables.keys()


async def test_expired_refresh_token_is_rejected_even_with_live_session(owner):
    async with Session.begin() as session:
        await session.execute(update(RefreshToken).values(expires_at=now() - timedelta(seconds=1)))
    response = await owner.post("/api/v1/auth/refresh")
    assert response.status_code == 401
    assert (await owner.get("/api/v1/me")).status_code == 401
