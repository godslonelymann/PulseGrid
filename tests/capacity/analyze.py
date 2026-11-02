"""Supplement the completed one-hour run with query/storage/retention measurements."""

import asyncio
import json
import os
import statistics
import time
from pathlib import Path

from pulsegrid.storage.db import Session, engine
from pulsegrid.storage.models import Monitor
from sqlalchemy import select, text


async def main():
    report = json.loads(
        (Path(os.environ.get("CAPACITY_OUTPUT_DIR", "verification/capacity")) / "report.json").read_text()
    )
    if report["status"] != "complete":
        raise RuntimeError("Capacity run must finish first")
    samples = []
    async with Session() as s:
        ids = list(await s.scalars(select(Monitor.id)))
        for mid in ids:
            start = time.perf_counter()
            await s.execute(
                text(
                    "SELECT count(*), count(*) FILTER (WHERE success), percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE success) FROM check_results WHERE monitor_id=:id AND observed_at>now()-interval '24 hours'"
                ),
                {"id": mid},
            )
            samples.append((time.perf_counter() - start) * 1000)
        relations = (
            (
                await s.execute(
                    text(
                        "SELECT relname,pg_total_relation_size(relid) AS bytes FROM pg_catalog.pg_statio_user_tables ORDER BY bytes DESC"
                    )
                )
            )
            .mappings()
            .all()
        )
        rowcount = await s.scalar(text("SELECT count(*) FROM check_results"))
    async with engine.connect() as c:
        tx = await c.begin()
        plan = await c.scalar(
            text(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) DELETE FROM check_results WHERE id IN (SELECT id FROM check_results ORDER BY observed_at LIMIT 1000)"
            )
        )
        await tx.rollback()
    async with Session() as s:
        assert await s.scalar(text("SELECT count(*) FROM check_results")) == rowcount
    supplemental = {
        "metric_query_samples": len(samples),
        "metric_query_median_ms": statistics.median(samples),
        "metric_query_p95_ms": sorted(samples)[int(0.95 * (len(samples) - 1))],
        "relation_sizes": list(map(dict, relations)),
        "retention_1000_row_delete_rolled_back": plan,
        "retention_scope": "Batch cost measured on final capacity data with rollback; no real 30-day retention/rollup claim.",
    }
    (Path(os.environ.get("CAPACITY_OUTPUT_DIR", "verification/capacity")) / "storage-and-query.json").write_text(
        json.dumps(supplemental, indent=2, default=str)
    )
    print(
        json.dumps(
            {
                k: v
                for k, v in supplemental.items()
                if k not in ("retention_1000_row_delete_rolled_back", "relation_sizes")
            },
            indent=2,
        )
    )
    await engine.dispose()


asyncio.run(main())
