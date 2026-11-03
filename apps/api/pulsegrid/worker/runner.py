import asyncio
import contextlib
import json
import math
import random
import signal
import uuid
from collections import defaultdict
from datetime import timedelta
from urllib.parse import urlsplit

import aiosmtplib
import httpx
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from pulsegrid.auth.security import decrypt
from pulsegrid.config import settings
from pulsegrid.domain.health import transition
from pulsegrid.storage.db import Session, now
from pulsegrid.storage.models import (
    Audit,
    Channel,
    Heartbeat,
    Incident,
    LoginSession,
    Monitor,
    MonitorVersion,
    Outbox,
    Result,
    Runtime,
    User,
)
from pulsegrid.worker.probe import SafeTransport, probe, validate_url

cfg = settings()
EXTERNAL_HEARTBEAT_INTERVAL = 60
MAX_CHECK_OVERDUE = timedelta(minutes=2)


def log(event, **fields):
    print(json.dumps({"event": event, "time": now().isoformat(), **fields}), flush=True)


async def claim(limit):
    if limit <= 0:
        return []
    async with Session.begin() as s:
        rows = (
            await s.execute(
                select(Runtime, Monitor, MonitorVersion.id)
                .join(Monitor, Runtime.id == Monitor.id)
                .join(MonitorVersion, (MonitorVersion.monitor_id == Monitor.id) & MonitorVersion.ends_at.is_(None))
                .where(
                    ~Monitor.paused,
                    Monitor.archived_at.is_(None),
                    Runtime.due_at <= now(),
                    or_(Runtime.lease_until.is_(None), Runtime.lease_until < now()),
                )
                .order_by(Runtime.due_at)
                .limit(limit)
                .with_for_update(of=Runtime, skip_locked=True)
            )
        ).all()
        jobs = []
        for r, m, snapshot_id in rows:
            r.lease_id = uuid.uuid4()
            r.lease_version = m.version
            r.lease_until = now() + timedelta(seconds=cfg.lease_seconds)
            jobs.append(
                {
                    "id": m.id,
                    "monitor_version_id": snapshot_id,
                    "lease_id": r.lease_id,
                    "version": m.version,
                    "scheduled": r.due_at,
                    "url": m.url,
                    "method": m.method,
                    "statuses": m.statuses,
                    "timeout": m.timeout,
                }
            )
        return jobs


async def renew(job):
    while True:
        await asyncio.sleep(cfg.lease_seconds / 3)
        async with Session.begin() as s:
            row = await s.execute(
                update(Runtime)
                .where(Runtime.id == job["id"], Runtime.lease_id == job["lease_id"], Runtime.lease_until > now())
                .values(lease_until=now() + timedelta(seconds=cfg.lease_seconds))
                .returning(Runtime.id)
            )
            if row.scalar_one_or_none() is None:
                return


async def commit_result(job, result, lag_ms):
    async with Session.begin() as s:
        m = await s.scalar(select(Monitor).where(Monitor.id == job["id"]).with_for_update())
        r = await s.scalar(select(Runtime).where(Runtime.id == job["id"]).with_for_update())
        at = now()
        if (
            not m
            or not r
            or m.archived_at is not None
            or m.paused
            or m.version != job["version"]
            or r.lease_id != job["lease_id"]
            or not r.lease_until
            or r.lease_until <= at
        ):
            log("stale_result_rejected", monitor_id=str(job["id"]))
            return False
        incident = await s.scalar(select(Incident).where(Incident.monitor_id == m.id, Incident.closed_at.is_(None)))
        gap = r.last_observed is None or (at - r.last_observed).total_seconds() > m.interval * 2 + m.timeout
        r.state, r.failures, r.successes, event = transition(
            r.state, r.failures, r.successes, result.success, gap, incident is not None
        )
        check = Result(
            monitor_id=m.id,
            monitor_version_id=job["monitor_version_id"],
            scheduled_for=job["scheduled"],
            observed_at=at,
            success=result.success,
            status_code=result.status_code,
            latency_ms=result.latency_ms,
            error=result.error,
            lag_ms=lag_ms,
        )
        s.add(check)
        await s.flush()
        if event == "outage":
            incident = Incident(monitor_id=m.id, opened_at=at, opening_result_id=check.id)
            s.add(incident)
            await s.flush()
        elif event == "recovery":
            incident.closed_at = at
            incident.closing_result_id = check.id
        if event:
            channels = await s.scalars(select(Channel).where(Channel.owner_id == m.owner_id, Channel.enabled))
            for channel in channels:
                s.add(
                    Outbox(
                        incident_id=incident.id,
                        channel_id=channel.id,
                        event_kind=event,
                        payload={
                            "event_kind": event,
                            "monitor": m.name,
                            "url": m.url,
                            "monitor_version_id": str(job["monitor_version_id"]),
                            "detected_at": at.isoformat(),
                            "reason": result.error or "Successful checks",
                            "incident_id": str(incident.id),
                            "link": f"{cfg.public_origin}/?monitor={m.id}",
                        },
                    )
                )
        r.last_observed = at
        # Missed slots are skipped, and remain visible through coverage.
        slots = max(1, math.floor((at - job["scheduled"]).total_seconds() / m.interval) + 1)
        r.due_at = job["scheduled"] + timedelta(seconds=slots * m.interval)
        r.lease_id = r.lease_until = None
        return True


async def execute(job, target_slots):
    renewal = asyncio.create_task(renew(job))
    try:
        async with target_slots:
            lag = max(0, (now() - job["scheduled"]).total_seconds() * 1000)
            result = await probe(job["url"], job["method"], job["statuses"], job["timeout"])
            await commit_result(job, result, lag)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log("job_failed", monitor_id=str(job["id"]), error_type=type(exc).__name__)
    finally:
        renewal.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await renewal


async def claim_delivery():
    async with Session.begin() as s:
        row = await s.scalar(
            select(Outbox)
            .join(Channel)
            .where(
                Outbox.delivered_at.is_(None),
                ~Outbox.exhausted,
                Channel.enabled,
                Outbox.next_attempt <= now(),
                or_(Outbox.lease_until.is_(None), Outbox.lease_until < now()),
            )
            .order_by(Outbox.next_attempt)
            .limit(1)
            .with_for_update(of=Outbox, skip_locked=True)
        )
        if not row:
            return None
        row.lease_id, row.lease_until = uuid.uuid4(), now() + timedelta(seconds=60)
        channel = await s.get(Channel, row.channel_id)
        recipient = (await s.get(User, channel.owner_id)).email if channel.kind == "email" else decrypt(channel.config)
        return {
            "id": row.id,
            "lease_id": row.lease_id,
            "kind": channel.kind,
            "destination": recipient,
            "payload": {**row.payload, "event_id": str(row.id)},
            "attempts": row.attempts,
        }


async def send_delivery(job):
    async with asyncio.timeout(20):
        if job["kind"] == "webhook":
            validate_url(job["destination"])
            async with httpx.AsyncClient(
                transport=SafeTransport(), trust_env=False, follow_redirects=False, timeout=15
            ) as client:
                async with client.stream(
                    "POST",
                    job["destination"],
                    json=job["payload"],
                    headers={"Idempotency-Key": str(job["id"]), "User-Agent": "PulseGrid/1.0"},
                ) as response:
                    if not 200 <= response.status_code < 300:
                        raise ValueError("Provider rejected delivery")
        else:
            from email.message import EmailMessage

            if not cfg.smtp_host or not cfg.smtp_from:
                raise ValueError("SMTP is not configured")
            message = EmailMessage()
            message["From"], message["To"] = cfg.smtp_from, job["destination"]
            message["Subject"] = f"PulseGrid: {job['payload'].get('event_kind', 'test')}"
            message["Message-ID"] = f"<{job['id']}@pulsegrid>"
            message.set_content(json.dumps(job["payload"], indent=2))
            await aiosmtplib.send(
                message,
                hostname=cfg.smtp_host,
                port=cfg.smtp_port,
                username=cfg.smtp_user or None,
                password=cfg.smtp_password or None,
                start_tls=cfg.smtp_starttls,
                timeout=15,
            )


async def finish_delivery(job, error=None):
    async with Session.begin() as s:
        row = await s.scalar(
            select(Outbox)
            .where(Outbox.id == job["id"], Outbox.lease_id == job["lease_id"], Outbox.lease_until > now())
            .with_for_update()
        )
        if not row:
            return False
        row.attempts += 1
        row.lease_id = row.lease_until = None
        if error:
            row.last_error = "Notification provider unavailable or rejected request"
            row.exhausted = row.attempts >= 8
            row.next_attempt = now() + timedelta(seconds=min(3600, 5 * 2**row.attempts) + random.uniform(0, 5))
        else:
            row.delivered_at = now()
            row.last_error = None
        return True


async def dispatch_once():
    job = await claim_delivery()
    if not job:
        return False
    try:
        await send_delivery(job)
    except asyncio.CancelledError:
        raise
    except Exception:
        await finish_delivery(job, "failure")
    else:
        await finish_delivery(job)
    return True


async def notifications(stop):
    while not stop.is_set():
        try:
            if await dispatch_once():
                continue
        except Exception as exc:
            log("dispatcher_unavailable", error_type=type(exc).__name__)
        await asyncio.sleep(1)


async def retention_once():
    async with Session.begin() as s:
        ids = select(Result.id).where(Result.observed_at < now() - timedelta(days=cfg.raw_retention_days)).limit(1000)
        await s.execute(delete(Result).where(Result.id.in_(ids)))
        ids = select(Audit.id).where(Audit.created_at < now() - timedelta(days=cfg.security_retention_days)).limit(1000)
        await s.execute(delete(Audit).where(Audit.id.in_(ids)))
        ids = (
            select(Incident.id)
            .where(Incident.closed_at < now() - timedelta(days=cfg.incident_retention_days))
            .limit(100)
        )
        await s.execute(delete(Incident).where(Incident.id.in_(ids)))

        # Keep used refresh-token hashes until the whole session expires, so reuse remains detectable.
        ids = select(LoginSession.id).where(LoginSession.expires_at < now()).limit(1000)
        await s.execute(delete(LoginSession).where(LoginSession.id.in_(ids)))
        cutoff = now() - timedelta(days=cfg.security_retention_days)
        ids = (
            select(Outbox.id)
            .where(or_(Outbox.delivered_at < cutoff, Outbox.exhausted & (Outbox.next_attempt < cutoff)))
            .limit(1000)
        )
        await s.execute(delete(Outbox).where(Outbox.id.in_(ids)))
        ids = select(Heartbeat.id).where(Heartbeat.seen_at < now() - timedelta(days=1)).limit(1000)
        await s.execute(delete(Heartbeat).where(Heartbeat.id.in_(ids)))


async def retention(stop):
    while not stop.is_set():
        try:
            await retention_once()
        except Exception as exc:
            log("retention_unavailable", error_type=type(exc).__name__)
        # Short sleeps allow responsive shutdown; work runs only once per minute.
        for _ in range(60):
            if stop.is_set():
                return
            await asyncio.sleep(1)


async def send_external_heartbeat(client, url):
    try:
        # A live scheduler alone does not prove it is recording checks.
        async with Session() as s:
            overdue = await s.scalar(
                select(Runtime.id)
                .join(Monitor)
                .where(~Monitor.paused, Monitor.archived_at.is_(None), Runtime.due_at < now() - MAX_CHECK_OVERDUE)
                .limit(1)
            )
        if overdue:
            log("external_heartbeat_skipped", reason="overdue_checks")
            return
        async with asyncio.timeout(5), client.stream("GET", url) as response:
            if not response.is_success:
                log("external_heartbeat_rejected", status=response.status_code)
    except Exception as exc:
        # Request URLs contain a secret. Do not log the URL or exception message.
        log("external_heartbeat_unavailable", error_type=type(exc).__name__)


async def main():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    worker_id = str(uuid.uuid4())
    target_slots = defaultdict(lambda: asyncio.Semaphore(2))
    tasks = set()
    background = [asyncio.create_task(notifications(stop)), asyncio.create_task(retention(stop))]
    heartbeat_url = cfg.worker_heartbeat_url.get_secret_value()
    heartbeat_client = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=5) if heartbeat_url else None
    heartbeat_task = None
    next_ping = loop.time()
    log("worker_started", worker_id=worker_id, concurrency=cfg.worker_concurrency)
    try:
        while not stop.is_set():
            tasks = {t for t in tasks if not t.done()}
            try:
                jobs = await claim(cfg.worker_concurrency - len(tasks))
                for job in jobs:
                    p = urlsplit(job["url"])
                    tasks.add(
                        asyncio.create_task(
                            execute(job, target_slots[(p.hostname, p.port or (443 if p.scheme == "https" else 80))])
                        )
                    )
                async with Session.begin() as s:
                    await s.execute(
                        insert(Heartbeat)
                        .values(id=worker_id, seen_at=now(), inflight=len(tasks))
                        .on_conflict_do_update(
                            index_elements=[Heartbeat.id], set_={"seen_at": now(), "inflight": len(tasks)}
                        )
                    )
                if (
                    heartbeat_client is not None
                    and loop.time() >= next_ping
                    and (heartbeat_task is None or heartbeat_task.done())
                ):
                    next_ping = loop.time() + EXTERNAL_HEARTBEAT_INTERVAL
                    heartbeat_task = asyncio.create_task(send_external_heartbeat(heartbeat_client, heartbeat_url))
            except Exception as exc:
                log("scheduler_unavailable", error_type=type(exc).__name__)
                await asyncio.sleep(1)
            await asyncio.sleep(0.2)
    finally:
        if heartbeat_task is not None:
            background.append(heartbeat_task)
        for t in background:
            t.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        if heartbeat_client is not None:
            await heartbeat_client.aclose()
        # Graceful shutdown drains bounded work; SIGKILL is recovered by lease expiry.
        await asyncio.gather(*tasks, return_exceptions=True)
        log("worker_stopped", worker_id=worker_id)


if __name__ == "__main__":
    asyncio.run(main())
