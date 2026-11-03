import asyncio
import uuid
from datetime import timedelta

import httpx
from conftest import seed
from pulsegrid.storage.db import Session, now
from pulsegrid.storage.models import Monitor, MonitorVersion, Outbox, Result, Runtime
from pulsegrid.worker.runner import send_external_heartbeat
from sqlalchemy import select, update
from test_monitoring import setup_monitor


async def test_monitor_limit_serializes_creates_and_counts_archived_services(owner, fixture_server):
    user_id = uuid.UUID((await owner.get("/api/v1/me")).json()["id"])
    another_user = await seed("other@example.com")
    payload = {"name": "Fixture", "url": "http://127.0.0.1:18080/"}
    async with Session.begin() as session:
        session.add_all([Monitor(owner_id=user_id, archived_at=now(), **payload) for _ in range(19)])
        session.add(Monitor(owner_id=another_user, archived_at=now(), **payload))
    responses = await asyncio.gather(*(owner.post("/api/v1/monitors", json=payload) for _ in range(3)))
    assert sorted(response.status_code for response in responses) == [201, 409, 409]
    assert all("20-service limit" in r.json()["detail"] for r in responses if r.status_code == 409)
    dashboard = (await owner.get("/api/v1/dashboard")).json()
    assert dashboard["limit"] == dashboard["monitor_total"] == 20
    assert len(dashboard["monitors"]) == 1
    assert (await owner.delete(f"/api/v1/monitors/{dashboard['monitors'][0]['id']}?confirm_history=true")).is_success
    assert (await owner.post("/api/v1/monitors", json=payload)).status_code == 201


async def test_latency_trend_includes_old_checks_beyond_recent_page(owner, fixture_server):
    _, mid = await setup_monitor(owner)
    at = now()
    async with Session.begin() as session:
        version = await session.scalar(select(MonitorVersion.id).where(MonitorVersion.monitor_id == mid))
        samples = [
            (at - timedelta(days=29), 29, True),
            (at - timedelta(days=6), 60, True),
            (at - timedelta(days=3), 3000, False),
        ] + [(at - timedelta(minutes=i + 1), 10, True) for i in range(110)]
        session.add_all(
            Result(
                monitor_id=mid,
                monitor_version_id=version,
                observed_at=time,
                scheduled_for=time,
                latency_ms=latency,
                success=success,
                lag_ms=0,
            )
            for time, latency, success in samples
        )
    for hours, count, values in [(168, 168, {10, 60, None}), (720, 120, {10, 29, 60, None})]:
        result = await owner.get(f"/api/v1/monitors/{mid}/metrics?hours={hours}")
        assert result.is_success, result.text
        metrics = result.json()
        trend = metrics["latency_trend"]
        assert len(trend) == count and trend[0]["starts_at"] == metrics["window_start"]
        assert [b["starts_at"] for b in trend] == sorted(b["starts_at"] for b in trend)
        assert {b["median_ms"] for b in trend} == values


async def test_external_heartbeat_stops_for_overdue_active_checks(owner, fixture_server):
    _, mid = await setup_monitor(owner)
    async with Session.begin() as session:
        await session.execute(update(Runtime).where(Runtime.id == mid).values(due_at=now() - timedelta(minutes=3)))
    pings = []

    def record(request):
        pings.append(request.method)
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
        await send_external_heartbeat(client, "https://example.test/ping")
        assert not pings
        assert (await owner.post(f"/api/v1/monitors/{mid}/pause")).is_success
        await send_external_heartbeat(client, "https://example.test/ping")
        assert pings == ["GET"]


async def test_latest_test_uses_queue_order_and_failed_filter_shows_retries(owner):
    channel = (await owner.post("/api/v1/notification-channels", json={"name": "Mail", "kind": "email"})).json()
    path = f"/api/v1/notification-channels/{channel['id']}/test"
    first = (await owner.post(path)).json()["id"]
    latest = (await owner.post(path)).json()["id"]
    async with Session.begin() as session:
        older = await session.get(Outbox, uuid.UUID(first))
        older.attempts = 1
        older.next_attempt = now() + timedelta(hours=1)
        older.last_error = "Provider unavailable"
    latest_rows = (await owner.get("/api/v1/deliveries?event_kind=test&limit=1")).json()
    assert [row["id"] for row in latest_rows] == [latest]
    assert latest_rows[0]["test_queued_at"]
    failed_rows = (await owner.get("/api/v1/deliveries?failed=true")).json()
    assert [row["id"] for row in failed_rows] == [first]
    assert not failed_rows[0]["exhausted"]
