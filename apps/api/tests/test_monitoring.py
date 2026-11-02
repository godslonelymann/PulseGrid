import asyncio
import uuid
from datetime import timedelta

import httpx
import pytest
from conftest import login, seed
from pulsegrid.api.main import app
from pulsegrid.storage.db import Session, now
from pulsegrid.storage.models import Incident, MonitorVersion, Outbox, Result, Runtime
from pulsegrid.worker.probe import ProbeResult
from pulsegrid.worker.runner import claim, claim_delivery, commit_result, dispatch_once, finish_delivery
from sqlalchemy import func, select, update


async def setup_monitor(owner):
    r = await owner.post(
        "/api/v1/monitors",
        json={"name": "Fixture", "url": "http://127.0.0.1:18080/", "interval": 30, "timeout": 1},
    )
    assert r.status_code == 201, r.text
    mid = uuid.UUID(r.json()["id"])
    async with Session.begin() as s:
        await s.execute(update(Runtime).where(Runtime.id == mid).values(due_at=now() - timedelta(seconds=1)))
    return r.json(), mid


async def due(mid):
    async with Session.begin() as s:
        await s.execute(update(Runtime).where(Runtime.id == mid).values(due_at=now() - timedelta(seconds=1)))


async def sample(mid, ok):
    await due(mid)
    jobs = await claim(1)
    assert len(jobs) == 1
    assert await commit_result(jobs[0], ProbeResult(ok, 200 if ok else 503, 12, None if ok else "HTTP 503"), 0)


async def test_ownership_all_resources(owner, fixture_server):
    b, mid = await setup_monitor(owner)
    c = (
        await owner.post(
            "/api/v1/notification-channels",
            json={"name": "Alerts", "kind": "webhook", "destination": "http://127.0.0.1:18080/"},
        )
    ).json()
    await seed("other@example.com")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as other:
        await other.get("/api/v1/auth/csrf")
        other.headers.update({"origin": "http://localhost:3000", "x-csrf-token": other.cookies["pg_csrf"]})
        await login(other, "other@example.com")
        for suffix in ("", "/results", "/metrics", "/incidents"):
            assert (await other.get(f"/api/v1/monitors/{mid}{suffix}")).status_code == 404
        assert (await other.delete(f"/api/v1/monitors/{mid}?confirm_history=true")).status_code == 404
        assert (await other.post(f"/api/v1/monitors/{mid}/pause")).status_code == 404
        assert (
            await other.put(f"/api/v1/monitors/{mid}", json={"name": "stolen", "url": "https://example.com/"})
        ).status_code == 404
        assert (await other.post(f"/api/v1/notification-channels/{c['id']}/test")).status_code == 404
        assert (await other.get("/api/v1/dashboard")).json()["monitors"] == []
        assert (await other.get("/api/v1/notification-channels")).json() == []


async def test_transactional_incidents_outbox_retries(owner, fixture_server):
    _, mid = await setup_monitor(owner)
    (
        await owner.post(
            "/api/v1/notification-channels",
            json={"name": "Alerts", "kind": "webhook", "destination": "http://127.0.0.1:18080/"},
        )
    ).json()
    for ok in (True, False, False, True):
        await sample(mid, ok)
    async with Session() as s:
        assert await s.scalar(select(func.count(Result.id))) == 4
        incident = await s.scalar(select(Incident))
        assert incident.closed_at is None
        assert await s.scalar(select(func.count(Outbox.id))) == 1
    fixture_server["status"] = 503
    assert await dispatch_once()
    async with Session() as s:
        o = await s.scalar(select(Outbox))
        assert o.attempts == 1 and o.delivered_at is None and o.next_attempt > now()
        oid = o.id
    fixture_server["status"] = 200
    await owner.post(f"/api/v1/deliveries/{oid}/retry")
    assert await dispatch_once()
    await sample(mid, True)
    assert await dispatch_once()
    async with Session() as s:
        assert (await s.scalar(select(Incident))).closed_at is not None
        rows = (await s.scalars(select(Outbox))).all()
        assert len(rows) == 2 and all(o.delivered_at for o in rows)
    payloads = [__import__("json").loads(x) for x in fixture_server["payloads"]]
    assert payloads[0]["event_id"] == payloads[1]["event_id"]
    assert payloads[-1]["event_kind"] == "recovery"


async def test_competing_workers_leases_and_stale_commit(owner, fixture_server):
    b, mid = await setup_monitor(owner)
    a, batches = await asyncio.gather(claim(1), claim(1))
    assert len(a) + len(batches) == 1
    old = (a or batches)[0]
    async with Session.begin() as s:
        await s.execute(update(Runtime).where(Runtime.id == mid).values(lease_until=now() - timedelta(seconds=1)))
    fresh = (await claim(1))[0]
    assert not await commit_result(old, ProbeResult(True, 200, 1), 0)
    assert await commit_result(fresh, ProbeResult(True, 200, 1), 0)
    assert not await commit_result(fresh, ProbeResult(True, 200, 1), 0)
    async with Session() as s:
        assert await s.scalar(select(func.count(Result.id))) == 1


@pytest.mark.parametrize("mutation", ["pause_monitor", "archive", "edit", "delete"])
async def test_configuration_fences_inflight_result(owner, fixture_server, mutation):
    b, mid = await setup_monitor(owner)
    job = (await claim(1))[0]
    if mutation == "pause_monitor":
        await owner.post(f"/api/v1/monitors/{mid}/pause")
    elif mutation == "archive":
        await owner.post(f"/api/v1/monitors/{mid}/archive")
    elif mutation == "delete":
        await owner.delete(f"/api/v1/monitors/{mid}?confirm_history=true")
    else:
        r = await owner.put(
            f"/api/v1/monitors/{mid}",
            json={
                "name": "changed",
                "url": "http://127.0.0.1:18080/",
                "interval": 60,
                "timeout": 1,
            },
        )
        assert r.status_code == 200, r.text
    assert not await commit_result(job, ProbeResult(True, 200, 1), 0)
    async with Session() as s:
        assert await s.scalar(select(func.count(Result.id))) == 0


async def test_missed_schedules_metrics_pause_gap(owner, fixture_server):
    b, mid = await setup_monitor(owner)
    for ok in (True, False, True):
        await sample(mid, ok)
    async with Session.begin() as s:
        await s.execute(
            update(MonitorVersion)
            .where(MonitorVersion.monitor_id == mid)
            .values(starts_at=now() - timedelta(seconds=300))
        )
        await s.execute(update(Runtime).where(Runtime.id == mid).values(due_at=now() - timedelta(seconds=150)))
    job = (await claim(1))[0]
    assert await commit_result(job, ProbeResult(True, 200, 4), 0)
    async with Session() as s:
        r = await s.get(Runtime, mid)
        assert now() < r.due_at < now() + timedelta(seconds=30)
    metrics = (await owner.get(f"/api/v1/monitors/{mid}/metrics")).json()
    assert metrics["completed"] == 4 and metrics["uptime"] == 0.75 and metrics["missing"] >= 5
    await owner.post(f"/api/v1/monitors/{mid}/pause")
    paused = (await owner.get(f"/api/v1/monitors/{mid}")).json()
    assert paused["state"] == "PAUSED" and paused["last_observed"] is not None
    await owner.post(f"/api/v1/monitors/{mid}/resume")
    assert (await owner.get(f"/api/v1/monitors/{mid}")).json()["state"] == "UNKNOWN"


async def test_ambiguous_notification_ack_and_exhaustion(owner, fixture_server):
    c = (
        await owner.post(
            "/api/v1/notification-channels",
            json={"name": "Alerts", "kind": "webhook", "destination": "http://127.0.0.1:18080/"},
        )
    ).json()
    await owner.post(f"/api/v1/notification-channels/{c['id']}/test")
    from pulsegrid.worker.runner import send_delivery

    first = await claim_delivery()
    await send_delivery(first)  # provider accepted, process dies before durable acknowledgement
    async with Session.begin() as s:
        await s.execute(
            update(Outbox).where(Outbox.lease_id.is_not(None)).values(lease_until=now() - timedelta(seconds=1))
        )
    second = await claim_delivery()
    assert second["id"] == first["id"]
    assert not await finish_delivery(first)
    await send_delivery(second)
    assert await finish_delivery(second)
    assert len(fixture_server["payloads"]) == 2
    assert fixture_server["payloads"][0] == fixture_server["payloads"][1]
    await owner.post(f"/api/v1/notification-channels/{c['id']}/test")
    fixture_server["status"] = 500
    for _ in range(8):
        async with Session.begin() as s:
            await s.execute(update(Outbox).where(Outbox.delivered_at.is_(None)).values(next_attempt=now()))
        assert await dispatch_once()
    assert not await dispatch_once()
    rows = (await owner.get("/api/v1/deliveries")).json()
    assert any(r["exhausted"] and r["attempts"] == 8 for r in rows)


async def test_lease_renewal_including_queue_wait(owner, fixture_server, monkeypatch):
    from pulsegrid.worker import runner

    _, first = await setup_monitor(owner)
    _, second = await setup_monitor(owner)
    monkeypatch.setattr(runner.cfg, "lease_seconds", 0.3)
    jobs = await claim(2)
    fixture_server["delay"] = 2
    slots = asyncio.Semaphore(1)
    await asyncio.gather(*(runner.execute(job, slots) for job in jobs))
    async with Session() as s:
        assert await s.scalar(select(func.count(Result.id))) == 2
        assert all(r.lease_id is None for r in await s.scalars(select(Runtime)))


async def test_smtp_delivery_failure_then_retry(owner, monkeypatch):
    from pulsegrid.worker import runner

    messages = []
    state = {"reject": True}

    async def smtp(reader, writer):
        writer.write(b"220 fixture SMTP\r\n")
        await writer.drain()
        try:
            while line := await reader.readline():
                command = line.split(b" ", 1)[0].strip().upper()
                if command in (b"EHLO", b"HELO", b"MAIL", b"RSET"):
                    writer.write(b"250 OK\r\n")
                elif command == b"RCPT":
                    writer.write(b"550 rejected\r\n" if state["reject"] else b"250 OK\r\n")
                elif command == b"DATA":
                    writer.write(b"354 send data\r\n")
                    await writer.drain()
                    messages.append(await reader.readuntil(b"\r\n.\r\n"))
                    writer.write(b"250 accepted\r\n")
                elif command == b"QUIT":
                    writer.write(b"221 goodbye\r\n")
                    await writer.drain()
                    break
                else:
                    writer.write(b"250 OK\r\n")
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(smtp, "127.0.0.1", 18082)
    monkeypatch.setattr(runner.cfg, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(runner.cfg, "smtp_port", 18082)
    monkeypatch.setattr(runner.cfg, "smtp_from", "pulse@example.com")
    monkeypatch.setattr(runner.cfg, "smtp_starttls", False)
    try:
        channel = (
            await owner.post(
                "/api/v1/notification-channels",
                json={"name": "Mail", "kind": "email", "destination": "owner@example.com"},
            )
        ).json()
        item = (await owner.post(f"/api/v1/notification-channels/{channel['id']}/test")).json()
        assert await dispatch_once()
        async with Session() as s:
            assert (await s.get(Outbox, uuid.UUID(item["id"]))).attempts == 1
        state["reject"] = False
        await owner.post(f"/api/v1/deliveries/{item['id']}/retry")
        assert await dispatch_once()
        assert len(messages) == 1 and item["id"].encode() in messages[0]
        async with Session() as s:
            assert (await s.get(Outbox, uuid.UUID(item["id"]))).delivered_at
    finally:
        server.close()
        await server.wait_closed()


async def test_metrics_clip_retained_history_instead_of_reporting_purged_checks_missing(
    owner, fixture_server, monkeypatch
):
    from pulsegrid.api.main import cfg

    _, mid = await setup_monitor(owner)
    monkeypatch.setattr(cfg, "raw_retention_days", 1)
    async with Session.begin() as s:
        await s.execute(
            update(MonitorVersion).where(MonitorVersion.monitor_id == mid).values(starts_at=now() - timedelta(days=5))
        )
    metrics = (await owner.get(f"/api/v1/monitors/{mid}/metrics?hours=720")).json()
    assert metrics["retention_clipped"] is True
    assert 2879 <= metrics["expected"] <= 2880
