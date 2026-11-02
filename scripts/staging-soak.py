"""Local native staging observation; never claims remote deployment capacity."""

import asyncio
import json
import time
from pathlib import Path

import httpx


async def main():
    started = time.monotonic()
    samples = []
    async with httpx.AsyncClient(verify=False, timeout=5, trust_env=False) as client:
        while True:
            start = time.monotonic()
            try:
                r = await client.get("https://localhost:8443/api/v1/health/ready")
                sample = {
                    "elapsed": time.monotonic() - started,
                    "status": r.status_code,
                    "latency_ms": (time.monotonic() - start) * 1000,
                }
            except httpx.HTTPError as exc:
                sample = {"elapsed": time.monotonic() - started, "status": "error", "error_type": type(exc).__name__}
            samples.append(sample)
            with Path("verification/staging-soak.jsonl").open("a") as f:
                f.write(json.dumps(sample) + "\n")
            print(json.dumps(sample), flush=True)
            if Path("verification/capacity/report.json").exists():
                break
            await asyncio.sleep(10)
    Path("verification/staging-soak-report.json").write_text(
        json.dumps(
            {
                "scope": "Local production packaging with local-CA HTTPS; overlaps capacity run, no remote server/ACME claim",
                "duration_seconds": time.monotonic() - started,
                "samples": len(samples),
                "failures": sum(x["status"] != 200 for x in samples),
                "p95_request_ms": sorted(x.get("latency_ms", 5000) for x in samples)[int(0.95 * (len(samples) - 1))],
            },
            indent=2,
        )
    )


asyncio.run(main())
