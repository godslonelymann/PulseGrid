# Application verification

This report covers `apps/api/pulsegrid` and `apps/web`, not feasibility code. Captured logs and measurements live in `verification/`. Intermediate failures were fixed and rerun; only final outcomes are listed below once complete.

## Reproduction

Install dependencies and generate local secrets as in the README. Run `bash scripts/test.sh` to start native dependencies, apply migrations to the isolated test URL and execute pytest. `uv run pytest -q` tests real PostgreSQL and Redis and includes destructive stop/restart and hard-process-kill scenarios. It does not use SQLite or mock database transactions. The real lease-expiry test takes about a minute. Never point tests at production: the test fixture deliberately pins its database/Redis URLs and truncates test tables.

Run `bash scripts/e2e.sh` after pytest. It seeds a test owner, starts a separate API/worker/frontend/HTTP fixture, and launches Chromium at port 3001. Its test credentials apply only to the reset verification database. It uses eight-second access tokens to exercise refresh coordination, and real 30-second monitor intervals for incident detection/recovery. Screenshots, webhook events, JSON results and process logs are saved in `verification/`.

Run lint/build checks and the native standalone build with the commands in README and `.github/workflows/ci.yaml`. The workflow is provided but has not been executed by a remote CI service in this workspace (the directory is not a Git checkout).

## Capacity test

Fresh isolated capacity database and empty output directory required; preserve previous evidence before re-running. For another run, create a separately named database and set `CAPACITY_OUTPUT_DIR` to a fresh directory. The runner refuses to overwrite existing measurements. This command does not delete a populated capacity database.

```sh
uv run python scripts/native-services.py up capacity postgres
DATABASE_URL=postgresql+psycopg://pulsegrid:capacity-only@localhost:55434/pulsegrid_capacity uv run alembic -c apps/api/alembic.ini upgrade head
PYTHONPATH=apps/api \
DATABASE_URL=postgresql+psycopg://pulsegrid:capacity-only@localhost:55434/pulsegrid_capacity \
PRIVATE_TARGET_ALLOWLIST="$(uv run python -c 'print(",".join(f"127.0.0.1:{18100+i}" for i in range(100)))')" \
uv run python tests/capacity/run.py
```

The production worker runs as a separate process with 100 distinct controlled HTTP fixture ports, every 30 seconds, for 3,600 seconds plus drain time. Mix: 70 immediate 200s, 20 250ms 200s, five 503s, five two-second fixtures with a one-second timeout. Checks are spread over the 30-second schedule. Private destination permission is fixture-only. The test records per-result scheduler lag, expected/actual results, duplicates, fixture-specific failures, worker CPU/RSS, PostgreSQL connections/size and notification backlog. JSONL samples are written every five seconds. Report: `verification/capacity/report.json`. Staging/internet capacity is not inferred from local HTTP fixtures.

## Local staging

`bash scripts/staging.sh` starts the native API/worker/standalone frontend and Caddy HTTPS at loopback 8443, with isolated native PostgreSQL/Redis and Secure cookies. Build first with `bash scripts/build-web.sh`. `bash scripts/verify-local-release.sh` exercises the browser and backup/restore/restart flow. Staging secrets are in ignored `.env.staging`. The Caddy local CA is not installed in the host trust store; staging tests explicitly accept that local certificate. This does not validate public certificate issuance.

## Minimal model verification — November 3, 2026

Revision `0005` has 13 application tables. The development, staging and capacity databases were migrated. A final development backup and a field-by-field preservation baseline were saved before cutover. Verification confirmed all existing records were preserved: two accounts, three sessions, 21 refresh tokens, two monitors, 614 checks, one destination, two delivery records and 13 audit records. Ownership, individual pauses and current settings were converted deliberately. Check rows now reference history periods; unknown legacy URLs remain NULL.

| Check | Result | Evidence |
| --- | --- | --- |
| Backend suite | 99 passed, including migration rehearsals, ownership, concurrency, constraints, archive/restore, history links and retention | `verification/model-backend-tests.log` |
| Final security/migration regressions | 24 passed, including individual refresh-token expiry and rejecting older-schema backups before database replacement | `verification/model-final-security-tests.log` |
| Existing application browser scenarios | 8 passed, including MFA, outage/recovery, hard worker restart, account-wide alerts and narrow layouts | `verification/model-browser.log` |
| New archive/history browser flow | 1 passed; original check settings, archive filtering, retained history and restore verified | `verification/model-archive-browser.log` |
| HTTPS staging and backup/restore | Passed Secure-cookie checks, encrypted-secret recovery, session/recovery-code invalidation and native database restart persistence | `verification/model-release.log` |
| Frontend | TypeScript, ESLint and standalone production build passed | `verification/model-frontend-checks.log`, `verification/model-frontend-build.log` |
| Schema | Fresh upgrades and populated legacy fixture upgrade passed; no ORM/migration drift on fresh or development databases | Migration tests and local `alembic check` |

The new schema removes group APIs and group-scoped routing. Selective destinations stop migration before schema changes; coverage is never broadened automatically. Rollback uses the verified pre-migration dump and matching source archive under `backups/pre-model-change/`. See [Data model](DATA_MODEL.md).

The one-hour capacity baseline below predates this schema. Production Linux service/reboot behavior, public certificates, real provider delivery, off-server recovery and deployment-host capacity remain separate external gates.

## Native transition verification — November 3, 2026

The existing development and capacity databases were copied to native PostgreSQL. Development counts matched before application restart: two users, two monitors, 470 check results and one encrypted notification channel. Existing encrypted channel/account secrets decrypt with the preserved keys. Original storage and migration backups were retained.

| Check | Native result | Evidence |
| --- | --- | --- |
| Existing backend suite | 87 passed, including native dependency outages/recovery | `verification/native-backend-tests.log` |
| Backup corruption guard | 1 passed; damaged backup rejected, account/session unchanged | `verification/native-backup-guard.log` |
| Application browser workflows | 8 passed, including MFA, outage/recovery and hard worker restart | `verification/native-browser.log` |
| HTTPS staging and restore | Browser/Secure cookies, encrypted secrets, credential invalidation and native restart persistence passed | `verification/native-release.log` |
| Frontend | TypeScript, ESLint and standalone production build passed | `verification/native-frontend-checks.log`, `verification/native-release.log` |
| Python/shell/configuration | Ruff lint/format, shell syntax and Caddy configuration validation passed | Local checks; CI repeats lint/build/test commands |
| Prototype feasibility | 15/15 passed with native dependency faults and child workers; separate from application coverage | `verification/native-feasibility/results.json` |

Native service versions on this machine were PostgreSQL 18.6 and Redis 8.10.3. The Linux unit files were inspected/parsed on macOS; actual systemd startup, host reboot, public certificates and production-host capacity still require a Linux deployment. The historical one-hour capacity run below was not repeated for this transition.

## Historical results before the native transition

These archived results describe the previous runtime. Current native checks are recorded separately below.

| Verification | Result | Evidence |
| --- | --- | --- |
| Python domain/API/integration/fault suite | 56 passed in 77.56s | `verification/backend-tests.log` |
| Chromium application scenarios | 2 passed; full account/configuration and outage/recovery workflows | `verification/browser-results.json`, desktop/mobile screenshots |
| Frontend | TypeScript, ESLint and production build passed | `verification/frontend-checks.log`, `frontend-build.log` |
| Python quality | Ruff lint and formatting passed | `verification/python-lint.log` |
| Schema | Fresh upgrades and no model drift | `verification/staging-migration.log`, `migration-drift.log` |
| Previous packaging | Archived API/worker/frontend builds passed; runtime packaging has since been replaced | `verification/api-image.log`, `web-image.log`, `runtime-images.txt` |
| Local HTTPS staging | Browser login, Secure/HttpOnly cookies, CSRF rejection and public TLS monitor passed | `verification/final-release.log`, `staging-browser-results.json` |
| Backup/restore/restart | History survives; post-backup marker removed; restored sessions/recovery codes invalidated; encrypted TOTP and channel destinations decrypt | `verification/final-release.log`, `backup-restore.log` |
| Local staging observation | 204 readiness requests over 33.9 minutes; zero failures; p95 45.6ms | `verification/staging-soak-report.json` |
| One-hour capacity | **12,000 / 12,000**, zero missing/duplicate results and no unexplained healthy failures | `verification/capacity/report.json` |

The backend suite covers blips/outages/partial recovery/gaps; ownership; MFA/challenge/recovery and refresh races; credential changes; rate limits; CSRF; Redis-loss replay protection; unsafe/mixed/rebinding destinations, redirect/body/deadline/TLS failures; competing claims, lease renewal including queue wait, natural expiry after SIGKILL and stale/deleted/paused/edited result rejection; transactional incidents/outbox; SMTP/webhook failure, exponential retries, exhaustion and ambiguous acceptance; PostgreSQL/Redis stop/restart and the first API request after restart; metric coverage and retention clipping.

The browser acceptance workflow signs in with MFA, creates a protected monitor and notification rule, observes a real outage notification, kills/restarts the real worker while the incident is open, pauses/resumes, restores the target, observes recovery and incident history, inspects charts at desktop/mobile widths, and verifies refresh coordination plus revocation in another tab. Additional browser steps cover bucket/monitor/channel editing/deletion, operator recovery, MFA replacement/disable, password changes and a visible dependency-unavailable state.

Capacity machine: macOS 26.7.1 arm64, 10 logical CPUs, 16 GiB RAM. p95 scheduler lag **211.786ms**, maximum 592.646ms; worker peak RSS **109.94MiB**, final 67.08MiB; mean worker CPU 8.53% of one core; maximum four PostgreSQL connections; database growth 3.10MiB, excluding WAL/backups. First steady ten-minute mean RSS was 72.84MiB versus 67.87MiB in the last ten minutes. No outbox growth occurred (no capacity-run notification rules). Each of 600 HTTP failures and 600 timeouts was expected; all 10,800 healthy/slow checks succeeded. See `verification/capacity/NOTES.md` for the exact workload and version boundary: this is a local baseline, not a production-host capacity certification.

Supplementary production-schema measurements: 100 per-monitor aggregate queries had median 0.375ms and p95 1.290ms; a simulated 1,000-row deletion batch took 7.923ms and was rolled back, preserving all check rows. Relation/index sizes and the full plan are in `verification/capacity/storage-and-query.json`. These are current-data measurements, not a 30-day storage or retention soak.

The previous packaging recorded Python 3.13.18, Node 24.22.2, PostgreSQL 17.11, Redis 7.4.12 and Caddy 2.11.7; these are historical versions. Local capacity used native Python 3.13.15/PostgreSQL 17.6 and the monitoring schema from revision 0001. The authentication-only revision 0002 and later API/UI/security-denial additions were verified separately. Repeat the hour test on the actual release host with its final native release before promising deployment capacity.

## Remaining external release gates

No public Linux server/domain, off-server backup location, independent observer, or real SMTP provider credentials were supplied. Public ACME/browser trust, remote server reboot, delivery through a real mail provider, installation of an external heartbeat and a remote staging soak therefore remain unverified. Local service restarts, local TLS fixtures, local SMTP/provider fixtures, browser flows and local backup restoration are separate evidence, and do not imply these external gates passed.

The app polls while visible; SSE, certificate-expiry reporting, distributed regions, public registration, monitor moves between buckets, daily partitions and SLA guarantees are outside this release. Redis state is ephemeral; challenges/rate counters are lost on restart, but a database TOTP fence and recovery-code digests preserve single-use protection. A clock must be synchronized for TOTP and UTC scheduling. Metric windows are clipped to the raw-retention boundary, with a visible notice, so purged history is excluded from coverage. Raw-result metrics older than configured retention cannot be reconstructed; there are no rollups yet. Browser cross-tab refresh coordination requires Web Locks. SMTP/webhook delivery is at least once, not exactly once.
