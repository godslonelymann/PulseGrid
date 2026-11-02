# PulseGrid — complete project plan

Last reviewed: November 3, 2026. Native service setup replaces the previous packaging; older verification records remain historical evidence.

This is the central overview of what PulseGrid does, how it works, what has been built, how to run and verify it, and what remains before a public release. It describes the implementation currently in this workspace and the next planned steps. Recorded verification results are historical evidence; they were not rerun just to write this document.

**Current position:** native local services, standalone frontend builds, HTTPS staging and native backup/restore replace the previous packaging. Current checks are recorded in [Verification](docs/VERIFICATION.md). Public Linux service deployment and external operational checks remain pending.

## 1. What we are building and why

PulseGrid is a self-hosted HTTP monitoring application. An owner adds websites or API endpoints, owns them directly, and receives notifications when repeated checks detect an outage or recovery. The dashboard explains current health, recent check results, latency, incident history, and gaps in monitoring coverage.

The product addresses four practical questions:

1. Is this endpoint responding with an accepted HTTP status?
2. How long does it take to return response headers?
3. When did monitoring detect an outage and subsequent recovery?
4. Did monitoring and notification delivery continue working through restarts and dependency failures?

The intended first-release user is a single owner operating a small collection of HTTP services. Registration is closed, and the owner is created from the command line. Ownership checks are enforced throughout the API so resources remain isolated even if additional accounts are introduced later.

The project also provides concrete engineering evidence: durable scheduling, race-safe authentication, safe outbound networking, transactional notification delivery, browser workflows, measured capacity, and recovery procedures.

## 2. Release scope

| Capability | First-release behavior | Current position |
| --- | --- | --- |
| Account access | Operator bootstrap; password login; optional TOTP MFA; recovery codes | Implemented and locally verified |
| Account management | Password changes, MFA enrollment/replacement/disable, session listing and revocation | Implemented and locally verified |
| Organization | Flat directly owned services, pause/resume and archive/restore | Implemented; see current model verification |
| HTTP monitors | Create/edit/delete; GET or HEAD; accepted statuses; interval and timeout | Implemented and locally verified |
| Health | UP, SUSPECT, DOWN, UNKNOWN, PAUSED; durable incidents | Implemented and locally verified |
| Dashboard | Flat service list, detail views, results, charts, histograms, uptime and coverage | Implemented and locally verified |
| Notifications | Email/webhook channels; account-wide coverage; tests; retries; failure history | Implemented; real SMTP provider validation pending |
| Deployment | Native web/API/worker services, systemd, Caddy HTTPS | Local production topology verified; remote deployment pending |
| Recovery | Database dumps, checksums, restore, session invalidation, encrypted-secret recovery | Local restore verified; off-server restore pending |
| Operational monitoring | Readiness and authenticated worker freshness endpoints | Implemented; independent external observer pending |

Monitor configuration permits intervals from **30 seconds to 24 hours**, timeouts from **1 to 20 seconds** strictly shorter than the interval, and accepted status codes from 100 through 599. Defaults are GET, status 200, a 30-second interval, and a 10-second timeout.

The initial engineering baseline is 100 active monitors checked every 30 seconds. This workload passed a controlled local one-hour run. Capacity on the deployment host still needs measurement.

### Outside the current release

Public registration, organization roles, billing, multiple monitoring regions, browser journeys, arbitrary request bodies, SSE updates, certificate-expiry reporting, daily partitions, historical rollups, and contractual SLA reporting are outside the current release. Private targets require an explicit operator allowlist.

These are possible future work, not commitments or prerequisites for completing the present scope.

## 3. How a user uses the application

1. The operator starts the system, migrates the database, and bootstraps the owner.
2. The owner signs in, enrolls an authenticator if desired, and stores recovery codes securely.
3. The owner adds an HTTP(S) monitor directly to their account.
4. The API validates the destination and configuration and stores the monitor and its initial schedule.
5. The worker claims due work, probes the endpoint, and saves the result and current health.
6. The dashboard polls while visible and shows updated health, results, metrics, and incidents.
7. Two consecutive failed checks open an incident. If a notification rule applies, the same database transaction creates the pending alert.
8. The notification loop delivers the alert or retries failures independently of probing.
9. Two consecutive successful checks while DOWN close the incident and create a recovery notification.
10. The owner can pause checks, change configuration, manage sessions, inspect delivery failures, and retry exhausted deliveries.

Pausing preserves history and does not announce a recovery. Permanently deleting a monitor requires confirmation because its monitoring history is permanently removed.

## 4. Architecture and technology choices

```text
Browser
  |
  | HTTPS to the server's public domain/IP
  v
Caddy :80/:443
  |-- /api/* ----------> FastAPI API :8000
  `-- other requests --> Next.js web :3000

API ------------------> PostgreSQL: accounts, configuration, history, incidents
API ------------------> Redis: temporary authentication/security state

Independent worker ---> PostgreSQL: schedules, leases, results, outbox, heartbeat
Independent worker ---> Allowed HTTP(S) monitoring targets
Independent worker ---> Webhook destinations and operator-configured SMTP

Independent external observer ---> Readiness and worker freshness
                                  (installation still pending)
```

Only Caddy accepts public browser traffic. Native services communicate over loopback or private authenticated database connections. PostgreSQL and Redis are private. The browser uses relative API URLs on the same origin.

| Layer | Implementation | Purpose |
| --- | --- | --- |
| Frontend | Next.js App Router, React, TypeScript, Tailwind CSS | Dashboard, account controls, configuration and history views |
| Client data | TanStack Query | Polling, caching, request state |
| Charts | Recharts | Latency lines, distributions and percentiles |
| API | Python 3.13, FastAPI, Pydantic, Uvicorn | Validation, authentication, ownership, queries and mutations |
| Worker | Separate Python asyncio process, HTTPX/httpcore | Probing, durable scheduling, delivery and retention |
| Database | PostgreSQL, SQLAlchemy async, Psycopg 3 | Authoritative durable state and transaction boundaries |
| Migrations | Alembic | Versioned schema changes |
| Security state | Redis | Rate limits and short-lived challenges |
| Authentication | Argon2id, PyOTP, PyJWT, Fernet | Password hashing, MFA, access tokens, encrypted secrets |
| Testing | pytest, pytest-asyncio, HTTPX, Playwright | Domain, integration, fault and browser verification |
| Tooling | uv, pnpm, Ruff, TypeScript, ESLint | Reproducible dependencies and quality checks |
| Deployment | Native processes, systemd and Caddy | Single-host services, HTTPS and persistent data |

The API and worker share domain and storage code. Running probes in a separate process keeps monitoring independent of dashboard requests and API restarts. PostgreSQL transactions keep results, health changes, incidents, and alert intents consistent. Redis is not the source of truth for monitor health.

Application versions are recorded in `uv.lock`, `apps/web/pnpm-lock.yaml` and package manifests. Native dependencies require PostgreSQL 17+, Redis 7+ and Caddy 2. Record the exact installed service versions with each release. Previous packaging logs describe their original runtime only.

## 5. Repository map

| Location | Responsibility |
| --- | --- |
| `apps/web/app/page.tsx` | Dashboard and application interface |
| `apps/web/app/api.ts` | Browser API requests and authentication coordination |
| `apps/web/app/globals.css` | Application styling |
| `apps/api/pulsegrid/api/` | FastAPI routes and Pydantic request/response schemas |
| `apps/api/pulsegrid/auth/` | Passwords, MFA, sessions, security helpers |
| `apps/api/pulsegrid/domain/` | Health transitions and metric/domain rules |
| `apps/api/pulsegrid/storage/` | SQLAlchemy models, connections and database utilities |
| `apps/api/pulsegrid/worker/` | Safe probes, scheduler, notifications, retention, health checks |
| `apps/api/pulsegrid/manage.py` | Owner bootstrap and operator recovery |
| `apps/api/migrations/` | Schema revisions, including durable TOTP replay protection |
| `apps/api/tests/` | Backend domain, authentication, monitoring, probe and fault tests |
| `tests/e2e/` | Full application browser workflows |
| `tests/staging/` | HTTPS and cookie/security browser verification |
| `tests/capacity/` | One-hour controlled capacity runner and resource analysis |
| `infra/` | Caddy configurations and native Linux service files |
| `scripts/` | Environment initialization, development, verification, backups and restoration |
| `.github/workflows/ci.yaml` | Automated quality/build/test workflow definition |
| `docs/` | Detailed architecture, operations, evidence and release checklists |
| `verification/` | Saved logs, screenshots, manifests and measurement reports |
| `feasibility/` | Original independent prototype and experimental evidence |
| `IMPLEMENTATION_PLAN.md` | Original detailed implementation sequence |

Prototype tests are separate from application coverage. The feasibility runner resets its own prototype tables and must not be used as the application's deployment procedure.

## 6. Data model and consistency rules

Domain identities use UUIDs; database/API timestamps use UTC. The UI displays times in the user's local timezone.

| Table | Stored responsibility |
| --- | --- |
| `users` | Account identity, Google login, password hash, encrypted MFA and replay protection |
| `sessions` | Session lifetime, revocation and browser label |
| `refresh_tokens` | Hashed token rotation/replacement history |
| `recovery_codes` | Single-use hashed MFA recovery codes |
| `monitors` | Direct owner, current settings, pause/archive state and configuration counter |
| `monitor_runtime` | Current health, schedule and worker lease |
| `monitor_versions` | Historical settings and active/paused/archive periods |
| `check_results` | Measurements linked to the configuration used |
| `incidents` | Outage/recovery times and supporting check references |
| `notification_channels` | Owner, encrypted destination and enablement |
| `notification_outbox` | Durable alerts, retries and delivery outcomes |
| `audit_events` | Actor, action, resource type/ID and time |
| `worker_heartbeats` | Worker freshness and in-flight work |

The model has 13 application tables; see [Data model](docs/DATA_MODEL.md) for migration and archive semantics.

Critical guarantees:

- Each monitor and channel is accessed through its owner. Foreign and unknown resource IDs both return 404.
- A unique `(monitor_id, scheduled_for)` prevents duplicate committed checks for a scheduled slot.
- A partial unique index permits at most one open incident per monitor.
- A unique `(incident_id, event_kind, channel_id)` prevents duplicate incident notification intents.
- A result, health transition, incident update and applicable outbox entries commit together.
- Edits, pauses and deletes invalidate stale work so an earlier probe cannot overwrite current configuration or recreate deleted data.
- Cascading deletion is deliberate: explicitly purging a monitor removes its history; archiving retains it; deleting a channel removes its delivery history.

## 7. Monitoring execution and health rules

### Scheduler

The worker claims due monitors using PostgreSQL row locks with `FOR UPDATE SKIP LOCKED`. It claims only work that fits available capacity. Defaults are 20 global probe slots and two simultaneous probes per hostname/port.

Each claim has a 60-second lease, renewed every 20 seconds, including time waiting for a target slot. Committing requires a matching live lease and unchanged configuration. If a worker dies, another worker can reclaim work after expiry. Database connections and queries have bounded pool sizes and timeouts.

Schedules advance from their scheduled timestamps. After an interruption, missed intervals are skipped rather than launched all at once. The resulting monitoring gap is reflected in coverage.

### Health transitions

| Observation | Result |
| --- | --- |
| Successful check when no DOWN incident needs recovery | UP |
| First consecutive failure | SUSPECT |
| Second consecutive failure | DOWN; open incident and create outage alert intent |
| First success while DOWN | Keep incident open |
| Second consecutive success while DOWN | UP; close incident and create recovery alert intent |
| No sufficiently recent result | Display UNKNOWN |
| Monitor paused or archived | Display PAUSED |

A gap beyond `2 × interval + timeout` resets consecutive-sample counters. An open incident survives gaps and pauses until two fresh successful checks establish recovery. UNKNOWN and PAUSED describe observation conditions; they are not proof of target failure or recovery.

Incident timestamps represent when checks detected a transition. They cannot establish the exact instant the target failed or recovered between samples.

### Safe probing

- Accept HTTP/HTTPS GET and HEAD only; reject embedded credentials, fragments and prohibited destinations.
- Validate all resolved addresses and connect to a validated numeric address while retaining the original Host header and TLS hostname.
- Deny private, loopback, link-local, metadata and other restricted infrastructure targets by default. An operator may allow exact trusted hostname:port entries.
- Disable redirects and environment proxies. Verify TLS certificates.
- Bound DNS resolution and the entire probe with deadlines.
- Return after response headers and read zero response body bytes.
- Apply the same destination protections to webhooks.

The transport currently selects the first validated DNS address and has no multi-address failover. It depends on pinned HTTPX/httpcore internals; dependency changes require rerunning transport and destination-security tests.

## 8. Authentication and account security

Passwords use Argon2id with 64 MiB memory, three iterations and two lanes. Expensive verification runs outside the event loop with bounded concurrency. There are no default owner credentials.

When MFA is enabled, password verification issues only a short-lived challenge. A valid authenticator or recovery code is required before application access. Enrollment must be confirmed. TOTP secrets are encrypted, recovery codes are stored as digests, and code consumption is atomic. PostgreSQL also stores a TOTP replay fence so losing Redis does not make an accepted code reusable.

Access JWTs expire after five minutes by default; absolute session expiry is 14 days. Every authenticated request checks the live database session. Refresh tokens are random values stored by digest and rotated under a session lock. Reuse revokes the session and its issued access tokens. Browser tabs coordinate refreshes through Web Locks.

Production cookies are host-only, Secure and SameSite=Strict; authentication cookies are HttpOnly. Mutations require both the CSRF token and the exact configured Origin. Credentials are not stored in browser local storage. Sensitive configuration is encrypted and logs omit secret values.

Strict refresh-token reuse handling can require a fresh sign-in after a lost successful refresh response. Browsers without Web Locks lack the cross-tab coordination guarantee. Clock synchronization is required for TOTP and scheduling.

The default API command ignores forwarded headers. Behind Caddy, source limits therefore share the proxy address; account/challenge limits remain separate. Any change to trusted proxy handling requires an explicitly controlled proxy network.

## 9. Notifications and failure handling

The owner creates encrypted account-wide email/webhook destinations. A separate worker loop claims pending outbox rows, sends outside the database transaction, and records outcomes under lease fencing.

Deliveries use a 20-second overall deadline, a 60-second delivery lease, exponential backoff with jitter, and up to eight attempts. Exhausted deliveries remain visible and can be manually retried. Disabled channels retain pending work until reenabled. Provider failure does not stop probes.

Delivery is **at least once**: a provider may accept a message immediately before the worker crashes, causing a retry and a duplicate. Webhooks include stable event IDs and an Idempotency-Key. SMTP uses a stable Message-ID, but receivers are not guaranteed to deduplicate it.

Local webhook and SMTP fixtures have been tested. Actual email delivery through the chosen provider is a remaining release gate.

## 10. Dashboard and metric definitions

The dashboard polls every five seconds while visible. It provides account/security controls, service configuration and archiving, monitor details, incident history, notification settings, and delivery outcomes. It handles loading, empty, unauthorized, paused, stale and dependency-unavailable states and has recorded desktop/mobile browser coverage.

| Metric | Definition |
| --- | --- |
| Check-based uptime | Successful completed checks divided by completed checks in the selected retained window |
| Expected checks | Floor of the summed active duration divided by each historical interval |
| Coverage | Completed divided by expected checks, capped at 100% for boundaries/jitter |
| Missing checks | Expected checks not represented by completed checks |
| Latency | Time to response headers; median/p95/p99 and histogram use successful timed checks |
| Bucket check success | Check-count-weighted success over the last 24 hours |
| Bucket health | Worst eligible member status, with unknown and paused counts |

Every failed completed check affects uptime, including failures before DOWN is declared. Missing checks remain separate from endpoint failures. These metrics do not establish contractual SLA compliance.

The latency line uses the current results page; percentiles and histogram use the full selected retained window. Windows older than raw retention are clipped with a visible notice. Purged raw history cannot be reconstructed because rollups are not implemented.

## 11. Development setup

Run commands from the project root. Required tools are Python 3.13, uv, Node 24, pnpm 10.32.1, and PostgreSQL 17+ tools and Redis 7+ (Caddy 2 for HTTPS staging).

```sh
uv sync --frozen
uv run python scripts/init-env.py
uv run python scripts/native-services.py up dev
uv run alembic -c apps/api/alembic.ini upgrade head
PYTHONPATH=apps/api uv run python -m pulsegrid.manage bootstrap you@example.com
pnpm --dir apps/web install --frozen-lockfile
bash scripts/dev.sh
```

Open `http://localhost:3000`. Bootstrap prompts for a password and refuses a second bootstrap once an account exists. Local API documentation is available at `http://127.0.0.1:8000/api/docs` while the API is running.

The development script runs the API, worker and frontend. Ctrl-C stops application processes while retaining database data. Stop dependencies with `uv run python scripts/native-services.py down dev`. Deleting `.local-services/dev` removes data and is not part of normal startup or shutdown.

### Configuration groups

| Group | Settings |
| --- | --- |
| Database/security services | `DATABASE_URL`, `REDIS_URL` |
| Signing/encryption | `JWT_SECRET`, `ENCRYPTION_KEY` |
| Browser/deployment | `ENVIRONMENT`, `PUBLIC_ORIGIN`, `SECURE_COOKIES`, `CADDY_ADDRESS`, `CADDY_EMAIL` |
| Authentication lifetime | `ACCESS_SECONDS`, `SESSION_DAYS` |
| Worker/networking | `WORKER_CONCURRENCY`, `LEASE_SECONDS`, `PRIVATE_TARGET_ALLOWLIST` |
| Retention | `RAW_RETENTION_DAYS`, `SECURITY_RETENTION_DAYS`, `INCIDENT_RETENTION_DAYS` |
| Email provider | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS` |

Use `.env.example` for placeholders and `scripts/init-env.py` to generate local secrets. Existing environment files are preserved. Environment variables override `.env`. Production requires HTTPS and Secure cookies. Preserve the encryption key across releases and restores; changing it without re-encrypting data makes existing encrypted secrets unreadable.

## 12. Verification strategy and saved results

Backend tests use real isolated PostgreSQL and Redis. They cover domain transitions, ownership, authentication races, CSRF, replay protection, destination validation, scheduler leases, stale commits, transactional alerts, provider failures, retention, and dependency stop/restart. Browser tests exercise actual account/configuration and outage/recovery workflows.

The fault suite stops only its dedicated native verification processes. Browser tests reset the dedicated test database and must run after pytest, not concurrently. Capacity testing uses a separate database and output directory.

```sh
bash scripts/test.sh
uv run ruff check apps/api tests/capacity
uv run ruff format --check apps/api tests/capacity
pnpm --dir apps/web install --frozen-lockfile
pnpm --dir apps/web typecheck
pnpm --dir apps/web lint
pnpm --dir apps/web build
pnpm --dir apps/web exec playwright install chromium
bash scripts/e2e.sh
```

See [Verification](docs/VERIFICATION.md) for the capacity command and reproduction details. Do not overwrite saved measurements when rerunning capacity tests.

| Recorded verification | Saved outcome | Evidence |
| --- | --- | --- |
| Backend suite | 56 tests passed | `verification/backend-tests.log` |
| Application browser workflows | 2 Chromium scenarios passed | `verification/browser-results.json` |
| Local HTTPS staging browser | 1 scenario passed | `verification/staging-browser-results.json` |
| Frontend checks/build and Python quality | Passed | Frontend/Python logs under `verification/` |
| Migrations and model drift | Fresh upgrades and drift check passed | Migration logs under `verification/` |
| Previous packaging (historical) | Archived build results; replaced by native services | Original logs retained under `verification/` |
| Local restore/restart | History and encrypted secrets recovered; restored sessions/recovery codes invalidated | `verification/final-release.log`, `verification/backup-restore.log` |
| Local staging observation | 204 readiness samples over about 33.9 minutes; zero failures; p95 45.6 ms | `verification/staging-soak-report.json` |
| Capacity | 12,000/12,000 checks; zero missing/duplicate schedules; no unexplained healthy failures | `verification/capacity/report.json` |

The one-hour capacity workload used 100 distinct local HTTP fixture ports: 70 fast successes, 20 delayed successes, five HTTP 503 targets and five timeouts. It recorded p95 scheduling lag of 211.786 ms, peak worker RSS of 109.94 MiB, a maximum of four PostgreSQL connections and database growth of about 3.10 MiB excluding WAL/backups. The capacity run had no notification rules, so it does not measure alert throughput under load.

That baseline used macOS arm64 with 10 logical CPUs and 16 GiB RAM, native Python 3.13.15/PostgreSQL 17.6, and schema revision 0001. Later authentication/security/UI changes were verified separately. Repeat capacity on the actual host with the final native release before asserting deployment capacity.

A CI workflow exists, but no remote CI run is claimed. This workspace was not a Git checkout when inspected for this document.

## 13. Delivery milestones and present status

| Milestone | Concrete result | Status |
| --- | --- | --- |
| 1. Foundation | Application layout, configuration, shared models, migrations, isolated services and health | Complete locally |
| 2. Identity/configuration | Password/MFA/session lifecycle, ownership, monitors and forms | Complete locally |
| 3. Monitoring | Safe probes, durable scheduling, leases, fencing and state/history | Complete locally |
| 4. Incidents/alerts | Transactional transitions, outbox, retries and failure visibility | Complete locally; external provider validation pending |
| 5. Dashboard | Charts, metric definitions, histories, settings and browser workflows | Complete locally |
| 6. Packaging/operations | Native Linux services, Caddy, backup/restore and rollback procedures | Complete locally; remote operations pending |
| 7. Validation | Fault suite, one-hour baseline, local HTTPS staging and recorded limitations | Complete locally; deployment-host validation pending |

The checked implementation milestones do not close the external release gates. The detailed gate record remains [Release checklist](docs/RELEASE_CHECKLIST.md).

## 14. Next execution plan

The following work is ordered by dependency. Completion is based on evidence rather than a speculative calendar deadline.

| Order | Action | Input/dependency | Acceptance evidence |
| --- | --- | --- | --- |
| 1 | Select and prepare Linux staging host | Server access, domain/DNS, operator | Restricted SSH; intended public ports only; time sync; deployment configuration recorded |
| 2 | Establish release source and builds | Source/lockfiles, Git remote or release archive | Immutable source release identifier, build records, migration revision, remote CI results if configured |
| 3 | Deploy isolated remote staging | Production secrets and HTTPS origin | Migrations succeed; services ready; external browser trusts certificate |
| 4 | Configure real alert providers | SMTP account and controlled webhook receiver | Test, outage and recovery alerts received; failure and retry behavior inspected |
| 5 | Configure off-server backups and key custody | Backup destination, access policy, encryption/key storage | Verified dump copied off-host and restored on an isolated target with matching keys |
| 6 | Install independent monitoring | Separate observer/service and alert destination | Readiness and worker-staleness alerts trigger when components are deliberately stopped |
| 7 | Run release-host capacity and soak | Final native release, controlled targets, separate evidence location | One-hour results reconciled; p95 lag below two seconds; no duplicate schedules or unexplained healthy failures; bounded resources/backlog |
| 8 | Verify host reboot and recovery | Persistent database directories and approved test window | Services return after reboot; owner access, history, fresh checks and alert delivery work |
| 9 | Review release and deploy production | All external gates passed, verified backup and rollback record | Final acceptance demonstration passes; evidence linked and checklist updated |

Host sizing, provider/storage costs, backup frequency, recovery point objective, recovery time objective and remote soak duration remain operator decisions. They must be selected for the actual deployment and recorded before treating operations as complete. There is no approved budget or fixed release date in the current project files.

## 15. Deployment, operations and recovery plan

Production uses six long-running services: Caddy, web, API, worker, PostgreSQL and Redis. Build the standalone frontend and install locked Python dependencies. API and worker share the Python environment with different commands. Use immutable source releases, non-root application users, loopback/private database addresses, health checks, persistent data and bounded logs. Linux service files are provided in `infra/systemd/`.

The release sequence is: prepare configuration and native builds, start database/security services, run a single migration step, bootstrap the owner on first installation, start the application, then verify externally. Subsequent releases require a verified backup and schema compatibility review before migration. Exact commands are in [Operations](docs/OPERATIONS.md).

Operational endpoints:

- `/api/v1/health/live`: process liveness.
- `/api/v1/health/ready`: API/dependency readiness.
- `/api/v1/worker-status`: authenticated worker freshness and in-flight status.

The external observer should alert on a missing worker heartbeat for 15 seconds, readiness failures, growing scheduling lag, unknown monitors, exhausted deliveries, outbox growth and storage pressure. A reachable dashboard alone does not prove checks are running.

Raw checks retain 30 days by default; security events retain 90 days and closed incidents 365 days. Open incidents are retained. Deletion runs in bounded batches, with raw checks removed in batches of 1,000 each minute. Measure sustained ingestion, retention throughput, indexes, WAL and backup storage before increasing scale. Short local storage measurements do not establish a 30-day storage budget.

Backups use PostgreSQL custom-format dumps, restore-list checks and SHA-256 checksums. The scripts do not provide an off-server encrypted backup service automatically; encrypt and transfer dumps through the chosen operational system and secure encryption keys separately.

Restoration explicitly replaces the selected database. It revokes restored sessions, invalidates restored recovery codes, clears temporary Redis challenges and advances the TOTP replay fence. Wait up to 90 seconds for a fresh authenticator code and generate new recovery codes afterward. Verify encrypted secrets, history and fresh checks after recovery.

Application rollback is straightforward only when the previous application release supports the current schema. Incompatible database changes require the matching backup, encryption key and application version, with loss of writes after the backup. Deleting database files is not a rollback procedure.

## 16. Risks, constraints and planned responses

| Risk or constraint | Current response / next action |
| --- | --- |
| Single-server outage stops checks and local delivery | Install an independent external observer; evaluate multi-host design only if required later |
| API readiness can pass while worker is stale | Monitor readiness and authenticated worker freshness separately |
| Local capacity differs from deployment behavior | Repeat the final native release workload and soak on the target host |
| Notification acceptance followed by crash can duplicate delivery | Document at-least-once semantics; provide stable event identifiers |
| Hostile monitor/webhook destinations can reach infrastructure | Validate DNS and actual connections; keep private allowlist narrow and controlled |
| Lost encryption key makes encrypted data unrecoverable | Preserve and test matching key custody independently of database dumps |
| Backup rollback can restore old credential state | Invalidate restored one-time/session credentials and review account state |
| Raw retention removes historical metric inputs | Clip visible windows; introduce rollups only when long-term history is in scope |
| Retention or database growth can outpace maintenance at larger scale | Measure full production ingestion/WAL/retention; consider partitions after evidence |
| Transport dependencies use internal HTTPX/httpcore APIs | Keep pins and rerun security/TLS tests on updates |
| Cross-tab refresh depends on Web Locks | Test supported browsers and document the limitation |
| External infrastructure has not been supplied or validated | Keep release gates open until real evidence is recorded |

## 17. Completion criteria

Local implementation is supported by the existing saved evidence. A public release is complete only when the external gates in this plan and the release checklist have passed on the actual deployment.

The final acceptance demonstration is:

1. Sign in with MFA and create a monitor and notification destination.
2. Observe successful checks and consistent dashboard metrics.
3. Cause a controlled outage and receive the outage notification.
4. Kill/restart the worker while the incident is open; verify recovery of monitoring without duplicate committed schedules.
5. Restore the target and receive the recovery notification after two successful checks.
6. Reboot the host and verify history, sign-in, fresh checks and delivery persist.
7. Restore an off-server backup in isolation and verify decryption and credential invalidation.
8. Stop the worker/system deliberately and verify independent alerts reach the operator.

## 18. Keeping this plan accurate

Update this file when scope, architecture, guarantees, milestone status or deployment decisions change. Record measured results with their machine, workload, software revision and evidence path. Keep planned features clearly labeled and external gates open until their acceptance evidence exists. Never put actual passwords, tokens, recovery codes, private keys or provider credentials in project documentation.

Related documents:

- [README](README.md): quick start and standard verification commands.
- [Original implementation plan](IMPLEMENTATION_PLAN.md): detailed original build sequence.
- [Milestones](docs/MILESTONES.md): implementation completion checklist.
- [Architecture](docs/ARCHITECTURE.md): exact security, scheduling, delivery and metric guarantees.
- [Operations](docs/OPERATIONS.md): configuration, deployment, backup, restore and rollback commands.
- [Verification](docs/VERIFICATION.md): reproduction procedures, evidence and limits.
- [Release checklist](docs/RELEASE_CHECKLIST.md): remaining external release gates.
- [Feasibility report](feasibility/results/REPORT.md): separate prototype evidence.
