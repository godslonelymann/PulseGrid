import json
import math
import secrets
import time
import uuid
from datetime import timedelta
from typing import Literal

import pyotp
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from pulsegrid.api.google import router as google_router
from pulsegrid.api.schemas import (
    ActionOut,
    ChannelInput,
    ChannelOut,
    CheckOut,
    CodesOut,
    ConfirmMFA,
    Credentials,
    DashboardOut,
    DeliveryOut,
    EnrollmentOut,
    ErrorOut,
    Factor,
    IncidentOut,
    LoginOut,
    MeOut,
    MetricsOut,
    MonitorInput,
    MonitorOut,
    MonitorVersionOut,
    PasswordChange,
    Reauth,
    SessionOut,
)
from pulsegrid.auth.security import (
    DUMMY_HASH,
    audit,
    cfg,
    challenge,
    csrf,
    decrypt,
    digest,
    encrypt,
    hasher,
    issue,
    password_ok,
    principal,
    rate,
    reauthenticate,
    recovery_codes,
    redis,
    require_csrf,
    verify_factor,
)
from pulsegrid.domain.health import display
from pulsegrid.storage.db import db, now
from pulsegrid.storage.models import (
    Channel,
    Heartbeat,
    Incident,
    LoginSession,
    Monitor,
    MonitorVersion,
    Outbox,
    RefreshToken,
    Runtime,
    User,
)
from pulsegrid.storage.models import Result as Check
from pulsegrid.worker.probe import UnsafeURL, resolve, validate_url

app = FastAPI(
    description="Monitoring API with Google sign-up and sign-in. Protected routes require HttpOnly session cookies. Mutations require an exact Origin and matching X-CSRF-Token/cookie. Unknown and foreign resources both return 404. History uses bounded limit/offset pagination; interval/timeout bounds are described in input schemas. Latency is time to headers; metric windows are clipped to retention.",
    responses={
        401: {"model": ErrorOut, "description": "Missing, expired, revoked, or invalid authentication/factor"},
        403: {"model": ErrorOut, "description": "Origin or CSRF rejected"},
        404: {"model": ErrorOut, "description": "Resource absent or not owned by caller"},
        409: {"model": ErrorOut, "description": "Conflicting state or explicit history deletion confirmation required"},
        429: {"model": ErrorOut, "description": "Rate limit exceeded"},
        503: {"model": ErrorOut, "description": "Storage or security dependency unavailable; retry later"},
    },
    title="PulseGrid",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
P = "/api/v1"
MONITOR_LIMIT = 20
app.include_router(google_router, prefix=P + "/auth/google", tags=["Google authentication"])


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Do not echo input values (especially passwords and one-time codes).
    return JSONResponse(
        {"detail": [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]}, status_code=422
    )


@app.middleware("http")
async def protection(request: Request, call_next):
    request_id = str(uuid.uuid4())
    started = time.monotonic()
    try:
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            require_csrf(request)
            if int(request.headers.get("content-length", "0")) > 16384:
                raise HTTPException(413, "Request too large")
        response = await call_next(request)
    except HTTPException as exc:
        response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    except (RedisError, SQLAlchemyError):
        response = JSONResponse(
            {"detail": "Security or storage dependency temporarily unavailable", "request_id": request_id},
            status_code=503,
        )
    response.headers.update(
        {
            "X-Request-ID": request_id,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        }
    )
    print(
        json.dumps(
            {
                "request_id": request_id,
                "method": request.method,
                "status": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            }
        ),
        flush=True,
    )
    return response


@app.get(P + "/health/live")
async def live():
    return {"status": "live"}


@app.get(P + "/health/ready")
async def ready(session: AsyncSession = Depends(db)):
    await session.execute(text("SELECT 1"))
    await redis.ping()
    return {"status": "ready"}


@app.get(P + "/auth/csrf")
async def get_csrf(response: Response):
    return {"csrf": csrf(response)}


@app.post(P + "/auth/login", response_model=LoginOut, response_model_exclude_none=True)
async def login(data: Credentials, request: Request, response: Response, session: AsyncSession = Depends(db)):
    if not cfg.enable_legacy_password_auth:
        raise HTTPException(403, "Password sign-in is disabled; continue with Google")
    await rate(f"ip:{request.client.host}", 30)
    await rate(f"login:{digest(data.email.lower())}", 10)
    user = await session.scalar(select(User).where(User.email == data.email.lower()))
    if not await password_ok(data.password, user.password_hash if user else DUMMY_HASH) or not user:
        raise HTTPException(401, "Invalid email or password")
    verified_hash = user.password_hash
    await session.refresh(user, with_for_update=True)
    if user.password_hash != verified_hash:
        raise HTTPException(401, "Credentials changed; sign in again")
    if user.totp_secret:
        return {"mfa_required": True, "challenge": await challenge(user)}
    await issue(session, response, user, label=request.headers.get("user-agent", "Browser"))
    audit(session, user, "login")
    await session.commit()
    return {"mfa_required": False}


@app.post(P + "/auth/verify", response_model=ActionOut, response_model_exclude_none=True)
async def mfa_login(data: Factor, request: Request, response: Response, session: AsyncSession = Depends(db)):
    await rate(f"challenge-attempt:{digest(data.challenge)}", 5)
    key = f"challenge:{data.challenge}"
    value = await redis.get(key)
    if not value:
        raise HTTPException(401, "Challenge expired or consumed")
    user = await session.scalar(
        select(User).where(User.id == uuid.UUID(json.loads(value)["user_id"])).with_for_update()
    )
    if (
        not user
        or not user.totp_secret
        or json.loads(value).get("credential_version") != digest((user.password_hash or "") + user.totp_secret)
        or not await verify_factor(session, user, data.code)
    ):
        raise HTTPException(401, "Invalid or already used factor")
    # Both recovery consumption and session creation roll back if another request wins.
    if not await redis.getdel(key):
        raise HTTPException(401, "Challenge already consumed")
    await issue(session, response, user, label=request.headers.get("user-agent", "Browser"))
    audit(session, user, "mfa_login")
    await session.commit()
    return {"ok": True}


@app.post(P + "/auth/refresh", response_model=ActionOut, response_model_exclude_none=True)
async def refresh(request: Request, response: Response, session: AsyncSession = Depends(db)):
    await redis.ping()
    token = await session.scalar(
        select(RefreshToken).where(RefreshToken.digest == digest(request.cookies.get("pg_refresh", "")))
    )
    if not token:
        raise HTTPException(401, "Refresh token invalid")
    family = await session.scalar(select(LoginSession).where(LoginSession.id == token.session_id).with_for_update())
    if family is None:
        raise HTTPException(401, "Session expired or revoked")
    await session.refresh(token)
    if token.consumed_at or token.expires_at <= now() or family.revoked_at or family.expires_at <= now():
        family.revoked_at = now()
        await session.commit()
        raise HTTPException(401, "Refresh reused or expired; sign in again")
    token.consumed_at = now()
    user = await session.get(User, family.user_id)
    replacement = await issue(session, response, user, family)
    token.replacement_id = replacement.id
    await session.commit()
    return {"ok": True}


@app.get(P + "/me", response_model=MeOut)
async def me(auth=Depends(principal)):
    user, family = auth
    return {
        "id": user.id,
        "email": user.email,
        "mfa_enabled": bool(user.totp_secret),
        "password_enabled": cfg.enable_legacy_password_auth and bool(user.password_hash),
        "google_connected": bool(user.google_sub),
        "session_id": family.id,
    }


@app.get(P + "/sessions", response_model=list[SessionOut])
async def sessions(auth=Depends(principal), session: AsyncSession = Depends(db)):
    rows = (
        await session.scalars(
            select(LoginSession)
            .where(
                LoginSession.user_id == auth[0].id, LoginSession.revoked_at.is_(None), LoginSession.expires_at > now()
            )
            .order_by(LoginSession.created_at.desc())
        )
    ).all()
    return [
        {
            "id": f.id,
            "label": f.label,
            "created_at": f.created_at,
            "last_activity": f.last_activity,
            "current": f.id == auth[1].id,
        }
        for f in rows
    ]


@app.delete(P + "/sessions/{session_id}")
async def revoke(session_id: uuid.UUID, auth=Depends(principal), session: AsyncSession = Depends(db)):
    await session.execute(
        update(LoginSession)
        .where(LoginSession.id == session_id, LoginSession.user_id == auth[0].id)
        .values(revoked_at=now())
    )
    audit(session, auth[0], "session_revoked")
    await session.commit()
    return {"ok": True}


@app.post(P + "/auth/logout", response_model=ActionOut, response_model_exclude_none=True)
@app.post(P + "/auth/logout-all", response_model=ActionOut, response_model_exclude_none=True)
async def logout(request: Request, response: Response, auth=Depends(principal), session: AsyncSession = Depends(db)):
    stmt = update(LoginSession).where(LoginSession.user_id == auth[0].id)
    if not request.url.path.endswith("logout-all"):
        stmt = stmt.where(LoginSession.id == auth[1].id)
    await session.execute(stmt.values(revoked_at=now()))
    audit(session, auth[0], "logout")
    await session.commit()
    for name in ("pg_access", "pg_refresh", "pg_csrf"):
        response.delete_cookie(name, path="/", secure=cfg.secure_cookies, httponly=name != "pg_csrf", samesite="strict")
    return {"ok": True}


@app.post(P + "/auth/password", response_model=ActionOut, response_model_exclude_none=True)
async def password_change(data: PasswordChange, auth=Depends(principal), session: AsyncSession = Depends(db)):
    user = await session.scalar(select(User).where(User.id == auth[0].id).with_for_update())
    await reauthenticate(session, user, data.password, data.code)
    import asyncio

    user.password_hash = await asyncio.to_thread(hasher.hash, data.new_password)
    await session.execute(update(LoginSession).where(LoginSession.user_id == user.id).values(revoked_at=now()))
    audit(session, user, "password_changed")
    await session.commit()
    return {"ok": True, "detail": "All sessions revoked; sign in again"}


@app.post(P + "/auth/mfa/enroll", response_model=EnrollmentOut)
async def enroll(data: Reauth, auth=Depends(principal), session: AsyncSession = Depends(db)):
    user = auth[0]
    if user.totp_secret:
        raise HTTPException(409, "MFA already enabled")
    await reauthenticate(session, user, data.password, data.code)
    secret = pyotp.random_base32()
    await redis.set(f"enroll:{auth[1].id}", encrypt(secret), ex=300)
    return {"secret": secret, "uri": pyotp.TOTP(secret).provisioning_uri(user.email, issuer_name="PulseGrid")}


@app.post(P + "/auth/mfa/confirm", response_model=CodesOut)
async def confirm(data: ConfirmMFA, auth=Depends(principal), session: AsyncSession = Depends(db)):
    if not cfg.enable_legacy_password_auth:
        raise HTTPException(403, "Manage account security through Google")
    await rate(f"enroll-attempt:{auth[1].id}", 5)
    secret = await redis.get(f"enroll:{auth[1].id}")
    if not secret or not pyotp.TOTP(decrypt(secret)).verify(data.code, valid_window=0):
        raise HTTPException(401, "Invalid or expired enrollment")
    user = await session.scalar(select(User).where(User.id == auth[0].id).with_for_update())
    if user.totp_secret or not await redis.getdel(f"enroll:{auth[1].id}"):
        raise HTTPException(409, "Enrollment already used")
    user.totp_secret = secret
    user.last_totp_step = int(now().timestamp()) // 30
    await redis.set(f"totp:{user.id}:{int(now().timestamp()) // 30}", "used", ex=120)
    codes = await recovery_codes(session, user)
    # Enabling MFA revokes other sessions that were authenticated with only a password.
    await session.execute(
        update(LoginSession)
        .where(LoginSession.user_id == user.id, LoginSession.id != auth[1].id)
        .values(revoked_at=now())
    )
    audit(session, user, "mfa_enabled")
    await session.commit()
    return {"recovery_codes": codes}


@app.post(P + "/auth/mfa/disable", response_model=ActionOut | CodesOut, response_model_exclude_none=True)
@app.post(P + "/auth/mfa/recovery-codes", response_model=ActionOut | CodesOut, response_model_exclude_none=True)
async def manage_mfa(data: Reauth, request: Request, auth=Depends(principal), session: AsyncSession = Depends(db)):
    user = await session.scalar(select(User).where(User.id == auth[0].id).with_for_update())
    if not user.totp_secret:
        raise HTTPException(409, "MFA is not enabled")
    await reauthenticate(session, user, data.password, data.code)
    if request.url.path.endswith("disable"):
        user.totp_secret = None
        user.last_totp_step = None
        from pulsegrid.storage.models import RecoveryCode

        await session.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
        result = {"ok": True}
        audit(session, user, "mfa_disabled")
    else:
        result = {"recovery_codes": await recovery_codes(session, user)}
        audit(session, user, "recovery_codes_replaced")
    await session.execute(
        update(LoginSession)
        .where(LoginSession.user_id == user.id, LoginSession.id != auth[1].id)
        .values(revoked_at=now())
    )
    await session.commit()
    return result


async def owned_monitor(session, owner, monitor_id, lock=False, include_archived=False):
    stmt = select(Monitor).where(Monitor.id == monitor_id, Monitor.owner_id == owner)
    if not include_archived:
        stmt = stmt.where(Monitor.archived_at.is_(None))
    row = await session.scalar(stmt.with_for_update() if lock else stmt)
    if row is None:
        raise HTTPException(404, "Monitor not found")
    return row


async def owned_channel(session, owner, channel_id):
    row = await session.scalar(select(Channel).where(Channel.id == channel_id, Channel.owner_id == owner))
    if row is None:
        raise HTTPException(404, "Channel not found")
    return row


def snapshot(monitor, at):
    return MonitorVersion(
        monitor_id=monitor.id,
        starts_at=at,
        interval=monitor.interval,
        active=not monitor.paused and monitor.archived_at is None,
        settings_known=True,
        name=monitor.name,
        url=monitor.url,
        method=monitor.method,
        statuses=list(monitor.statuses),
        timeout=monitor.timeout,
    )


async def configuration_changed(session, monitor):
    # The caller holds the monitor lock; worker commits use monitor -> runtime too.
    at = now()
    await session.execute(
        update(MonitorVersion)
        .where(MonitorVersion.monitor_id == monitor.id, MonitorVersion.ends_at.is_(None))
        .values(ends_at=at)
    )
    session.add(snapshot(monitor, at))
    runtime = await session.get(Runtime, monitor.id, with_for_update=True)
    runtime.lease_id = runtime.lease_until = None
    runtime.failures = runtime.successes = 0
    runtime.last_observed = None
    runtime.due_at = at + timedelta(seconds=secrets.randbelow(min(5, monitor.interval)))
    monitor.version += 1


def monitor_json(m, r, last_seen=None):
    return {
        "id": m.id,
        "name": m.name,
        "url": m.url,
        "method": m.method,
        "statuses": m.statuses,
        "interval": m.interval,
        "timeout": m.timeout,
        "paused": m.paused,
        "archived_at": m.archived_at,
        "state": display(r, m, now()),
        "last_observed": r.last_observed or last_seen,
        "observed_state": r.state,
    }


async def safe_destination(url):
    p = validate_url(url)
    try:
        await resolve(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except (UnsafeURL, OSError, TimeoutError):
        raise HTTPException(422, "Destination is prohibited or could not be resolved") from None


@app.post(P + "/monitors", status_code=201, response_model=MonitorOut)
async def create_monitor(data: MonitorInput, auth=Depends(principal), session: AsyncSession = Depends(db)):
    await safe_destination(data.url)
    owner_id = auth[0].id
    # Serialize concurrent creates for this account before counting its monitors.
    await session.execute(select(User.id).where(User.id == owner_id).with_for_update())
    total = await session.scalar(select(func.count()).select_from(Monitor).where(Monitor.owner_id == owner_id))
    if total >= MONITOR_LIMIT:
        raise HTTPException(409, f"You’ve reached the {MONITOR_LIMIT}-service limit. Delete a service to add another.")
    m = Monitor(owner_id=owner_id, **data.model_dump())
    session.add(m)
    await session.flush()
    r = Runtime(id=m.id, due_at=now() + timedelta(seconds=secrets.randbelow(5)))
    session.add(r)
    session.add(snapshot(m, now()))
    audit(session, auth[0], "monitor_created", "monitor", m.id)
    await session.commit()
    return monitor_json(m, r)


@app.put(P + "/monitors/{monitor_id}", response_model=MonitorOut)
async def edit_monitor(
    monitor_id: uuid.UUID, data: MonitorInput, auth=Depends(principal), session: AsyncSession = Depends(db)
):
    await safe_destination(data.url)
    m = await owned_monitor(session, auth[0].id, monitor_id, lock=True)
    for key, value in data.model_dump().items():
        setattr(m, key, value)
    await configuration_changed(session, m)
    audit(session, auth[0], "monitor_updated", "monitor", m.id)
    await session.commit()
    return monitor_json(m, await session.get(Runtime, m.id))


@app.delete(P + "/monitors/{monitor_id}")
async def delete_monitor(
    monitor_id: uuid.UUID, confirm_history: bool = False, auth=Depends(principal), session: AsyncSession = Depends(db)
):
    m = await owned_monitor(session, auth[0].id, monitor_id, lock=True, include_archived=True)
    if not confirm_history:
        raise HTTPException(
            409, "Set confirm_history=true to permanently delete check/incident history; archive to retain it"
        )
    await session.delete(m)
    audit(session, auth[0], "monitor_deleted", "monitor", m.id)
    await session.commit()
    return {"ok": True}


@app.post(P + "/monitors/{monitor_id}/{action}")
async def monitor_action(
    monitor_id: uuid.UUID, action: str, auth=Depends(principal), session: AsyncSession = Depends(db)
):
    if action not in ("pause", "resume", "archive", "restore"):
        raise HTTPException(404)
    m = await owned_monitor(session, auth[0].id, monitor_id, lock=True, include_archived=True)
    if action in ("pause", "resume"):
        if m.archived_at is not None:
            raise HTTPException(409, "Restore the archived monitor first")
        m.paused = action == "pause"
    elif action == "archive":
        if m.archived_at is not None:
            return {"ok": True}
        m.archived_at = now()
    else:
        if m.archived_at is None:
            return {"ok": True}
        m.archived_at = None
    await configuration_changed(session, m)
    audit(session, auth[0], f"monitor_{action}", "monitor", m.id)
    await session.commit()
    return {"ok": True}


@app.get(P + "/monitors", response_model=DashboardOut)
@app.get(P + "/dashboard", response_model=DashboardOut)
async def dashboard(archived: bool = False, auth=Depends(principal), session: AsyncSession = Depends(db)):
    rows = (
        await session.execute(
            select(
                Monitor,
                Runtime,
                select(Check.observed_at)
                .where(Check.monitor_id == Monitor.id)
                .order_by(Check.observed_at.desc())
                .limit(1)
                .correlate(Monitor)
                .scalar_subquery()
                .label("last_seen"),
            )
            .join(Runtime, Runtime.id == Monitor.id)
            .where(
                Monitor.owner_id == auth[0].id,
                Monitor.archived_at.is_not(None) if archived else Monitor.archived_at.is_(None),
            )
            .order_by(Monitor.name)
            .limit(1000)
        )
    ).all()
    ms = [monitor_json(m, r, last_seen) for m, r, last_seen in rows]
    return {
        "monitors": ms,
        "counts": {
            state: sum(m["state"] == state for m in ms) for state in ("UP", "SUSPECT", "DOWN", "UNKNOWN", "PAUSED")
        },
        "limit": MONITOR_LIMIT,
        "monitor_total": await session.scalar(
            select(func.count()).select_from(Monitor).where(Monitor.owner_id == auth[0].id)
        ),
    }


@app.get(P + "/monitors/{monitor_id}", response_model=MonitorOut)
async def monitor_detail(monitor_id: uuid.UUID, auth=Depends(principal), session: AsyncSession = Depends(db)):
    m = await owned_monitor(session, auth[0].id, monitor_id, include_archived=True)
    last_seen = await session.scalar(
        select(Check.observed_at).where(Check.monitor_id == m.id).order_by(Check.observed_at.desc()).limit(1)
    )
    return monitor_json(m, await session.get(Runtime, m.id), last_seen)


@app.get(P + "/monitors/{monitor_id}/results", response_model=list[CheckOut])
async def results(
    monitor_id: uuid.UUID,
    hours: int = Query(24, ge=1, le=720),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0, le=100000),
    auth=Depends(principal),
    session: AsyncSession = Depends(db),
):
    await owned_monitor(session, auth[0].id, monitor_id, include_archived=True)
    rows = await session.scalars(
        select(Check)
        .where(
            Check.monitor_id == monitor_id,
            Check.observed_at >= now() - timedelta(hours=min(hours, cfg.raw_retention_days * 24)),
        )
        .order_by(Check.observed_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [
        {
            "id": r.id,
            "monitor_version_id": r.monitor_version_id,
            "scheduled_for": r.scheduled_for,
            "observed_at": r.observed_at,
            "success": r.success,
            "status_code": r.status_code,
            "latency_ms": r.latency_ms,
            "error": r.error,
            "lag_ms": r.lag_ms,
        }
        for r in rows
    ]


@app.get(P + "/monitors/{monitor_id}/metrics", response_model=MetricsOut)
async def metrics(
    monitor_id: uuid.UUID,
    hours: int = Query(24, ge=1, le=720),
    auth=Depends(principal),
    session: AsyncSession = Depends(db),
):
    await owned_monitor(session, auth[0].id, monitor_id, include_archived=True)
    end = now()
    requested_start = end - timedelta(hours=hours)
    start = max(requested_start, end - timedelta(days=cfg.raw_retention_days))
    # At most 168 points, including empty buckets so gaps stay visible.
    bucket_seconds = next(size for size in (60, 600, 3600, 21600) if hours * 3600 <= size * 168)
    step = timedelta(seconds=bucket_seconds)
    bucket = func.date_bin(step, Check.observed_at, start)
    latencies = dict(
        (
            await session.execute(
                select(bucket, func.percentile_cont(0.5).within_group(Check.latency_ms))
                .where(
                    Check.monitor_id == monitor_id,
                    Check.observed_at >= start,
                    Check.observed_at < end,
                    Check.success,
                    Check.latency_ms.is_not(None),
                )
                .group_by(bucket)
            )
        ).all()
    )
    bucket_starts = (start + step * i for i in range(math.ceil((end - start) / step)))
    count, success, median, p95, p99 = (
        await session.execute(
            select(
                func.count(Check.id),
                func.count(Check.id).filter(Check.success),
                func.percentile_cont(0.5).within_group(Check.latency_ms).filter(Check.success),
                func.percentile_cont(0.95).within_group(Check.latency_ms).filter(Check.success),
                func.percentile_cont(0.99).within_group(Check.latency_ms).filter(Check.success),
            ).where(Check.monitor_id == monitor_id, Check.observed_at >= start, Check.observed_at <= end)
        )
    ).one()
    expected = 0
    for a in await session.scalars(
        select(MonitorVersion).where(
            MonitorVersion.monitor_id == monitor_id,
            MonitorVersion.active,
            MonitorVersion.starts_at <= end,
            (MonitorVersion.ends_at.is_(None) | (MonitorVersion.ends_at > start)),
        )
    ):
        expected += max(0.0, (min(a.ends_at or end, end) - max(a.starts_at, start)).total_seconds()) / a.interval
    # Fractional scheduled opportunities at window boundaries, rounded down only once.
    expected = math.floor(expected)
    histogram = []
    for lower, upper in ((0, 100), (100, 250), (250, 500), (500, 1000), (1000, 5000), (5000, 100000)):
        n = await session.scalar(
            select(func.count(Check.id)).where(
                Check.monitor_id == monitor_id,
                Check.observed_at >= start,
                Check.success,
                Check.latency_ms >= lower,
                Check.latency_ms < upper,
            )
        )
        histogram.append({"range": f"{lower}–{upper} ms", "count": n})
    return {
        "window_start": start,
        "window_end": end,
        "retention_clipped": start > requested_start,
        "completed": count,
        "successful": success,
        "failed": count - success,
        "expected": expected,
        "missing": max(0, expected - count),
        "uptime": success / count if count else None,
        "coverage": min(1, count / expected) if expected else None,
        "median_ms": median,
        "p95_ms": p95,
        "p99_ms": p99,
        "histogram": histogram,
        "latency_bucket_seconds": bucket_seconds,
        "latency_trend": [{"starts_at": at, "median_ms": latencies.get(at)} for at in bucket_starts],
        "latency_definition": "Time to response headers, successful checks only",
        "coverage_definition": "Completed / floor(sum(active seconds / configured interval)); capped at 100% for boundary jitter",
    }


@app.get(P + "/monitors/{monitor_id}/versions", response_model=list[MonitorVersionOut])
async def versions(
    monitor_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    auth=Depends(principal),
    session: AsyncSession = Depends(db),
):
    await owned_monitor(session, auth[0].id, monitor_id, include_archived=True)
    rows = await session.scalars(
        select(MonitorVersion)
        .where(MonitorVersion.monitor_id == monitor_id)
        .order_by(MonitorVersion.starts_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [{key: getattr(row, key) for key in MonitorVersionOut.model_fields} for row in rows]


@app.get(P + "/monitors/{monitor_id}/incidents", response_model=list[IncidentOut])
async def incidents(
    monitor_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    auth=Depends(principal),
    session: AsyncSession = Depends(db),
):
    await owned_monitor(session, auth[0].id, monitor_id, include_archived=True)
    rows = await session.scalars(
        select(Incident)
        .where(Incident.monitor_id == monitor_id)
        .order_by(Incident.opened_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return [{"id": r.id, "opened_at": r.opened_at, "closed_at": r.closed_at} for r in rows]


def channel_json(c, account_email):
    return {
        "id": c.id,
        "name": c.name,
        "kind": c.kind,
        "enabled": c.enabled,
        "email": account_email if c.kind == "email" else None,
    }


@app.get(P + "/notification-settings")
async def notification_settings(auth=Depends(principal)):
    return {"email_available": bool(cfg.smtp_host and cfg.smtp_from)}


@app.get(P + "/notification-channels", response_model=list[ChannelOut])
async def channels(auth=Depends(principal), session: AsyncSession = Depends(db)):
    return [
        channel_json(c, auth[0].email)
        for c in await session.scalars(select(Channel).where(Channel.owner_id == auth[0].id))
    ]


@app.post(P + "/notification-channels", status_code=201, response_model=ChannelOut)
async def channel_create(data: ChannelInput, auth=Depends(principal), session: AsyncSession = Depends(db)):
    if data.kind == "webhook":
        await safe_destination(data.destination)
    c = Channel(
        owner_id=auth[0].id,
        name=data.name,
        kind=data.kind,
        enabled=data.enabled,
        config=encrypt(auth[0].email if data.kind == "email" else data.destination),
    )
    session.add(c)
    audit(session, auth[0], "channel_created")
    await session.commit()
    return channel_json(c, auth[0].email)


@app.put(P + "/notification-channels/{channel_id}")
async def channel_edit(
    channel_id: uuid.UUID, data: ChannelInput, auth=Depends(principal), session: AsyncSession = Depends(db)
):
    c = await owned_channel(session, auth[0].id, channel_id)
    if data.kind == "webhook":
        await safe_destination(data.destination)
    destination = auth[0].email if data.kind == "email" else data.destination
    c.name, c.kind, c.enabled, c.config = data.name, data.kind, data.enabled, encrypt(destination)
    audit(session, auth[0], "channel_updated")
    await session.commit()
    return channel_json(c, auth[0].email)


@app.delete(P + "/notification-channels/{channel_id}")
async def channel_delete(channel_id: uuid.UUID, auth=Depends(principal), session: AsyncSession = Depends(db)):
    c = await owned_channel(session, auth[0].id, channel_id)
    await session.delete(c)
    audit(session, auth[0], "channel_deleted")
    await session.commit()
    return {"ok": True}


@app.post(P + "/notification-channels/{channel_id}/test")
async def channel_test(channel_id: uuid.UUID, auth=Depends(principal), session: AsyncSession = Depends(db)):
    channel = await owned_channel(session, auth[0].id, channel_id)
    if not channel.enabled:
        raise HTTPException(409, "Enable the alert channel before sending a test")
    await rate(f"channel-test:{channel_id}", 5, 60)
    item = Outbox(
        channel_id=channel_id,
        event_kind="test",
        payload={"message": "PulseGrid test notification", "time": now().isoformat()},
    )
    session.add(item)
    await session.commit()
    return {"id": item.id, "queued": True}


@app.get(P + "/deliveries", response_model=list[DeliveryOut])
async def deliveries(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    event_kind: Literal["outage", "recovery", "test"] | None = None,
    failed: bool = False,
    auth=Depends(principal),
    session: AsyncSession = Depends(db),
):
    query = select(Outbox).join(Channel).where(Channel.owner_id == auth[0].id)
    if event_kind is not None:
        query = query.where(Outbox.event_kind == event_kind)
    if failed:
        query = query.where(Outbox.delivered_at.is_(None), Outbox.last_error.is_not(None))
    # A retry changes next_attempt. Use the original queue time for latest-test lookup.
    order = (
        Outbox.payload["time"].as_string().desc().nulls_last() if event_kind == "test" else Outbox.next_attempt.desc()
    )
    rows = await session.scalars(query.order_by(order, Outbox.id.desc()).limit(limit).offset(offset))
    return [
        {
            "id": r.id,
            "channel_id": r.channel_id,
            "event_kind": r.event_kind,
            "attempts": r.attempts,
            "delivered_at": r.delivered_at,
            "exhausted": r.exhausted,
            "last_error": r.last_error,
            "next_attempt": r.next_attempt,
            "test_queued_at": r.payload.get("time") if r.event_kind == "test" else None,
        }
        for r in rows
    ]


@app.post(P + "/deliveries/{delivery_id}/retry")
async def delivery_retry(delivery_id: uuid.UUID, auth=Depends(principal), session: AsyncSession = Depends(db)):
    item = await session.scalar(
        select(Outbox)
        .join(Channel)
        .where(Outbox.id == delivery_id, Channel.owner_id == auth[0].id)
        .with_for_update(of=Outbox)
    )
    if not item:
        raise HTTPException(404, "Delivery not found")
    if item.delivered_at:
        raise HTTPException(409, "Already delivered")
    if item.lease_until and item.lease_until > now():
        raise HTTPException(409, "Delivery in progress")
    item.attempts, item.exhausted, item.next_attempt = 0, False, now()
    item.lease_id = item.lease_until = None
    await session.commit()
    return {"ok": True}


@app.get(P + "/worker-status")
async def worker_status(auth=Depends(principal), session: AsyncSession = Depends(db)):
    heartbeats = await session.scalars(select(Heartbeat).where(Heartbeat.seen_at > now() - timedelta(minutes=5)))
    return {
        "workers": [
            {
                "id": r.id,
                "seen_at": r.seen_at,
                "inflight": r.inflight,
                "stale": r.seen_at < now() - timedelta(seconds=15),
            }
            for r in heartbeats
        ],
        "outbox_pending": await session.scalar(
            select(func.count(Outbox.id))
            .join(Channel)
            .where(Channel.owner_id == auth[0].id, Outbox.delivered_at.is_(None), ~Outbox.exhausted)
        ),
        "outbox_failed": await session.scalar(
            select(func.count(Outbox.id)).join(Channel).where(Channel.owner_id == auth[0].id, Outbox.exhausted)
        ),
    }
