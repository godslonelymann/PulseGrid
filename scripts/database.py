"""Native PostgreSQL backup and guarded restore for the selected environment."""

import argparse
import hashlib
import os
import subprocess
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
        path.with_suffix(".dump.sha256").write_text(
            hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
        )
    finally:
        partial.unlink(missing_ok=True)
    print(
        f"Backup verified as readable: {path}\nCopy the dump and checksum off-server. Back up ENCRYPTION_KEY separately."
    )


def restore(path, writers_stopped):
    expected = path.with_suffix(".dump.sha256").read_text().split()[0]
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Backup checksum mismatch")
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
    subprocess.run(
        [postgres_tool("pg_restore"), "--dbname", dsn, "--exit-on-error", "--no-owner", "--no-privileges", str(path)],
        env=environment,
        check=True,
    )
    with psycopg.connect(dsn, password=url.password) as restored:
        restored.execute("UPDATE sessions SET revoked_at = now()")
        restored.execute("DELETE FROM recovery_codes")
        restored.execute(
            "UPDATE users SET last_totp_step = GREATEST(COALESCE(last_totp_step,0), "
            "floor(extract(epoch FROM now())/30)::integer + 1) WHERE totp_secret IS NOT NULL"
        )
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
    else:
        restore(args.path, args.writers_stopped)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
