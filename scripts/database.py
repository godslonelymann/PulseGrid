"""Native PostgreSQL backup and guarded restore for the selected environment."""

import argparse
import hashlib
import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from native_common import postgres_tool
from psycopg import sql
from psycopg.conninfo import make_conninfo
from redis import Redis
from sqlalchemy.engine import make_url


def connection():
    from pulsegrid.config import settings

    config = settings()
    url = make_url(config.database_url)
    if url.get_backend_name() != "postgresql" or not url.database:
        raise RuntimeError("DATABASE_URL must select a PostgreSQL database")
    params = dict(url.query)
    params.update(host=url.host or "localhost", port=url.port or 5432, user=url.username, dbname=url.database)
    params = {key: value for key, value in params.items() if value is not None}
    dsn = make_conninfo(**params)
    environment = os.environ.copy()
    environment["PGPASSWORD"] = url.password or ""
    return config, url, dsn, environment


def backup(directory):
    _, _, dsn, environment = connection()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"pulsegrid-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.dump"
    partial = path.with_suffix(".partial")
    try:
        with partial.open("xb") as output:
            partial.chmod(0o600)
            subprocess.run(
                [postgres_tool("pg_dump"), "--dbname", dsn, "--format=custom"],
                env=environment,
                stdout=output,
                check=True,
            )
        with path.with_suffix(".dump.list").open("w") as listing:
            subprocess.run([postgres_tool("pg_restore"), "--list", str(partial)], stdout=listing, check=True)
        partial.replace(path)
        path.with_suffix(".dump.sha256").write_text(sha256(path) + "  " + path.name + "\n")
    finally:
        partial.unlink(missing_ok=True)
    print(
        f"Backup verified as readable: {path}\nCopy the dump and checksum off-server. Back up ENCRYPTION_KEY separately."
    )


def sha256(path):
    with path.open("rb") as archive:
        return hashlib.file_digest(archive, "sha256").hexdigest()


def verify_archive(path):
    listing = subprocess.check_output([postgres_tool("pg_restore"), "--list", str(path)], text=True)
    archive_tables = {
        parts[5]
        for line in listing.splitlines()
        if len(parts := line.split()) >= 6 and parts[3:5] == ["TABLE", "public"]
    }
    if not {"sessions", "monitor_versions"} <= archive_tables:
        raise RuntimeError(
            "Backup uses an older schema. Restore with its matching source release, then migrate; the selected database was not changed"
        )


def load_archive(path, dsn, environment):
    subprocess.run(
        [postgres_tool("pg_restore"), "--dbname", dsn, "--exit-on-error", "--no-owner", "--no-privileges", str(path)],
        env=environment,
        check=True,
    )


def invalidate_credentials(restored):
    restored.execute("UPDATE sessions SET revoked_at = now()")
    restored.execute("DELETE FROM recovery_codes")
    restored.execute(
        "UPDATE users SET last_totp_step = GREATEST(COALESCE(last_totp_step,0), "
        "floor(extract(epoch FROM now())/30)::integer + 1) WHERE totp_secret IS NOT NULL"
    )


def rehearse(path):
    """Load into a new database and verify it without touching the selected database or Redis."""
    from cryptography.fernet import Fernet, InvalidToken

    verify_archive(path)
    config, url, dsn, environment = connection()
    scratch = f"pulsegrid_rehearsal_{uuid.uuid4().hex}"
    identifier = sql.Identifier(scratch)
    target = make_conninfo(dsn, dbname=scratch)
    cipher = Fernet(config.encryption_key.encode())
    with psycopg.connect(make_conninfo(dsn, dbname="postgres"), password=url.password, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(identifier))
        try:
            load_archive(path, target, environment)
            with psycopg.connect(target, password=url.password) as restored:
                revision = restored.execute("SELECT version_num FROM alembic_version").fetchone()
                if not revision:
                    raise RuntimeError("The restored database has no migration revision")
                for table in ("users", "monitors", "check_results", "notification_channels"):
                    count = restored.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                    print(f"{table}: {count.fetchone()[0]}")
                secrets = 0
                for query in (
                    "SELECT totp_secret FROM users WHERE totp_secret IS NOT NULL",
                    "SELECT config FROM notification_channels",
                ):
                    for (encrypted,) in restored.execute(query):
                        try:
                            cipher.decrypt(encrypted.encode())
                        except InvalidToken:
                            raise RuntimeError("A restored secret could not be decrypted with ENCRYPTION_KEY") from None
                        secrets += 1
                invalidate_credentials(restored)
                if restored.execute(
                    "SELECT EXISTS (SELECT FROM sessions WHERE revoked_at IS NULL) "
                    "OR EXISTS (SELECT FROM recovery_codes) "
                    "OR EXISTS (SELECT FROM users WHERE totp_secret IS NOT NULL "
                    "AND (last_totp_step IS NULL OR last_totp_step <= floor(extract(epoch FROM now())/30)))"
                ).fetchone()[0]:
                    raise RuntimeError("Restored credential invalidation did not pass")
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(identifier))
    print(f"Restore rehearsal passed: revision {revision[0]}, {secrets} encrypted secrets verified.")


def restore(path, writers_stopped):
    expected = path.with_suffix(".dump.sha256").read_text().split()[0]
    if sha256(path) != expected:
        raise RuntimeError("Backup checksum mismatch")
    verify_archive(path)
    config, url, dsn, environment = connection()
    if url.database in ("postgres", "template0", "template1"):
        raise RuntimeError("Refusing to replace a PostgreSQL system database")
    print(f"Replacing {url.host}:{url.port or 5432}/{url.database}", flush=True)
    if not writers_stopped:
        subprocess.run(["systemctl", "stop", "pulsegrid-api.service", "pulsegrid-worker.service"], check=True)
    # On failure leave writers stopped. Restart only after every invalidation succeeds.
    with psycopg.connect(make_conninfo(dsn, dbname="postgres"), password=url.password, autocommit=True) as admin:
        if not admin.execute("SELECT rolsuper OR rolcreatedb FROM pg_roles WHERE rolname = current_user").fetchone()[0]:
            raise RuntimeError("The restore database role needs CREATEDB before a database can be replaced")
        admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (url.database,))
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(url.database)))
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(url.database), sql.Identifier(admin.info.user))
        )
    load_archive(path, dsn, environment)
    with psycopg.connect(dsn, password=url.password) as restored:
        invalidate_credentials(restored)
    with Redis.from_url(config.redis_url) as redis:
        redis.flushdb()
    if not writers_stopped:
        subprocess.run(["systemctl", "start", "pulsegrid-api.service", "pulsegrid-worker.service"], check=True)
    print(
        "Restore complete. Sessions and recovery codes invalidated. Wait up to 90s for a fresh TOTP, "
        "then regenerate recovery codes. Keep the original ENCRYPTION_KEY."
    )


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("backup").add_argument("directory", type=Path, nargs="?", default=Path("backups"))
    sub.add_parser("rehearse").add_argument("path", type=Path)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("path", type=Path)
    restore_parser.add_argument("--confirm-replace-database", action="store_true", required=True)
    restore_parser.add_argument(
        "--writers-stopped",
        action="store_true",
        help="For manually managed services: assert that API and worker are already stopped",
    )
    args = parser.parse_args()
    if args.command == "backup":
        backup(args.directory)
    elif args.command == "rehearse":
        rehearse(args.path)
    else:
        restore(args.path, args.writers_stopped)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
