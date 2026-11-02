"""Controlled HTTP target and webhook receiver for browser verification only."""

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response

app = FastAPI()
state = {"status": 200, "events": []}


@app.get("/health")
async def health():
    return Response("fixture", status_code=state["status"])


@app.post("/hook")
async def hook(request: Request):
    data = await request.json()
    state["events"].append(data)
    Path("verification/browser-notifications.json").write_text(json.dumps(state["events"], indent=2))
    return {"ok": True}


@app.post("/control/{status}")
async def control(status: int):
    state["status"] = status
    return state


@app.get("/events")
async def events():
    return state["events"]
