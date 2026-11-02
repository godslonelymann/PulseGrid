"""Destructive faults touch only the repository-owned native test services."""

import asyncio
import os
import subprocess
import sys

from pulsegrid.auth.security import redis
from pulsegrid.storage.db import Session, now
from pulsegrid.storage.models import Result, Runtime
from pulsegrid.worker.probe import ProbeResult
from pulsegrid.worker.runner import claim, commit_result
from sqlalchemy import func, select
from test_monitoring import setup_monitor


async def dependency(action, service):
    p = await asyncio.create_subprocess_exec(
        sys.executable,
        "scripts/native-services.py",
        action,
        "test",
        service,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await p.communicate()
    assert p.returncode == 0, output.decode()


async def test_redis_outage_fails_closed_and_recovers(owner):
    try:
        await dependency("down", "redis")
        r = await owner.get("/api/v1/me")
        assert r.status_code == 503, r.text
        assert (
            await owner.post(
                "/api/v1/auth/login", json={"email": "owner@example.com", "password": "correct-password-123"}
            )
        ).status_code == 503
    finally:
        await dependency("up", "redis")
    for _ in range(40):
        try:
            await redis.ping()
            break
        except Exception:
            await asyncio.sleep(0.25)
    assert (await owner.get("/api/v1/me")).status_code == 200


async def test_postgres_outage_first_request_and_history(owner, fixture_server):
    _, mid = await setup_monitor(owner)
    from test_monitoring import sample

    await sample(mid, False)
    await sample(mid, False)
    try:
        await dependency("down", "postgres")
        assert (await owner.get("/api/v1/dashboard")).status_code == 503
        assert (await owner.get("/api/v1/health/ready")).status_code == 503
    finally:
        await dependency("up", "postgres")
    # No warm-up request: connection checkout health must recover the first request.
    r = await owner.get("/api/v1/me")
    assert r.status_code == 200, r.text
    incidents = (await owner.get(f"/api/v1/monitors/{mid}/incidents")).json()
    assert len(incidents) == 1 and incidents[0]["closed_at"] is None


async def test_hard_worker_kill_natural_lease_expiry(owner, fixture_server):
    _, mid = await setup_monitor(owner)
    fixture_server["delay"] = 5
    with open("verification/crash-worker.log", "w") as log:
        worker = subprocess.Popen(
            [".venv/bin/python", "-m", "pulsegrid.worker.runner"],
            env={**os.environ, "PYTHONPATH": "apps/api"},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            until = None
            for _ in range(100):
                async with Session() as s:
                    r = await s.get(Runtime, mid)
                    if r.lease_id:
                        until = r.lease_until
                        break
                await asyncio.sleep(0.05)
            assert until is not None
            worker.kill()
            await asyncio.to_thread(worker.wait, 5)
            assert not await claim(1)
            # Let the real configured lease expire; no database time manipulation.
            await asyncio.sleep(max(0, (until - now()).total_seconds()) + 0.2)
            fixture_server["delay"] = 0
            jobs = await claim(1)
            assert len(jobs) == 1
            assert await commit_result(jobs[0], ProbeResult(True, 200, 1), 0)
            async with Session() as s:
                assert await s.scalar(select(func.count(Result.id))) == 1
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
