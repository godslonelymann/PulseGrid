import asyncio
import uuid

from conftest import seed
from pulsegrid.auth.security import encrypt
from pulsegrid.storage.db import Session
from pulsegrid.storage.models import Channel, Monitor, MonitorVersion, Outbox
from sqlalchemy import func, select
from test_monitoring import sample

URL = "http://127.0.0.1:18080/health"


async def test_url_only_setup_and_edit_preserve_defaults(owner, fixture_server):
    response = await owner.post("/api/v1/monitors", json={"url": URL})
    assert response.status_code == 201, response.text
    service = response.json()
    assert service["name"] == "127.0.0.1"
    assert (service["method"], service["interval"], service["timeout"], service["statuses"]) == ("GET", 30, 10, [200])
    assert "bucket_id" not in service
    edited = await owner.put(f"/api/v1/monitors/{service['id']}", json={"url": URL, "name": "Updated API"})
    assert edited.status_code == 200 and edited.json()["name"] == "Updated API"
    async with Session() as session:
        monitor = await session.get(Monitor, uuid.UUID(service["id"]))
        account = (await owner.get("/api/v1/me")).json()
        assert str(monitor.owner_id) == account["id"]
        assert await session.scalar(select(func.count()).select_from(MonitorVersion)) == 2


async def test_simultaneous_setup_creates_directly_owned_monitors(owner, fixture_server):
    responses = await asyncio.gather(*[owner.post("/api/v1/monitors", json={"url": URL}) for _ in range(3)])
    assert all(r.status_code == 201 for r in responses)
    async with Session() as session:
        monitors = (await session.scalars(select(Monitor))).all()
        assert len(monitors) == 3 and len({m.owner_id for m in monitors}) == 1
        assert await session.scalar(select(func.count()).select_from(MonitorVersion)) == 3


async def test_concurrent_edits_preserve_both_configuration_versions(owner, fixture_server):
    service = (await owner.post("/api/v1/monitors", json={"url": URL})).json()
    responses = await asyncio.gather(
        *[
            owner.put(f"/api/v1/monitors/{service['id']}", json={"url": URL, "name": name})
            for name in ("First edit", "Second edit")
        ]
    )
    assert all(response.status_code == 200 for response in responses)
    async with Session() as session:
        monitor = await session.get(Monitor, uuid.UUID(service["id"]))
        versions = (await session.scalars(select(MonitorVersion).order_by(MonitorVersion.starts_at))).all()
        assert monitor.version == 3 and len(versions) == 3
        assert {v.name for v in versions} == {"127.0.0.1", "First edit", "Second edit"}
        assert sum(v.ends_at is None for v in versions) == 1
        assert all(a.ends_at == b.starts_at for a, b in zip(versions, versions[1:]))


async def test_invalid_service_leaves_no_records(owner):
    response = await owner.post("/api/v1/monitors", json={"url": "http://169.254.169.254/latest/meta-data"})
    assert response.status_code == 422
    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(Monitor)) == 0
        assert await session.scalar(select(func.count()).select_from(MonitorVersion)) == 0


async def test_removed_group_fields_are_rejected(owner, fixture_server):
    assert (await owner.post("/api/v1/monitors", json={"url": URL, "bucket_id": str(uuid.uuid4())})).status_code == 422
    assert (
        await owner.post(
            "/api/v1/notification-channels", json={"name": "Old scope", "kind": "email", "all_groups": False}
        )
    ).status_code == 422


async def test_pausing_one_service_does_not_pause_others(owner, fixture_server):
    first = (await owner.post("/api/v1/monitors", json={"url": URL})).json()
    second = (await owner.post("/api/v1/monitors", json={"url": URL})).json()
    await owner.post(f"/api/v1/monitors/{first['id']}/pause")
    assert (await owner.get(f"/api/v1/monitors/{first['id']}")).json()["state"] == "PAUSED"
    assert (await owner.get(f"/api/v1/monitors/{second['id']}")).json()["state"] == "UNKNOWN"


async def test_new_service_does_not_resume_paused_services(owner, fixture_server):
    first = (await owner.post("/api/v1/monitors", json={"url": URL})).json()
    await owner.post(f"/api/v1/monitors/{first['id']}/pause")
    second = (await owner.post("/api/v1/monitors", json={"url": URL})).json()
    assert second["state"] == "UNKNOWN"
    assert (await owner.get(f"/api/v1/monitors/{first['id']}")).json()["paused"]


async def test_alerts_cover_future_services_once_and_preserve_ownership(owner, fixture_server):
    channel = (await owner.post("/api/v1/notification-channels", json={"name": "Email alerts", "kind": "email"})).json()
    assert channel["email"] == "owner@example.com"
    other_id = await seed("other@example.com")
    async with Session.begin() as session:
        session.add(Channel(owner_id=other_id, name="Other owner", kind="email", config=encrypt("other@example.com")))
    service = (await owner.post("/api/v1/monitors", json={"url": URL})).json()
    await sample(uuid.UUID(service["id"]), False)
    await sample(uuid.UUID(service["id"]), False)
    async with Session() as session:
        rows = (await session.scalars(select(Outbox))).all()
        assert len(rows) == 1 and str(rows[0].channel_id) == channel["id"] and rows[0].event_kind == "outage"
    await owner.put(
        f"/api/v1/notification-channels/{channel['id']}",
        json={"name": "Email alerts", "kind": "email", "enabled": False},
    )
    await sample(uuid.UUID(service["id"]), True)
    await sample(uuid.UUID(service["id"]), True)
    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(Outbox)) == 1
