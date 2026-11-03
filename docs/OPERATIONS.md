# Operator runbook

## Local development

Install Python 3.13, uv, Node 24, pnpm 10.32.1, PostgreSQL 17+ tools and Redis 7+. Caddy 2 is needed for HTTPS staging and production. Run these commands from the repository root as a normal user.

On macOS, `brew install postgresql@18 redis caddy` installs native services. The helper finds versioned Homebrew PostgreSQL installations; `PG_BIN` can select a particular toolchain. On Ubuntu, install PostgreSQL 17+ from the [official PostgreSQL APT repository](https://www.postgresql.org/download/linux/ubuntu/) and install `redis-server`. Do not start a second server on the project's ports.

```sh
uv sync --frozen
uv run python scripts/init-env.py
uv run python scripts/native-services.py up dev
uv run alembic -c apps/api/alembic.ini upgrade head
bash scripts/dev.sh
```

Open **http://localhost:3000** after configuring Google credentials as described in the README. Continue with Google creates the account; do not bootstrap a password account. The development script starts PostgreSQL/Redis, applies migrations, and runs the API, worker and frontend. Ctrl-C stops the application processes and retains the database. Stop dependencies separately with `uv run python scripts/native-services.py down dev`.

The helper manages only its own loopback services under ignored `.local-services/PROFILE/`. It refuses occupied ports, retains database files on shutdown, and uses PostgreSQL password authentication for TCP connections. These development profiles have known local passwords and must never serve public production traffic. Never delete a profile directory containing valuable data. Run the helper as a non-root user; `initdb` refuses root.

| Profile | PostgreSQL port/database | Redis port |
| --- | --- | --- |
| `dev` | 55432 / `pulsegrid` | 56379 |
| `test` | 55433 / `pulsegrid_test` | 56380 |
| `capacity` | 55434 / `pulsegrid_capacity` | 56383 |
| `staging` | 55435 / `pulsegrid` | 56381 |
| `feasibility` | 55436 / `pulse_test` | 56382 |

Use `up`, `down`, `restart`, or `status`, followed by the profile and optionally `postgres` or `redis`. Fault tests stop only the `test` profile. Capacity and feasibility have independent databases. All profile data survives shutdown; feasibility still resets its own prototype tables during experiments.

To run application processes separately after migration:

```sh
PYTHONPATH=apps/api uv run uvicorn pulsegrid.api.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
PYTHONPATH=apps/api uv run python -m pulsegrid.worker.runner
pnpm --dir apps/web dev
```

API OpenAPI: http://127.0.0.1:8000/api/docs . Browser requests use the Next.js development rewrite. HTTP/non-Secure cookies are for development only.

## Configuration

`scripts/init-env.py` generates JWT/Fernet keys in `.env` with mode 0600 and preserves an existing file. `.env.example` documents settings. Pydantic loads `.env`; environment variables win. `scripts/run-env.py COMMAND [ARGS...]` loads the file selected by `ENV_FILE` before running a command. Preserve encryption keys across releases and restores.

| Setting | Meaning |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy `postgresql+psycopg://...`; local development uses `localhost:55432`. Production uses the real native/managed database address and a unique password. URL-encode password characters. |
| `REDIS_URL` | Development uses `redis://localhost:56379/0`. Production uses a dedicated local/private Redis database. Restore flushes the selected database, so never share it with another application. |
| `JWT_SECRET` / `ENCRYPTION_KEY` | Random signing secret (32+ characters) / Fernet key; no defaults. Rotating signing keys signs out browsers. Changing encryption keys without re-encryption makes existing secrets unreadable. |
| `ENVIRONMENT`, `SECURE_COOKIES`, `PUBLIC_ORIGIN` | Production requires HTTPS and Secure cookies. Origin must exactly match the browser URL, including nondefault ports. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Required Google OAuth Web application credentials. Register `PUBLIC_ORIGIN/api/v1/auth/google/callback` exactly. Restart the API after changes. |
| `CADDY_ADDRESS`, `CADDY_EMAIL` | Public domain/IP and certificate contact. Configure these for Caddy separately from application secrets. |
| `PRIVATE_TARGET_ALLOWLIST` | Empty by default. Comma-separated exact hostname:port entries for trusted targets only. |
| `WORKER_CONCURRENCY`, `LEASE_SECONDS` | Default 20 and 60 seconds. Renewal covers queue time. Tune after measuring. |
| `WORKER_HEARTBEAT_URL` | Optional secret HTTPS ping URL for the external worker check. The worker sends a GET about once a minute. |
| `ACCESS_SECONDS`, `SESSION_DAYS` | Default five-minute access cookies and 14-day session expiry. |
| `RAW_RETENTION_DAYS`, `SECURITY_RETENTION_DAYS`, `INCIDENT_RETENTION_DAYS` | Default 30 / 90 / 365. Open incidents are retained. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS` | SMTP provider configuration. Default STARTTLS on port 587 with certificate verification. Missing configuration appears in the UI. |
| `PG_BIN` | Optional PostgreSQL 17+ binary directory for the service and backup/restore helpers. Use the same major version as an existing profile; upgrading binaries alone does not upgrade database files. |
| `BACKUP_S3_PREFIX`, `BACKUP_AGE_RECIPIENT` | Private S3 destination (`s3://bucket/prefix`) and the public age recipient for the optional daily backup. |

## Production deployment

Install the application source at `/opt/pulsegrid`, owned by a dedicated `pulsegrid` user. Install Python 3.13, uv, Node 24 and pnpm. Node must be available through the system service PATH (for example `/usr/bin/node` or `/usr/local/bin/node`). Install native PostgreSQL 17+, Redis 7+ and Caddy using their distribution/service packages. Use the database packages' services for production; the repository's profile helper is for local work.

Create a `pulsegrid` PostgreSQL login with a unique password and a database owned by that login. It must own its database and have `CREATEDB` if using the provided replacement-restore script; restrict that operator capability deliberately. Bind PostgreSQL and Redis to loopback or a private authenticated network. Do not expose their ports publicly. Use dedicated Redis state; configure a 128 MB memory limit with `noeviction` and no persistence, as the security state is temporary. Enable the database/Redis services at boot. Keep logs bounded with the host's journal policy.

Create `/etc/pulsegrid/pulsegrid.env`, owner root, group pulsegrid, mode 0640. Set fresh `JWT_SECRET`, `ENCRYPTION_KEY` and database credentials, plus:

```dotenv
ENVIRONMENT=production
SECURE_COOKIES=true
PUBLIC_ORIGIN=https://pulse.example.com
DATABASE_URL=postgresql+psycopg://pulsegrid:URL_ENCODED_PASSWORD@127.0.0.1:5432/pulsegrid
REDIS_URL=redis://127.0.0.1:6379/0
```

From `/opt/pulsegrid`, install dependencies and build as the application user:

```sh
uv sync --frozen
bash scripts/build-web.sh
ENV_FILE=/etc/pulsegrid/pulsegrid.env uv run python scripts/run-env.py .venv/bin/alembic -c apps/api/alembic.ini upgrade head
```

Install the provided service files as an operator:

```sh
sudo cp infra/systemd/pulsegrid-{api,worker,web}.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pulsegrid-api pulsegrid-worker pulsegrid-web
```

The three services bind application ports to loopback, run without root, restart after failure and use resource limits. The frontend uses Next.js standalone output; the build script copies its static assets. PostgreSQL/Redis are managed by their native service packages and must already be running. Migration is a single release step, not part of each process startup.

Install `infra/Caddyfile` at `/etc/caddy/Caddyfile`. Create `/etc/caddy/pulsegrid.env` containing only `CADDY_ADDRESS=pulse.example.com` and `CADDY_EMAIL=operator@example.com` (root:caddy, mode 0640). Add a Caddy service drop-in with `[Service]` and `EnvironmentFile=/etc/caddy/pulsegrid.env`. Reload systemd, then enable/restart Caddy using its official package service. Only Caddy should listen publicly on 80/443. Point DNS at the server, keep SSH restricted, and permit the chosen public ports. See [Caddy installation](https://caddyserver.com/docs/install) and [service operation](https://caddyserver.com/docs/running).

Caddy proxies `/api/*` to `127.0.0.1:8000` and other traffic to `127.0.0.1:3000`. Keep production Secure cookies enabled and configure both Google OAuth settings before opening the app. Verify readiness, Google sign-in, monitor creation, fresh checks, history and notification delivery after deploying. A reachable frontend alone does not prove the worker is running.

## Health and operations

- `/api/v1/health/live`: process liveness.
- `/api/v1/health/ready`: API/PostgreSQL/Redis readiness, 503 on dependency failure.
- `/api/v1/worker-status`: authenticated worker heartbeat and in-flight work.

For a simple outage email, use the **PulseGrid health email** GitHub Actions workflow.
It checks the production readiness URL every five minutes and emails
`gaikwadanurag16@gmail.com` when both attempts in a run fail. Each failed
scheduled run can send an email while the outage continues.

Before pushing the workflow to the default branch, add two repository secrets under
**Settings → Secrets and variables → Actions**. Set `PULSEGRID_SMTP_USER` to
the Gmail sender address and `PULSEGRID_SMTP_PASSWORD` to that account's Gmail
app password. After pushing, run **PulseGrid health email** manually from the
Actions tab to verify the endpoint check. The workflow runs on GitHub and does
not require an EC2 deployment.

Set up these checks from accounts outside the PulseGrid host, with a verified operator email address. Their alerts do not depend on PulseGrid SMTP.

1. In an external uptime service such as [UptimeRobot](https://help.uptimerobot.com/en/articles/11358364-how-to-create-your-first-monitor-on-uptimerobot-quick-setup-guide), create HTTPS monitors for the public dashboard (`PUBLIC_ORIGIN/`) and API readiness (`PUBLIC_ORIGIN/api/v1/health/ready`). Use GET, expect HTTP 200 and enable downtime and recovery email for both. For the existing EC2 deployment use `https://mypulsegrid.duckdns.org` as the origin.
2. Create a [Healthchecks.io check](https://healthchecks.io/docs/configuring_checks/) named **PulseGrid worker**, with a 1-minute period, 2-minute grace and an email integration. Put its secret Ping URL in `WORKER_HEARTBEAT_URL` in the server environment file and restart only `pulsegrid-worker`. Confirm the check becomes Up and sees pings about a minute apart.
3. In staging or during a planned maintenance window, stop only `pulsegrid-worker` long enough to get the missing-heartbeat email. The dashboard and readiness checks should still be Up. Restart the worker and verify recovery. Separately test a downtime and recovery email for the HTTPS monitors against a staging endpoint.

The worker sends a ping only after a successful scheduler iteration and skips it if any unpaused, unarchived monitor is more than two minutes overdue. An empty schedule can still be healthy. The app's internal worker status uses a separate 15-second threshold. Use `journalctl -u pulsegrid-api -u pulsegrid-worker -u caddy -n 100` to investigate. Synchronize the system clock for scheduling and TOTP.

The main **Alerts** screen shows the latest test and unresolved delivery failures, including retries. **Delivered** means the configured provider accepted the notification. Each account can save 20 services; paused and archived services count, and permanent deletion frees a slot.

## Backup and restore

The daily backup job runs native `pg_dump`, checks the archive, encrypts it locally with [age](https://github.com/FiloSottile/age), then uploads only the encrypted object to S3. It removes the temporary files after the job. Use PostgreSQL tools at least as new as the database server.

1. Create a private S3 bucket with public access blocked. Set a lifecycle policy for the backup prefix (for example, expire backups after 90 days and abort incomplete multipart uploads). Give the EC2 instance role `s3:PutObject` and `s3:AbortMultipartUpload` for that prefix. Use separate recovery credentials with `s3:GetObject` and listing access for recovery.
2. On a separate machine, run `age-keygen -o pulsegrid-backup.identity`. Store the identity securely off-server. Set its **public** recipient as `BACKUP_AGE_RECIPIENT`, and set `BACKUP_S3_PREFIX` and the bucket's `AWS_DEFAULT_REGION`, in `/etc/pulsegrid/pulsegrid.env`. Back up the original PulseGrid environment (in particular `ENCRYPTION_KEY`) and the matching source release securely and separately.
3. Install age and [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) on EC2, available on the system service PATH. From `/opt/pulsegrid`, install the timer and run the first backup:

```sh
sudo install -m 0644 infra/systemd/pulsegrid-offsite-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start pulsegrid-offsite-backup.service
sudo journalctl -u pulsegrid-offsite-backup.service -n 30
sudo systemctl enable --now pulsegrid-offsite-backup.timer
```

On a separate recovery host with a disposable PostgreSQL cluster, configure its `DATABASE_URL` with a role that has `CREATEDB`, and set the original PulseGrid `ENCRYPTION_KEY`. Download and rehearse the exact uploaded object:

```sh
ENV_FILE=/secure/pulsegrid-recovery.env .venv/bin/python scripts/run-env.py \
  bash scripts/offsite-backup.sh rehearse \
  s3://private-bucket/pulsegrid/pulsegrid-TIMESTAMP.dump.age /secure/pulsegrid-backup.identity
```

The rehearsal verifies the download, decrypts and restores it into a new temporary database, checks the schema and representative row counts, decrypts stored secrets, verifies credential invalidation and drops the temporary database. It leaves the selected database, Redis and running services alone. Record the S3 object and rehearsal result; repeat after changes to backup tooling, database versions or keys, and periodically check that fresh objects are arriving.

For an actual recovery, download the chosen S3 object with `aws s3 cp --checksum-mode ENABLED`, decrypt it with `age --decrypt --identity /secure/pulsegrid-backup.identity --output pulsegrid-TIMESTAMP.dump pulsegrid-TIMESTAMP.dump.age`, and generate the local checksum with `sha256sum pulsegrid-TIMESTAMP.dump > pulsegrid-TIMESTAMP.dump.sha256`. Keep those files in a private directory. You can also create a local backup for use before a release:

```sh
ENV_FILE=/etc/pulsegrid/pulsegrid.env bash scripts/backup.sh /secure/backups
# On the selected restore host, with operator access to systemctl and the environment file:
ENV_FILE=/etc/pulsegrid/pulsegrid.env bash scripts/restore.sh /secure/backups/pulsegrid-TIMESTAMP.dump --confirm-replace-database
```

Restore checks the checksum/archive and rejects pre-0005 schema dumps before stopping API/worker through systemd. Restore an old dump with its matching source release before upgrading it. It replaces exactly the database selected by `DATABASE_URL`, revokes restored sessions, deletes recovery codes, fences the current/next TOTP step, clears the selected Redis database, then restarts writers. Failure leaves writers stopped. Review errors before restarting. For manually managed processes, stop every API/worker yourself and add `--writers-stopped`; restart them only after successful restoration. Keep the original encryption key, wait up to 90 seconds for a fresh TOTP and regenerate recovery codes. Restore also rewinds account/password configuration to the backup.

Google account recovery is managed through Google. For an existing password account, approve a one-time conversion with `ENV_FILE=/etc/pulsegrid/pulsegrid.env uv run python scripts/run-env.py .venv/bin/python -m pulsegrid.manage allow-google-link you@example.com`, then use Continue with Google within 15 minutes. The old password is removed only after verified Google sign-in; ownership/history and any existing MFA are preserved. Do not leave the verification-only `ENABLE_LEGACY_PASSWORD_AUTH` override enabled in production. See [the existing EC2 update guide](EC2_UPDATES.md) for the shared-server deployment sequence.

## Local HTTPS staging

`bash scripts/build-web.sh`, then `bash scripts/staging.sh`, serves native staging at `https://localhost:8443`. It uses a dedicated PostgreSQL/Redis profile, API 8002, frontend 3002 and Caddy's local CA. `.env.staging` retains existing signing/encryption keys; the initializer selects the dedicated local URLs. Caddy data stays under `.local-services/staging/`. The local CA is not installed into the host trust store; staging tests explicitly accept its certificate.

`bash scripts/verify-local-release.sh` builds, bootstraps/recovers the staging fixture owner, verifies browser/Secure-cookie behavior, backs up/restores encrypted data, checks credential invalidation and verifies database restart persistence. It modifies only local staging fixture data. It stops staging application processes on exit and retains the staging database. `uv run python scripts/native-services.py down staging` stops its dependencies. This is separate from public certificate issuance and Linux service/reboot validation.

## Releases and rollback

Record the immutable source release identifier, dependency lockfiles, migration revision, validated checks and backup checksum. Back up, stop API/worker, install the release and dependencies, build the frontend, migrate once, then restart all application services and verify externally. Keep previous source/build artifacts available.

For a compatible schema rollback, stop application services, restore the previous source and matching locked dependencies/build output, then restart. A prior application release cannot undo schema changes. For incompatible changes, stop writers and restore the matching verified database dump and encryption key. This loses changes made after the backup. Never delete database storage as a rollback.

## Existing data

The local development and capacity databases were copied to native PostgreSQL during the transition. Development account/monitor/history/channel counts were checked before restarting the app. Backups remain in ignored `backups/pre-native-final/` and `backups/pre-native-capacity/`. Previous service storage was retained for recovery; no original data storage was deleted. Old verification logs are historical evidence and may describe the previous packaging. They do not establish native production-service behavior.

## Schema changes

The current model has 13 application tables at revision `0005`. See [Data model](DATA_MODEL.md) for ownership, configuration history, archive/delete behavior, migration preflight and rollback. Stop API/worker writers and back up before migrating an older installation. Group-scoped destinations block the migration until their coverage is explicitly reviewed.
