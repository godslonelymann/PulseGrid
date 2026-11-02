"""Direct monitor ownership, versioned history and database guardrails."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # Never silently turn selective/no-coverage channels into account-wide alerts.
    if connection.scalar(sa.text("SELECT count(*) FROM notification_channels WHERE NOT all_groups")):
        raise RuntimeError(
            "Migration stopped: selective alert destinations exist. Review their saved rules and explicitly "
            "convert them to account-wide coverage or retire them before upgrading. No coverage was changed."
        )
    cutover = connection.scalar(sa.text("SELECT now()"))
    op.rename_table("session_families", "sessions")
    op.execute("ALTER INDEX ix_session_families_user_id RENAME TO ix_sessions_user_id")
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.alter_column("refresh_tokens", "family_id", new_column_name="session_id")
    op.execute("ALTER INDEX ix_refresh_tokens_family_id RENAME TO ix_refresh_tokens_session_id")
    op.create_foreign_key(
        "fk_refresh_replacement", "refresh_tokens", "refresh_tokens", ["replacement_id"], ["id"], ondelete="SET NULL"
    )

    op.add_column("monitors", sa.Column("owner_id", sa.UUID(), nullable=True))
    op.add_column("monitors", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE monitors m SET owner_id=b.owner_id, paused=m.paused OR b.paused, version=m.version+1 FROM buckets b WHERE b.id=m.bucket_id"
    )
    op.alter_column("monitors", "owner_id", nullable=False)
    op.create_foreign_key("fk_monitors_owner", "monitors", "users", ["owner_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_monitors_owner_id", "monitors", ["owner_id"])
    op.execute("""CREATE FUNCTION pg_temp.pulse_statuses(json) RETURNS smallint[]
        LANGUAGE sql IMMUTABLE STRICT AS $$ SELECT ARRAY(SELECT json_array_elements_text($1)::smallint) $$""")
    op.alter_column(
        "monitors", "statuses", type_=sa.ARRAY(sa.SmallInteger()), postgresql_using="pg_temp.pulse_statuses(statuses)"
    )

    op.rename_table("monitor_activity", "monitor_versions")
    op.drop_index("ix_monitor_activity_monitor_id", table_name="monitor_versions")
    op.add_column(
        "monitor_versions", sa.Column("settings_known", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    for column in [
        sa.Column("name", sa.String(100)),
        sa.Column("url", sa.Text()),
        sa.Column("method", sa.String(4)),
        sa.Column("statuses", sa.ARRAY(sa.SmallInteger())),
        sa.Column("timeout", sa.Integer()),
    ]:
        op.add_column("monitor_versions", column)
    connection.execute(sa.text("UPDATE monitor_versions SET ends_at=:at WHERE ends_at IS NULL"), {"at": cutover})
    op.add_column("check_results", sa.Column("monitor_version_id", sa.UUID(), nullable=True))
    connection.execute(
        sa.text("""UPDATE check_results r SET monitor_version_id=(
        SELECT v.id FROM monitor_versions v WHERE v.monitor_id=r.monitor_id
        AND v.starts_at<=r.observed_at AND v.ends_at>=r.observed_at
        ORDER BY v.starts_at DESC, v.id LIMIT 1)""")
    )
    # Preserve checks outside recorded activity windows without inventing old URLs or coverage.
    connection.execute(
        sa.text("""WITH missing AS (
        INSERT INTO monitor_versions(id,monitor_id,starts_at,ends_at,interval,active,settings_known)
        SELECT gen_random_uuid(),m.id,min(r.observed_at),greatest(:at,max(r.observed_at)),m.interval,false,false
        FROM monitors m JOIN check_results r ON r.monitor_id=m.id WHERE r.monitor_version_id IS NULL
        GROUP BY m.id RETURNING id,monitor_id)
        UPDATE check_results r SET monitor_version_id=v.id FROM missing v
        WHERE r.monitor_id=v.monitor_id AND r.monitor_version_id IS NULL"""),
        {"at": cutover},
    )
    connection.execute(
        sa.text("""INSERT INTO monitor_versions
        (id,monitor_id,starts_at,interval,active,settings_known,name,url,method,statuses,timeout)
        SELECT gen_random_uuid(),id,:at,interval,NOT paused,true,name,url,method,statuses,timeout FROM monitors"""),
        {"at": cutover},
    )
    op.alter_column("monitor_versions", "settings_known", server_default=None)
    op.create_unique_constraint("uq_monitor_versions_monitor_id", "monitor_versions", ["monitor_id", "id"])
    op.create_index("ix_monitor_versions_time", "monitor_versions", ["monitor_id", "starts_at"])
    op.create_index(
        "one_current_monitor_version",
        "monitor_versions",
        ["monitor_id"],
        unique=True,
        postgresql_where=sa.text("ends_at IS NULL"),
    )
    op.alter_column("check_results", "monitor_version_id", nullable=False)
    op.create_foreign_key(
        "fk_result_monitor_version",
        "check_results",
        "monitor_versions",
        ["monitor_id", "monitor_version_id"],
        ["monitor_id", "id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_check_results_retention", "check_results", ["observed_at"])
    op.create_index("ix_check_results_version", "check_results", ["monitor_id", "monitor_version_id"])
    op.create_index("ix_incidents_retention", "incidents", ["closed_at"])
    op.create_index("ix_outbox_retention", "notification_outbox", ["delivered_at"])
    op.create_index("ix_worker_heartbeats_seen_at", "worker_heartbeats", ["seen_at"])
    op.add_column("audit_events", sa.Column("resource_type", sa.String(32), nullable=True))
    op.add_column("audit_events", sa.Column("resource_id", sa.UUID(), nullable=True))
    for foreign_key in sa.inspect(connection).get_foreign_keys("notification_outbox"):
        if foreign_key["referred_table"] == "incidents":
            op.drop_constraint(foreign_key["name"], "notification_outbox", type_="foreignkey")
    op.create_foreign_key(
        "fk_outbox_incident", "notification_outbox", "incidents", ["incident_id"], ["id"], ondelete="SET NULL"
    )

    statuses = "cardinality(statuses) BETWEEN 1 AND 100 AND array_ndims(statuses) = 1 AND array_position(statuses, NULL) IS NULL AND 100 <= ALL(statuses) AND 599 >= ALL(statuses)"
    checks = {
        "users": {"user_login_identity": "password_hash IS NOT NULL OR google_sub IS NOT NULL"},
        "monitors": {
            "monitor_interval": "interval BETWEEN 30 AND 86400",
            "monitor_timeout": "timeout BETWEEN 1 AND 20 AND timeout < interval",
            "monitor_method": "method IN ('GET', 'HEAD')",
            "monitor_statuses": statuses,
            "monitor_version_positive": "version >= 1",
        },
        "monitor_versions": {
            "version_time_order": "ends_at IS NULL OR ends_at >= starts_at",
            "version_interval": "interval BETWEEN 30 AND 86400",
            "version_timeout": "timeout IS NULL OR (timeout BETWEEN 1 AND 20 AND timeout < interval)",
            "version_method": "method IS NULL OR method IN ('GET', 'HEAD')",
            "version_statuses": "statuses IS NULL OR (" + statuses + ")",
            "version_known_settings": "NOT settings_known OR (name IS NOT NULL AND url IS NOT NULL AND method IS NOT NULL AND statuses IS NOT NULL AND timeout IS NOT NULL)",
        },
        "monitor_runtime": {
            "runtime_state": "state IN ('UNKNOWN', 'UP', 'SUSPECT', 'DOWN')",
            "runtime_counters": "failures >= 0 AND successes >= 0 AND lease_version >= 0",
            "runtime_lease_pair": "(lease_id IS NULL) = (lease_until IS NULL)",
        },
        "check_results": {
            "result_status_code": "status_code IS NULL OR status_code BETWEEN 100 AND 599",
            "result_latency": "latency_ms IS NULL OR (latency_ms >= 0 AND latency_ms < 'Infinity'::float8)",
            "result_lag": "lag_ms >= 0 AND lag_ms < 'Infinity'::float8",
        },
        "incidents": {"incident_time_order": "closed_at IS NULL OR closed_at >= opened_at"},
        "notification_channels": {"channel_kind": "kind IN ('email', 'webhook')"},
        "notification_outbox": {
            "outbox_event_kind": "event_kind IN ('outage', 'recovery', 'test')",
            "outbox_attempts": "attempts >= 0",
            "outbox_lease_pair": "(lease_id IS NULL) = (lease_until IS NULL)",
        },
        "worker_heartbeats": {"heartbeat_inflight": "inflight >= 0"},
    }
    # Existing claims cannot be reused by a pre-migration worker.
    op.execute("UPDATE monitor_runtime SET lease_id=NULL,lease_until=NULL")
    op.execute("UPDATE notification_outbox SET lease_id=NULL,lease_until=NULL")
    for table, constraints in checks.items():
        for name, expression in constraints.items():
            op.create_check_constraint(name, table, expression)
    op.drop_table("notification_rules")
    op.drop_column("monitors", "bucket_id")
    op.drop_column("notification_channels", "all_groups")
    op.drop_table("buckets")


def downgrade():
    raise RuntimeError(
        "0005 removes group information. Restore the verified pre-migration backup and matching source release to roll back; do not invent old groups or historical settings."
    )
