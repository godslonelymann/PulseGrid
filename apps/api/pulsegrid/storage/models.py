import uuid

from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from pulsegrid.storage.db import Base, now


def identity():
    return Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def fk(table, **kw):
    return Column(UUID(as_uuid=True), ForeignKey(f"{table}.id", ondelete="CASCADE"), nullable=False, **kw)


def timecol(**kw):
    return Column(DateTime(timezone=True), **kw)


def valid_statuses():
    return "cardinality(statuses) BETWEEN 1 AND 100 AND array_ndims(statuses) = 1 AND array_position(statuses, NULL) IS NULL AND 100 <= ALL(statuses) AND 599 >= ALL(statuses)"


class User(Base):
    __tablename__ = "users"
    id = identity()
    email = Column(String(254), unique=True, nullable=False)
    password_hash = Column(Text)
    google_sub = Column(String(255), unique=True)
    totp_secret = Column(Text)
    last_totp_step = Column(Integer)
    created_at = timecol(default=now, nullable=False)
    __table_args__ = (
        CheckConstraint("password_hash IS NOT NULL OR google_sub IS NOT NULL", name="user_login_identity"),
    )


class RecoveryCode(Base):
    __tablename__ = "recovery_codes"
    id = identity()
    user_id = fk("users", index=True)
    digest = Column(String(64), unique=True, nullable=False)
    consumed_at = timecol()


class LoginSession(Base):
    __tablename__ = "sessions"
    id = identity()
    user_id = fk("users", index=True)
    created_at = timecol(default=now, nullable=False)
    expires_at = timecol(nullable=False, index=True)
    revoked_at = timecol()
    last_activity = timecol(default=now, nullable=False)
    label = Column(String(200), nullable=False)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id = identity()
    session_id = fk("sessions", index=True)
    digest = Column(String(64), unique=True, nullable=False)
    consumed_at = timecol()
    expires_at = timecol(nullable=False)
    replacement_id = Column(UUID(as_uuid=True), ForeignKey("refresh_tokens.id", ondelete="SET NULL"))


class Monitor(Base):
    __tablename__ = "monitors"
    id = identity()
    owner_id = fk("users", index=True)
    name = Column(String(100), nullable=False)
    url = Column(Text, nullable=False)
    method = Column(String(4), nullable=False, default="GET")
    statuses = Column(ARRAY(SmallInteger), nullable=False, default=lambda: [200])
    interval = Column(Integer, nullable=False, default=30)
    timeout = Column(Integer, nullable=False, default=10)
    paused = Column(Boolean, nullable=False, default=False)
    version = Column(Integer, nullable=False, default=1)
    created_at = timecol(default=now, nullable=False)
    archived_at = timecol()
    __table_args__ = (
        CheckConstraint("interval BETWEEN 30 AND 86400", name="monitor_interval"),
        CheckConstraint("timeout BETWEEN 1 AND 20 AND timeout < interval", name="monitor_timeout"),
        CheckConstraint("method IN ('GET', 'HEAD')", name="monitor_method"),
        CheckConstraint(valid_statuses(), name="monitor_statuses"),
        CheckConstraint("version >= 1", name="monitor_version_positive"),
    )


class Runtime(Base):
    __tablename__ = "monitor_runtime"
    id = fk("monitors", primary_key=True)
    state = Column(String(10), nullable=False, default="UNKNOWN")
    failures = Column(Integer, nullable=False, default=0)
    successes = Column(Integer, nullable=False, default=0)
    last_observed = timecol()
    due_at = timecol(nullable=False, default=now, index=True)
    lease_id = Column(UUID(as_uuid=True))
    lease_until = timecol()
    lease_version = Column(Integer, nullable=False, default=0)
    __table_args__ = (
        CheckConstraint("state IN ('UNKNOWN', 'UP', 'SUSPECT', 'DOWN')", name="runtime_state"),
        CheckConstraint("failures >= 0 AND successes >= 0 AND lease_version >= 0", name="runtime_counters"),
        CheckConstraint("(lease_id IS NULL) = (lease_until IS NULL)", name="runtime_lease_pair"),
    )


class MonitorVersion(Base):
    __tablename__ = "monitor_versions"
    id = identity()
    monitor_id = fk("monitors")
    starts_at = timecol(nullable=False)
    ends_at = timecol()
    interval = Column(Integer, nullable=False)
    active = Column(Boolean, nullable=False)
    # Pre-migration settings were not recorded. NULL means unknown, never guessed.
    settings_known = Column(Boolean, nullable=False, default=True)
    name = Column(String(100))
    url = Column(Text)
    method = Column(String(4))
    statuses = Column(ARRAY(SmallInteger))
    timeout = Column(Integer)
    __table_args__ = (
        UniqueConstraint("monitor_id", "id", name="uq_monitor_versions_monitor_id"),
        Index("ix_monitor_versions_time", "monitor_id", "starts_at"),
        Index("one_current_monitor_version", "monitor_id", unique=True, postgresql_where=text("ends_at IS NULL")),
        CheckConstraint("ends_at IS NULL OR ends_at >= starts_at", name="version_time_order"),
        CheckConstraint("interval BETWEEN 30 AND 86400", name="version_interval"),
        CheckConstraint("timeout IS NULL OR (timeout BETWEEN 1 AND 20 AND timeout < interval)", name="version_timeout"),
        CheckConstraint("method IS NULL OR method IN ('GET', 'HEAD')", name="version_method"),
        CheckConstraint("statuses IS NULL OR (" + valid_statuses() + ")", name="version_statuses"),
        CheckConstraint(
            "NOT settings_known OR (name IS NOT NULL AND url IS NOT NULL AND method IS NOT NULL AND statuses IS NOT NULL AND timeout IS NOT NULL)",
            name="version_known_settings",
        ),
    )


class Result(Base):
    __tablename__ = "check_results"
    id = identity()
    monitor_id = fk("monitors")
    monitor_version_id = Column(UUID(as_uuid=True), nullable=False)
    scheduled_for = timecol(nullable=False)
    observed_at = timecol(nullable=False)
    success = Column(Boolean, nullable=False)
    status_code = Column(Integer)
    latency_ms = Column(Float)
    error = Column(String(120))
    lag_ms = Column(Float, nullable=False)
    __table_args__ = (
        ForeignKeyConstraint(
            ["monitor_id", "monitor_version_id"],
            ["monitor_versions.monitor_id", "monitor_versions.id"],
            name="fk_result_monitor_version",
            ondelete="CASCADE",
        ),
        UniqueConstraint("monitor_id", "scheduled_for"),
        Index("ix_results_monitor_time", "monitor_id", "observed_at"),
        Index("ix_check_results_retention", "observed_at"),
        Index("ix_check_results_version", "monitor_id", "monitor_version_id"),
        CheckConstraint("status_code IS NULL OR status_code BETWEEN 100 AND 599", name="result_status_code"),
        CheckConstraint(
            "latency_ms IS NULL OR (latency_ms >= 0 AND latency_ms < 'Infinity'::float8)", name="result_latency"
        ),
        CheckConstraint("lag_ms >= 0 AND lag_ms < 'Infinity'::float8", name="result_lag"),
    )


class Incident(Base):
    __tablename__ = "incidents"
    id = identity()
    monitor_id = fk("monitors", index=True)
    opened_at = timecol(nullable=False)
    closed_at = timecol()
    opening_result_id = Column(UUID(as_uuid=True), ForeignKey("check_results.id", ondelete="SET NULL"))
    closing_result_id = Column(UUID(as_uuid=True), ForeignKey("check_results.id", ondelete="SET NULL"))
    __table_args__ = (
        Index("one_open_incident", "monitor_id", unique=True, postgresql_where=text("closed_at IS NULL")),
        Index("ix_incidents_retention", "closed_at"),
        CheckConstraint("closed_at IS NULL OR closed_at >= opened_at", name="incident_time_order"),
    )


class Channel(Base):
    __tablename__ = "notification_channels"
    id = identity()
    owner_id = fk("users", index=True)
    name = Column(String(100), nullable=False)
    kind = Column(String(10), nullable=False)
    config = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    __table_args__ = (CheckConstraint("kind IN ('email', 'webhook')", name="channel_kind"),)


class Outbox(Base):
    __tablename__ = "notification_outbox"
    id = identity()
    # Retiring old incidents must not silently delete queued alerts.
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"))
    channel_id = fk("notification_channels", index=True)
    event_kind = Column(String(20), nullable=False)
    payload = Column(JSON, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt = timecol(default=now, nullable=False, index=True)
    lease_id = Column(UUID(as_uuid=True))
    lease_until = timecol()
    delivered_at = timecol()
    exhausted = Column(Boolean, nullable=False, default=False)
    last_error = Column(String(120))
    __table_args__ = (
        UniqueConstraint("incident_id", "event_kind", "channel_id"),
        Index("ix_outbox_retention", "delivered_at"),
        CheckConstraint("event_kind IN ('outage', 'recovery', 'test')", name="outbox_event_kind"),
        CheckConstraint("attempts >= 0", name="outbox_attempts"),
        CheckConstraint("(lease_id IS NULL) = (lease_until IS NULL)", name="outbox_lease_pair"),
    )


class Audit(Base):
    __tablename__ = "audit_events"
    id = identity()
    user_id = fk("users", index=True)
    kind = Column(String(60), nullable=False)
    resource_type = Column(String(32))
    resource_id = Column(UUID(as_uuid=True))
    created_at = timecol(default=now, nullable=False, index=True)


class Heartbeat(Base):
    __tablename__ = "worker_heartbeats"
    id = Column(String(64), primary_key=True)
    seen_at = timecol(nullable=False, index=True)
    inflight = Column(Integer, nullable=False)
    __table_args__ = (CheckConstraint("inflight >= 0", name="heartbeat_inflight"),)
