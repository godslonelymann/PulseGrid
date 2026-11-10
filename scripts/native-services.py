"""Manage repository-owned, loopback-only development and verification services."""

import argparse
import fcntl
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import psycopg
from native_common import postgres_tool
from psycopg import sql
from redis import Redis
from redis.exceptions import RedisError

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    "dev": (55432, 56379, "pulsegrid", "pulsegrid", "local-development-only"),
    "test": (55433, 56380, "pulsegrid_test", "pulsegrid", "test-only"),
    "capacity": (55434, 56383, "pulsegrid_capacity", "pulsegrid", "capacity-only"),
    "staging": (55435, 56381, "pulsegrid", "pulsegrid", "local-staging-only"),
    "feasibility": (55436, 56382, "pulse_test", "pulse_test", "local_feasibility_only"),
}


def binary(name):
    if name in ("pg_ctl", "initdb"):
        return postgres_tool(name)
    path = shutil.which(name)
    if not path or not Path(path).is_file():
        raise RuntimeError(f"Install {name} and add it to PATH (or set PG_BIN for PostgreSQL). See docs/OPERATIONS.md")
    return path


def run(*args, **kwargs):
    # PostgreSQL on macOS refuses to start ("postmaster became multithreaded") without a valid locale.
    env = {**os.environ, "LC_ALL": os.environ.get("LC_ALL") or "C"}
    return subprocess.run(args, check=True, env=env, **kwargs)


def free_port(port):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            raise RuntimeError(
                f"Port {port} is in use by another service; stop it before starting this profile"
            ) from None


def pg_running(data):
    return (data / "PG_VERSION").exists() and subprocess.run(
        [binary("pg_ctl"), "-D", str(data), "status"], capture_output=True
    ).returncode == 0


def redis_running(directory, port):
    pid_file = directory / "redis.pid"
    if not pid_file.exists():
        return False
    try:
        with Redis(host="127.0.0.1", port=port, socket_connect_timeout=1, socket_timeout=1) as client:
            return client.info("server")["process_id"] == int(pid_file.read_text().strip())
    except (OSError, ValueError, RedisError):
        # A stopped service is expected during fault tests.
        return False


def postgres(action, directory, profile):
    port, _, database, user, password = PROFILES[profile]
    data = directory / "postgres"
    running = pg_running(data)
    if action == "status":
        print(f"{profile} postgres: {'running' if running else 'stopped'} (127.0.0.1:{port})")
        return
    if action == "down":
        if running:
            run(binary("pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop")
        return
    if not running:
        free_port(port)
        if not (data / "PG_VERSION").exists():
            with tempfile.NamedTemporaryFile(mode="w", dir=directory) as secret:
                secret.write(password + "\n")
                secret.flush()
                run(
                    binary("initdb"),
                    "-D",
                    str(data),
                    "-U",
                    user,
                    "--pwfile",
                    secret.name,
                    "--auth-host=scram-sha-256",
                    "--auth-local=trust",
                    "--encoding=UTF8",
                    "--no-locale",
                    stdout=subprocess.DEVNULL,
                )
            with (data / "postgresql.conf").open("a") as config:
                config.write(f"\nlisten_addresses = '127.0.0.1'\nport = {port}\nunix_socket_directories = ''\n")
        run(binary("pg_ctl"), "-D", str(data), "-l", str(directory / "postgres.log"), "-w", "start")
    with psycopg.connect(
        host="127.0.0.1", port=port, user=user, password=password, dbname="postgres", autocommit=True
    ) as connection:
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,)).fetchone():
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))


def redis_service(action, directory, profile):
    port = PROFILES[profile][1]
    running = redis_running(directory, port)
    if action == "status":
        print(f"{profile} redis: {'running' if running else 'stopped'} (127.0.0.1:{port})")
        return
    if action == "down":
        if running:
            with Redis(host="127.0.0.1", port=port) as client:
                client.shutdown(nosave=True)
        return
    if running:
        return
    free_port(port)
    config = directory / "redis.conf"
    config.write_text(
        f'bind 127.0.0.1\nprotected-mode yes\nport {port}\nsave ""\nappendonly no\n'
        f'daemonize yes\npidfile "{directory / "redis.pid"}"\n'
        f'logfile "{directory / "redis.log"}"\ndir "{directory}"\n'
        "maxmemory 128mb\nmaxmemory-policy noeviction\n"
    )
    run(binary("redis-server"), str(config))
    for _ in range(100):
        if redis_running(directory, port):
            return
        time.sleep(0.1)
    raise RuntimeError(f"Redis did not become ready; inspect {directory / 'redis.log'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("up", "down", "restart", "status", "ports"))
    parser.add_argument("profile", choices=PROFILES)
    parser.add_argument("service", nargs="?", choices=("postgres", "redis"))
    args = parser.parse_args()
    if args.action == "ports":
        print(json.dumps(dict(zip(("postgres", "redis"), PROFILES[args.profile][:2]))))
        return
    directory = ROOT / ".local-services" / args.profile
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for service in [args.service] if args.service else ["postgres", "redis"]:
            handler = postgres if service == "postgres" else redis_service
            if args.action == "restart":
                handler("down", directory, args.profile)
            handler("up" if args.action == "restart" else args.action, directory, args.profile)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
