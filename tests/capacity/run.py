"""One hour against the real production worker, with isolated durable PostgreSQL."""

import asyncio
import json
import os
import platform
import statistics
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import psutil
from pulsegrid.api.main import snapshot
from pulsegrid.storage.db import Session, engine, now
from pulsegrid.storage.models import Monitor, Outbox, Result, Runtime, User
from sqlalchemy import func, select, text

OUT = Path(os.environ.get("CAPACITY_OUTPUT_DIR", "verification/capacity"))
OUT.mkdir(parents=True, exist_ok=True)


async def fixture(reader, writer, kind):
    try:
        await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
        if kind == "slow":
            await asyncio.sleep(0.25)
        if kind == "timeout":
            await asyncio.sleep(2)
        status = b"503 Service Unavailable" if kind == "failed" else b"200 OK"
        writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK")
        await writer.drain()
    except (OSError, TimeoutError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()


async def run():
    if (OUT / "samples.jsonl").exists() or (OUT / "report.json").exists():
        raise RuntimeError("Preserve previous measurements; set CAPACITY_OUTPUT_DIR to a fresh directory")
    duration = int(os.environ.get("CAPACITY_SECONDS", "3600"))
    kinds = ["fast"] * 70 + ["slow"] * 20 + ["failed"] * 5 + ["timeout"] * 5
    servers = []
    for i, kind in enumerate(kinds):
        servers.append(await asyncio.start_server(lambda r, w, k=kind: fixture(r, w, k), "127.0.0.1", 18100 + i))
    async with Session.begin() as s:
        if await s.scalar(select(func.count(Monitor.id))):
            raise RuntimeError("Capacity database must be empty; preserve earlier evidence before recreating it")
        user = User(email="capacity@example.test", password_hash="no-login-capacity-fixture")
        s.add(user)
        await s.flush()
        start = now() + timedelta(seconds=5)
        for i, kind in enumerate(kinds):
            m = Monitor(
                owner_id=user.id, name=f"{kind}-{i}", url=f"http://127.0.0.1:{18100 + i}/", interval=30, timeout=1
            )
            s.add(m)
            await s.flush()
            due = start + timedelta(seconds=i * 0.3)
            s.add(Runtime(id=m.id, due_at=due))
            s.add(snapshot(m, due))
    log = (OUT / "worker.log").open("w")
    worker = subprocess.Popen(
        [".venv/bin/python", "-m", "pulsegrid.worker.runner"],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )
    process = psutil.Process(worker.pid)
    process.cpu_percent()
    samples = []
    started = time.monotonic()
    deadline = start + timedelta(seconds=duration)
    (OUT / "status.json").write_text(
        json.dumps(
            {
                "status": "running",
                "started": start.isoformat(),
                "ends": deadline.isoformat(),
                "worker_pid": worker.pid,
                "duration_seconds": duration,
            },
            indent=2,
        )
    )
    try:
        while now() < deadline + timedelta(seconds=3):
            if worker.poll() is not None:
                raise RuntimeError("Production worker exited; inspect worker.log")
            async with Session() as s:
                count = await s.scalar(select(func.count(Result.id)))
                size = await s.scalar(text("SELECT pg_database_size(current_database())"))
                connections = await s.scalar(
                    text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()")
                )
                backlog = await s.scalar(select(func.count(Outbox.id)).where(Outbox.delivered_at.is_(None)))
            sample = {
                "elapsed": round(time.monotonic() - started, 2),
                "results": count,
                "rss_mib": process.memory_info().rss / 2**20,
                "cpu_percent": process.cpu_percent(),
                "connections": connections,
                "database_bytes": size,
                "outbox_backlog": backlog,
            }
            samples.append(sample)
            with (OUT / "samples.jsonl").open("a") as f:
                f.write(json.dumps(sample) + "\n")
            print(json.dumps(sample), flush=True)
            await asyncio.sleep(5)
        worker.terminate()
        await asyncio.to_thread(worker.wait, 30)
        async with Session() as s:
            rows = (
                await s.execute(select(Result, Monitor.name).join(Monitor).where(Result.scheduled_for < deadline))
            ).all()
            dupes = await s.scalar(
                text(
                    "SELECT count(*) FROM (SELECT monitor_id, scheduled_for FROM check_results GROUP BY 1,2 HAVING count(*)>1) x"
                )
            )
        lags = sorted(r.lag_ms for r, _ in rows)
        expected = sum(max(0, int((duration - i * 0.3 - 0.00001) // 30) + 1) for i in range(100))
        unexpected = [str(r.id) for r, name in rows if name.startswith(("fast", "slow")) and not r.success]
        mix = {
            kind: {
                "completed": sum(name.startswith(kind) for _, name in rows),
                "failed": sum(name.startswith(kind) and not r.success for r, name in rows),
            }
            for kind in set(kinds)
        }
        report = {
            "status": "complete",
            "machine": platform.platform(),
            "cpu_count": psutil.cpu_count(),
            "host_memory_gib": psutil.virtual_memory().total / 2**30,
            "duration_seconds": duration,
            "started": start.isoformat(),
            "ended": now().isoformat(),
            "expected": expected,
            "actual": len(rows),
            "missing": expected - len(rows),
            "duplicate_schedules": dupes,
            "unexplained_healthy_failures": unexpected,
            "p95_scheduler_lag_ms": lags[int(0.95 * (len(lags) - 1))] if lags else None,
            "max_scheduler_lag_ms": max(lags, default=None),
            "peak_rss_mib": max(x["rss_mib"] for x in samples),
            "mean_cpu_percent": statistics.mean(x["cpu_percent"] for x in samples),
            "max_connections": max(x["connections"] for x in samples),
            "database_growth_bytes": samples[-1]["database_bytes"] - samples[0]["database_bytes"],
            "max_outbox_backlog": max(x["outbox_backlog"] for x in samples),
            "mix": mix,
            "scope": "Local HTTP; 70 fast / 20 250ms / 5 HTTP503 / 5 2s timeout. Distinct ports. Real production worker; private allowlist is fixture-only. Internet/TLS/staging not inferred.",
        }
        report["passed"] = (
            duration >= 3600
            and len(rows) == expected
            and not dupes
            and not unexpected
            and report["p95_scheduler_lag_ms"] < 2000
        )
        (OUT / "report.json").write_text(json.dumps(report, indent=2))
        (OUT / "status.json").write_text(json.dumps({"status": "complete", "passed": report["passed"]}))
        print(json.dumps(report, indent=2), flush=True)
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()
        log.close()
        for server in servers:
            server.close()
            await server.wait_closed()
        await engine.dispose()


asyncio.run(run())
