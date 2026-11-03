from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from pulsegrid.worker.probe import validate_url


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class Factor(BaseModel):
    challenge: str = Field(max_length=100)
    code: str = Field(min_length=6, max_length=64)


class Reauth(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(default="", max_length=64)


class ConfirmMFA(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class PasswordChange(Reauth):
    new_password: str = Field(min_length=12, max_length=256)


class MonitorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=100)
    url: str = Field(max_length=2048)
    method: Literal["GET", "HEAD"] = "GET"
    statuses: list[int] = Field(default=[200], min_length=1, max_length=100)
    interval: int = Field(default=30, ge=30, le=86400)
    timeout: int = Field(default=10, ge=1, le=20)

    @model_validator(mode="after")
    def validate(self):
        parsed = validate_url(self.url)
        self.name = self.name.strip() or parsed.hostname[:100]
        if self.timeout >= self.interval or any(not 100 <= s <= 599 for s in self.statuses):
            raise ValueError("Invalid timeout or accepted status")
        return self


class ChannelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["webhook", "email"]
    destination: str = Field(default="", max_length=2048)
    enabled: bool = True

    @model_validator(mode="after")
    def validate(self):
        if self.kind == "webhook":
            validate_url(self.destination)
        return self


class MonitorOut(MonitorInput):
    id: UUID
    paused: bool
    archived_at: datetime | None
    state: Literal["UP", "DOWN", "SUSPECT", "UNKNOWN", "PAUSED"]
    observed_state: str
    last_observed: datetime | None


class CheckOut(BaseModel):
    id: UUID
    monitor_version_id: UUID
    scheduled_for: datetime
    observed_at: datetime
    success: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None
    lag_ms: float


class IncidentOut(BaseModel):
    id: UUID
    opened_at: datetime
    closed_at: datetime | None


class ChannelOut(BaseModel):
    id: UUID
    name: str
    kind: Literal["webhook", "email"]
    enabled: bool
    email: str | None = None


class HistogramBin(BaseModel):
    range: str
    count: int


class LatencyBucket(BaseModel):
    starts_at: datetime
    median_ms: float | None


class MetricsOut(BaseModel):
    window_start: datetime
    window_end: datetime
    retention_clipped: bool
    completed: int
    successful: int
    failed: int
    expected: int
    missing: int
    uptime: float | None
    coverage: float | None
    median_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    histogram: list[HistogramBin]
    latency_bucket_seconds: int
    latency_trend: list[LatencyBucket]
    latency_definition: str
    coverage_definition: str


class ErrorOut(BaseModel):
    detail: str | list[dict]
    request_id: str | None = None


class LoginOut(BaseModel):
    mfa_required: bool
    challenge: str | None = None


class MeOut(BaseModel):
    id: UUID
    email: str
    mfa_enabled: bool
    password_enabled: bool
    google_connected: bool
    session_id: UUID


class SessionOut(BaseModel):
    id: UUID
    label: str
    created_at: datetime
    last_activity: datetime
    current: bool


class ActionOut(BaseModel):
    ok: bool
    detail: str | None = None


class EnrollmentOut(BaseModel):
    secret: str
    uri: str


class CodesOut(BaseModel):
    recovery_codes: list[str]


class DashboardOut(BaseModel):
    monitors: list[MonitorOut]
    counts: dict[str, int]
    limit: int
    monitor_total: int


class MonitorVersionOut(BaseModel):
    id: UUID
    starts_at: datetime
    ends_at: datetime | None
    interval: int
    active: bool
    settings_known: bool
    name: str | None
    url: str | None
    method: str | None
    statuses: list[int] | None
    timeout: int | None


class DeliveryOut(BaseModel):
    id: UUID
    channel_id: UUID
    event_kind: str
    attempts: int
    delivered_at: datetime | None
    exhausted: bool
    last_error: str | None
    next_attempt: datetime
    test_queued_at: datetime | None
