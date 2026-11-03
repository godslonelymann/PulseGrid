"""Rehearse the destructive schema transition against an isolated legacy database."""

import os
import subprocess
import sys
import uuid
from datetime import timedelta

import psycopg
import pytest
from pulsegrid.auth.security import decrypt
from pulsegrid.storage.db import now
from sqlalchemy import create_engine, inspect

DATABASE = "pulsegrid_schema_fixture"
DSN = f"postgresql://pulsegrid:test-only@127.0.0.1:55433/{DATABASE}"


def migrate(revision):
    environment = {**os.environ, "DATABASE_URL": DSN.replace("postgresql://", "postgresql+psycopg://")}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "apps/api/alembic.ini", "upgrade", revision],
        env=environment,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def legacy_database():
    # Dedicated disposable fixture on the test cluster, never a development/production database.
    assert DATABASE == "pulsegrid_schema_fixture"
    with psycopg.connect("postgresql://pulsegrid:test-only@127.0.0.1:55433/postgres", autocommit=True) as admin:
        admin.execute("DROP DATABASE IF EXISTS pulsegrid_schema_fixture WITH (FORCE)")
        admin.execute("CREATE DATABASE pulsegrid_schema_fixture")
    result = migrate("0004")
    assert result.returncode == 0, result.stderr
    yield


async def test_migration_preserves_ownership_history_and_security(legacy_database):
    (
        owner,
        other,
        group,
        second_group,
        mid,
        second,
        activity,
        check,
        orphan,
        session_id,
        token,
        replacement,
        channel,
        incident,
        alert,
    ) = [uuid.uuid4() for _ in range(15)]
    at = now()
    with psycopg.connect(DSN) as c:
        for uid, email in [(owner, "old@example.test"), (other, "other@example.test")]:
            c.execute(
                "INSERT INTO users(id,email,password_hash,created_at) VALUES(%s,%s,'existing-password-hash',%s)",
                (uid, email, at),
            )
        for bid, uid, paused in [(group, owner, True), (second_group, other, False)]:
            c.execute(
                "INSERT INTO buckets(id,owner_id,name,paused,is_default) VALUES(%s,%s,'Legacy',%s,false)",
                (bid, uid, paused),
            )
        for monitor, bid in [(mid, group), (second, second_group)]:
            c.execute(
                """INSERT INTO monitors(id,bucket_id,name,url,method,statuses,interval,timeout,paused,version,created_at)
                VALUES(%s,%s,'Legacy endpoint','https://example.com/current','GET','[200]',30,1,false,7,%s)""",
                (monitor, bid, at - timedelta(hours=2)),
            )
            c.execute(
                "INSERT INTO monitor_runtime(id,state,failures,successes,due_at,lease_version) VALUES(%s,'UNKNOWN',0,0,%s,0)",
                (monitor, at),
            )
        c.execute(
            "INSERT INTO monitor_activity(id,monitor_id,starts_at,interval,active) VALUES(%s,%s,%s,30,false)",
            (activity, mid, at - timedelta(hours=1)),
        )
        for rid, observed in [(check, at - timedelta(minutes=20)), (orphan, at - timedelta(hours=2))]:
            c.execute(
                "INSERT INTO check_results(id,monitor_id,scheduled_for,observed_at,success,status_code,latency_ms,lag_ms) VALUES(%s,%s,%s,%s,true,200,10,0)",
                (rid, mid, observed, observed),
            )
        c.execute(
            "INSERT INTO session_families(id,user_id,created_at,expires_at,last_activity,label) VALUES(%s,%s,%s,%s,%s,'Saved login')",
            (session_id, owner, at, at + timedelta(days=1), at),
        )
        c.execute(
            "INSERT INTO refresh_tokens(id,family_id,digest,consumed_at,expires_at,replacement_id) VALUES(%s,%s,%s,%s,%s,%s)",
            (token, session_id, "a" * 64, at, at + timedelta(days=1), replacement),
        )
        c.execute(
            "INSERT INTO refresh_tokens(id,family_id,digest,expires_at) VALUES(%s,%s,%s,%s)",
            (replacement, session_id, "b" * 64, at + timedelta(days=1)),
        )
        c.execute(
            "INSERT INTO notification_channels(id,owner_id,name,kind,config,enabled,all_groups) VALUES(%s,%s,'Saved webhook','webhook','encrypted-destination',true,true)",
            (channel, owner),
        )
        c.execute(
            "INSERT INTO notification_rules(id,channel_id,bucket_id,enabled) VALUES(%s,%s,%s,true)",
            (uuid.uuid4(), channel, group),
        )
        c.execute(
            "INSERT INTO incidents(id,monitor_id,opened_at,opening_result_id) VALUES(%s,%s,%s,%s)",
            (incident, mid, at - timedelta(minutes=20), check),
        )
        c.execute(
            "INSERT INTO notification_outbox(id,incident_id,channel_id,event_kind,payload,attempts,next_attempt,exhausted) VALUES(%s,%s,%s,'outage','{}',2,%s,false)",
            (alert, incident, channel, at),
        )
    result = migrate("head")
    assert result.returncode == 0, result.stderr
    with psycopg.connect(DSN) as c:
        assert c.execute("SELECT owner_id,paused,version FROM monitors WHERE id=%s", (mid,)).fetchone() == (
            owner,
            True,
            8,
        )
        assert c.execute("SELECT owner_id,paused FROM monitors WHERE id=%s", (second,)).fetchone() == (other, False)
        assert c.execute("SELECT id FROM sessions").fetchone()[0] == session_id
        assert c.execute("SELECT session_id,replacement_id FROM refresh_tokens WHERE id=%s", (token,)).fetchone() == (
            session_id,
            replacement,
        )
        assert c.execute("SELECT config FROM notification_channels").fetchone()[0] == "encrypted-destination"
        assert c.execute("SELECT attempts,incident_id FROM notification_outbox WHERE id=%s", (alert,)).fetchone() == (
            2,
            incident,
        )
        rows = c.execute(
            "SELECT r.id,v.monitor_id,v.settings_known,v.url FROM check_results r JOIN monitor_versions v ON r.monitor_version_id=v.id"
        ).fetchall()
        assert len(rows) == 2 and {r[0] for r in rows} == {check, orphan}
        assert all(r[1:] == (mid, False, None) for r in rows)
        current = c.execute(
            "SELECT settings_known,url,active FROM monitor_versions WHERE monitor_id=%s AND ends_at IS NULL", (mid,)
        ).fetchone()
        assert current == (True, "https://example.com/current", False)
        assert c.execute(
            "SELECT starts_at,interval,active,settings_known,url FROM monitor_versions WHERE id=%s", (activity,)
        ).fetchone() == (at - timedelta(hours=1), 30, False, False, None)
        c.execute("DELETE FROM incidents WHERE id=%s", (incident,))
        assert c.execute("SELECT incident_id FROM notification_outbox WHERE id=%s", (alert,)).fetchone() == (None,)
    engine = create_engine(DSN.replace("postgresql://", "postgresql+psycopg://"))
    try:
        tables = set(inspect(engine).get_table_names())
        assert len(tables - {"alembic_version"}) == 13
        assert not tables & {"buckets", "notification_rules", "monitor_activity", "session_families"}
    finally:
        engine.dispose()


async def test_migration_refuses_to_broaden_selective_alerts(legacy_database):
    with psycopg.connect(DSN) as c:
        uid = uuid.uuid4()
        c.execute(
            "INSERT INTO users(id,email,password_hash,created_at) VALUES(%s,'scope@example.test','hash',now())", (uid,)
        )
        c.execute(
            "INSERT INTO notification_channels(id,owner_id,name,kind,config,enabled,all_groups) VALUES(%s,%s,'Scoped','email','encrypted',true,false)",
            (uuid.uuid4(), uid),
        )
    result = migrate("head")
    assert result.returncode != 0 and "selective alert destinations exist" in result.stderr
    with psycopg.connect(DSN) as c:
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0004"
        assert c.execute("SELECT all_groups FROM notification_channels").fetchone()[0] is False
        assert c.execute("SELECT to_regclass('session_families'),to_regclass('monitor_activity')").fetchone() == (
            "session_families",
            "monitor_activity",
        )


async def test_migration_enables_email_for_google_accounts_without_preferences(legacy_database):
    assert (result := migrate("0005")).returncode == 0, result.stderr
    new, disabled, legacy = (uuid.uuid4() for _ in range(3))
    with psycopg.connect(DSN) as connection:
        connection.execute(
            "INSERT INTO users(id,email,google_sub,created_at) VALUES "
            "(%s,'new@example.test','sub-new',now()), (%s,'disabled@example.test','sub-disabled',now())",
            (new, disabled),
        )
        connection.execute(
            "INSERT INTO users(id,email,password_hash,created_at) VALUES (%s,'legacy@example.test','hash',now())",
            (legacy,),
        )
        connection.execute(
            "INSERT INTO notification_channels(id,owner_id,name,kind,config,enabled) "
            "VALUES (%s,%s,'Existing preference','email','existing',false)",
            (uuid.uuid4(), disabled),
        )
    assert (result := migrate("head")).returncode == 0, result.stderr
    with psycopg.connect(DSN) as connection:
        channels = {
            owner: (config, enabled)
            for owner, config, enabled in connection.execute(
                "SELECT owner_id,config,enabled FROM notification_channels WHERE kind = 'email'"
            )
        }
    assert set(channels) == {new, disabled}
    assert decrypt(channels[new][0]) == "new@example.test" and channels[new][1]
    assert channels[disabled] == ("existing", False)


async def test_migration_enables_alerts_for_confirmed_email_without_preferences(legacy_database):
    assert (result := migrate("0007")).returncode == 0, result.stderr
    confirmed, disabled, unverified = (uuid.uuid4() for _ in range(3))
    with psycopg.connect(DSN) as connection:
        connection.execute(
            "INSERT INTO users(id,email,password_hash,created_at,email_verified_at) VALUES "
            "(%s,'confirmed@example.test','hash',now(),now()), "
            "(%s,'disabled@example.test','hash',now(),now()), "
            "(%s,'unverified@example.test','hash',now(),NULL)",
            (confirmed, disabled, unverified),
        )
        connection.execute(
            "INSERT INTO notification_channels(id,owner_id,name,kind,config,enabled) "
            "VALUES (%s,%s,'Existing preference','email','existing',false)",
            (uuid.uuid4(), disabled),
        )
    assert (result := migrate("head")).returncode == 0, result.stderr
    with psycopg.connect(DSN) as connection:
        channels = {
            owner: (config, enabled)
            for owner, config, enabled in connection.execute(
                "SELECT owner_id,config,enabled FROM notification_channels WHERE kind = 'email'"
            )
        }
    assert set(channels) == {confirmed, disabled}
    assert decrypt(channels[confirmed][0]) == "confirmed@example.test" and channels[confirmed][1]
    assert channels[disabled] == ("existing", False)


async def test_old_backup_is_rejected_before_replacing_current_database(legacy_database, owner, tmp_path):
    from test_native_operations import command

    environment = {**os.environ, "DATABASE_URL": DSN.replace("postgresql://", "postgresql+psycopg://")}
    backup = subprocess.run(
        [sys.executable, "scripts/run-env.py", sys.executable, "scripts/database.py", "backup", str(tmp_path)],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert backup.returncode == 0, backup.stderr
    archive = next(tmp_path.glob("*.dump"))
    code, output = await command("restore", archive, "--confirm-replace-database")
    assert code != 0 and "Backup uses an older schema" in output, output
    assert (await owner.get("/api/v1/me")).status_code == 200
