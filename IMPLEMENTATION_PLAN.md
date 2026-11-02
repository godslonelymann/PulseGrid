# PulseGrid implementation plan

Status: local implementation and verification completed November 2, 2026; see `docs/MILESTONES.md`, `docs/VERIFICATION.md`, and the remaining external gates in `docs/RELEASE_CHECKLIST.md`. The original implementation sequence is preserved below. `feasibility/` remains an independent experiment; the application lives in `apps/`.

## 1. Product scope and completion criteria

Build a self-hosted monitoring application for one owner, with closed registration. The owner can own HTTP services directly, configure checks, inspect latency and check-based uptime, and receive outage/recovery notifications. Every API query enforces ownership so the data model can support additional accounts later.

The first release includes:

- Owner bootstrap, password login, TOTP enrollment and recovery codes, session management, and logout.
- Monitor creation/editing, archive/restore, explicit permanent deletion, and individual pause/resume.
- HTTP/HTTPS GET and HEAD checks, configurable accepted status codes, intervals, and timeouts.
- UP, SUSPECT, DOWN, UNKNOWN, and PAUSED display states; persistent incidents and recovery events.
- Dashboard summaries, monitor detail pages, latency charts/histograms, check-based uptime, and incident history.
- Configurable SMTP email and webhook destinations, retries, and delivery-failure visibility.
- Native Linux service deployment, HTTPS, persistent data, backups, restore instructions, and independent monitoring.

Initial engineering target: 100 active monitors with intervals of 30 seconds or longer on a measured deployment machine. This is a target to validate, not a promised capacity based on the short local experiment.

First release excludes public sign-up, organization roles, billing, browser journeys, arbitrary request bodies, and multiple monitoring regions. Add SSE after polling works reliably. Private-network monitoring requires an explicit administrator allowlist; public destinations are the default.

## 2. Technology and repository structure

| Area | Choice |
|---|---|
| Frontend | Next.js App Router, TypeScript, Tailwind CSS, Recharts, TanStack Query for polling/cache |
| API | Python 3.13, FastAPI, Pydantic, Uvicorn |
| Worker | Separate Python process, asyncio, HTTPX, bounded concurrency |
| Storage | PostgreSQL, SQLAlchemy async with Psycopg 3, Alembic migrations |
| Temporary security state | Redis and redis-py asyncio |
| Authentication primitives | argon2-cffi, PyOTP, PyJWT; encrypted TOTP secrets using a maintained authenticated-encryption library |
| Tests | pytest, pytest-asyncio, HTTPX, Playwright, real isolated PostgreSQL and Redis |
| Tooling | uv and a committed Python lockfile; pnpm and a committed frontend lockfile; Ruff, TypeScript checks, ESLint |
| Deployment | Native PostgreSQL/Redis, systemd, Caddy, one Linux server initially |

Resolve compatible supported library versions at implementation time, commit lockfiles, and record native service versions. Use one database connection pool per process, with connection validation on checkout and bounded pool sizes; do not nest independent connection pools.

```text
pulse/
  apps/
    web/                       Next.js dashboard
    api/
      pulsegrid/
        api/                   REST endpoints and request validation
        auth/                  Passwords, MFA, sessions, ownership checks
        domain/                Monitoring states and incident rules
        storage/               Models and database access
        worker/                Scheduler, probes, notifications, retention
      migrations/              Alembic migrations
      tests/
  tests/e2e/                   Browser workflows
  infra/
    Caddyfile
    systemd/
  docs/                        Setup, operations, recovery, architecture
  feasibility/                 Preserve experiment and evidence separately
```

Share domain/storage code between the API and worker. Keep HTTP routes and worker execution separate. Authorization is enforced in FastAPI; frontend route guards only improve navigation.

## 3. Access and deployment architecture

```text
Browser -> https://pulse.example.com -> Caddy on server ports 80/443
                                        |-- /api/v1/* -> 127.0.0.1:8000
                                        `-- everything else -> 127.0.0.1:3000

api    -> 127.0.0.1:5432 and 127.0.0.1:6379
worker -> 127.0.0.1:5432 and permitted monitoring destinations
worker -> configured notification providers
```

The server has the public IP. The browser uses that IP or a domain pointing to it. Caddy publishes the public ports. Native application services communicate over loopback or private authenticated database connections. The frontend, API, PostgreSQL and Redis ports stay private.

During development, run PostgreSQL and Redis using `scripts/native-services.py up dev` and the application processes directly on the laptop. Use a Next.js development rewrite for `/api/v1/*` so browser requests stay on one origin. Local access is `http://localhost:3000`; secure-cookie settings differ explicitly between local HTTP and deployed HTTPS.

## 4. Database design

Use UUIDs for domain identities and UTC timestamps. Create migrations for:

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


Important constraints and indexes:

- Ownership follows monitor -> user and is checked on reads and writes.
- Unique `(monitor_id, scheduled_for)` for committed results.
- At most one open incident per monitor.
- Unique `(incident_id, event_kind, destination_id)` for notification intents.
- Index due-monitor lookups, pending outbox work, and results by monitor/time.
- Make deletion behavior explicit: confirm history deletion, stop future checks, and prevent an already running worker from resurrecting a deleted or paused monitor.

Keep incident state, check results, and notification intents authoritative in PostgreSQL. Redis must not be required to reconstruct a monitor's health after restart.

## 5. Authentication and account lifecycle

1. Provide an operator bootstrap command that creates the owner without public registration or default credentials.
2. Hash passwords with Argon2id. Start with the blueprint parameters and benchmark their cost on the actual server. Run expensive verification outside the async event loop and limit concurrent attempts.
3. Implement login, MFA challenge verification, refresh, logout, logout-all, session listing, password change, and an operator-documented account recovery procedure.
4. When MFA is enabled, password verification creates only a short-lived Redis challenge. It does not issue application access.
5. Enroll TOTP through a temporary challenge; require a valid code before activation. Encrypt confirmed secrets. Generate hashed one-time recovery codes; require reauthentication for replacing or disabling MFA.
6. Atomically consume MFA challenges and accepted user/time-step combinations. Apply attempt limits to challenges, accounts, and source addresses. Trust forwarded IP headers only from Caddy.
7. Issue short-lived access JWTs and random refresh tokens in Secure, HttpOnly, host-only cookies. Set deliberate SameSite and path policies. Use CSRF tokens and Origin checks on state-changing browser requests; do not store tokens in browser local storage.
8. Store refresh-token digests. Serialize rotation by session. Reuse revokes the session and is checked during authenticated access so revocation also affects issued access JWTs.
9. Coordinate browser refreshes across tabs. Test simultaneous requests, expired access, lost refresh responses, and reuse. Preserve the strict reuse policy; document that ambiguous refresh failures can require signing in again.
10. Return useful temporary-unavailable responses when security dependencies are unavailable. Do not treat a dependency outage as successful authentication.

Deliverable: a protected `/me` request works only after the required factors, and logout/revocation takes effect across browser tabs.

## 6. Monitor API

Create versioned routes under `/api/v1`:

- `/auth/*`, `/sessions`, and `/me` for account/session lifecycle.
- `/monitors/{id}/versions` for historical configuration.
- `/monitors` and `/monitors/{id}` for configuration.
- Explicit pause/resume and archive/restore actions for monitors.
- `/monitors/{id}/results`, `/metrics`, and `/incidents` for paginated/filterable history.
- `/dashboard` for bounded summary queries.
- `/notification-channels`, channel test actions, and notification-rule configuration.
- Separate liveness, readiness, and worker-status endpoints with appropriate access restrictions.

Specify response schemas, error codes, pagination, validation ranges, and ownership behavior in OpenAPI. For the first release, accept intervals of 30 seconds to 24 hours and timeouts of 1 to 20 seconds, strictly shorter than the interval. Treat these as configurable product bounds.

Monitor edits must invalidate stale in-flight configurations through a version/fencing check. Pauses and archival must be honored by both the scheduler and result-commit path.

## 7. Safe HTTP probe execution

- Accept HTTP/HTTPS GET and HEAD; validate method, URL, port, accepted statuses, and numeric limits.
- Reject embedded URL credentials and sanitize errors/logs. Avoid logging query-string secrets or authorization values.
- Resolve and validate all possible destination addresses. Deny loopback, private, link-local, cloud metadata, internal infrastructure, and other non-public destinations unless an administrator explicitly permits a narrowly scoped private target.
- Connect to a validated address while preserving the original hostname for HTTP Host and TLS verification, or use an egress gateway that enforces the policy at connection time. URL preflight alone does not close the DNS-rebinding gap.
- Disable redirects initially. If added later, revalidate every hop under the same destination policy.
- Apply the same destination rules to user-configured webhooks.
- Verify TLS certificates by default. Record certificate errors as check failures; add expiry reporting as an explicit later feature.
- Bound concurrent requests, connection counts, per-target concurrency, DNS time, and the entire probe duration. HTTPX per-stage timeouts do not replace an overall deadline.
- Stream responses and cap bytes read. Initially measure time to response headers; label this clearly. Do not claim separate DNS/TLS/TTFB timings without implementing and testing instrumentation.

Deliverable: allowed fixtures work, prohibited destinations are blocked, redirects cannot bypass policy, and timeout/body-size tests release resources.

## 8. Scheduling and health state

Run one worker process initially, with independent async loops for scheduling, notification delivery, and retention.

Scheduler behavior:

1. Claim only as many due monitors as the bounded worker pool can reasonably execute.
2. Use PostgreSQL row locking with `SKIP LOCKED`, expiring leases, and an ownership/configuration version.
3. Renew leases while necessary. Queue wait plus total probe duration must fit the lease policy; the prototype's fixed three-second lease is not suitable as the production default.
4. Commit the result only if the worker still owns the lease and the monitor remains eligible.
5. Save the result, update state, open/close an incident, and create any notification intent in one transaction.
6. Advance the schedule from its scheduled timestamp to avoid accumulating completion-time drift. Skip missed intervals after an outage rather than launching a burst; show the resulting coverage gap.
7. Use initial scheduling jitter and never overlap checks for one monitor under normal lease ownership.

Health rules:

```text
Healthy -> first failed check -> SUSPECT
SUSPECT -> successful check  -> UP
SUSPECT -> second failure    -> DOWN + open incident
DOWN    -> two successes     -> UP + close incident
No recent completed check   -> display UNKNOWN
Explicit pause              -> display PAUSED
```

UNKNOWN and PAUSED are display/coverage conditions, not evidence that the target is down. Preserve the last observed health and incident separately. Pausing does not send a recovery notification. Define and test how counters reset after long gaps or configuration changes; do not combine widely separated samples into a supposedly consecutive failure sequence.

## 9. Alerts and incident history

- Create the incident event and pending notification in the same transaction as the triggering result.
- The dispatcher claims pending deliveries with leases, sends outside database transactions, then records the outcome.
- Retry with exponential backoff and jitter; cap attempts and expose exhausted deliveries in the UI with a manual retry action.
- Use stable event IDs for webhook receiver deduplication. Delivery is at least once; do not promise exactly-once email or arbitrary webhook delivery.
- Record outage detection time and recovery detection time. Do not represent them as exact target failure/recovery times between samples.
- Include monitor, URL, detected status, sanitized reason, time, and incident link in each notification.
- Test provider unavailability and ambiguous responses after acceptance. A failed alert must not stop the probe loop.

## 10. Dashboard and metric definitions

Implement screens in this order:

1. Login, MFA prompt, enrollment/recovery, and account/session settings.
2. Flat service list, with pause/resume and configuration forms.
3. Monitor detail with current state, last check age, recent outcomes, incidents, and latency charts.
4. Dashboard summaries and notification settings/delivery history.

Start with five-second polling while the page is visible. Handle loading, empty, paused, stale, unauthorized, and dependency-unavailable states. Show timestamps in the user's timezone while keeping API/database times in UTC. Make forms and charts usable on narrow screens and with a keyboard.

Metric definitions must be visible and consistent:

- **Check-based uptime:** successful completed checks / completed checks over the selected window. Delayed alerting does not remove failures from this calculation.
- **Coverage:** received checks compared with expected checks while monitoring was active. Missing checks are shown separately from target failures.
- **Latency:** median, p95, p99, and histogram for successful timed checks; failed/time-out checks have a separate count. Label time-to-headers rather than full-response latency.

If SSE is added, treat events as update hints and let the client reconcile from database-backed APIs after reconnect. SSE must not become the sole source of history.

## 11. Retention and operations

- Start with configurable 30-day raw-check retention and a bounded batch deletion task. Keep incident/security retention separately configurable.
- Record actual relation/index growth, query latency, and retention cost. Introduce daily partitions or rollup tables when measured scale justifies them.
- The prototype's approximately 1.45 GiB/30 days estimate covers only its sampled check table/indexes at 100 monitors/30 seconds. Budget separately for WAL, backups, errors, and the production schema.
- Expose scheduler lag, last worker heartbeat, in-flight probes, check outcomes, database/Redis availability, outbox backlog, and failed deliveries.
- Use structured logs with request/event identifiers and secret redaction.
- Perform PostgreSQL-aware backups to off-server storage and test restoration. Back up necessary encryption keys securely; a database backup alone cannot recover encrypted secrets without their keys.
- Install an independent external heartbeat check for PulseGrid. A single-server failure otherwise stops monitoring and local notifications together.

## 12. Production packaging and release process

1. Build the standalone frontend and install locked Python dependencies; run the API and worker as separate processes.
2. Use non-root service users, locked dependencies, recorded native service versions and explicit health checks.
3. Provide Linux service files for web, API and worker. Use native PostgreSQL, Redis and Caddy package services. Configure persistent data directories, automatic restart, bounded logs, resource limits and loopback/private listening addresses.
4. Keep production secrets outside source control and build artifacts. Provide `.env.example` with placeholders and document each setting.
5. Run database migrations as a controlled one-shot release step before compatible API/worker processes start. Use backward-compatible changes where possible; do not let every replica run migrations independently.
6. Point a domain to the Linux server's public IP, publish only Caddy's web ports, configure firewall/SSH access, and verify HTTPS and cookie behavior in a real browser.
7. Run CI lint/type checks, API/domain/integration tests, frontend build, browser workflows, and standalone production builds. Record immutable source release identifiers.
8. Verify login, monitor creation, actual check execution, outage/recovery delivery, restart persistence, and restore instructions on staging before production.
9. Document release and rollback commands. Record database compatibility requirements and take a verified backup before destructive migrations; a previous application release cannot automatically undo a schema change.

The `feasibility/run.sh` runner resets its own prototype tables and stops its native dependency profile on exit. It must never be reused as the production startup or deployment script.

## 13. Verification and release gates

Preserve the existing feasibility evidence, and recreate its useful scenarios against production code. Do not count prototype tests as application test coverage.

| Area | Required evidence |
|---|---|
| Domain rules | Short blip, persistent outage, partial recovery, repeated recovery, long gaps, pause/resume, configuration edits |
| Ownership | Another account cannot read, edit, or infer monitors, results, channels, or incidents |
| Auth | Password-only MFA gate, replay races, recovery-code races, refresh reuse, cross-tab refresh, CSRF, logout, expiry, secret handling |
| Probes | Success/error/timeout, TLS failure, rejected destination, mixed DNS answers, rebinding, redirect bypass, oversized/slow body, overall deadline |
| Scheduling | Competing workers, hard process kill, lease expiry/renewal, stale worker commits, missed schedules, deletion during a probe |
| Alerts | Provider failure, backoff, restart between acceptance and acknowledgment, event deduplication, exhausted retries |
| Dependencies | PostgreSQL/Redis stop and restart, first API request after restart, clear unavailable UI, preserved incidents |
| Browser | Sign in, enroll MFA, create monitor, inspect history, pause/resume, receive session revocation, mobile-width layout |
| Operations | Fresh migrations, native production build, server restart, backup restoration, independent heartbeat |

Performance gate: run 100 controlled HTTP targets for at least one hour at 30-second intervals (approximately 12,000 scheduled checks), with a documented mix of fast, slow, failed, and timed-out targets. Capture scheduling lag, actual/expected results, duplicate rows, CPU, process memory, connection counts, database size, and notification backlog. On the declared test machine, require zero duplicate committed schedule results, every absent scheduled result accounted for, no unexplained failures on healthy fixtures, p95 scheduler lag under two seconds, and bounded resource/backlog growth. Report target failures separately from monitor-system failures. Follow with a staging soak and controlled internet/TLS targets before claiming deployment capacity.

## 14. Build order and reviewable milestones

| Milestone | Concrete result | Completion gate |
|---|---|---|
| 1. Foundation | Application layout, local services, models, migrations, CI skeleton | Fresh database upgrades successfully and API health checks work |
| 2. Identity and configuration | Owner login/MFA, direct monitor ownership and API, basic forms | Auth and ownership tests pass; a browser can create a protected monitor |
| 3. Monitoring loop | Safe probes, durable schedules, state transitions, history | A controlled target moves through expected states; competing workers/crash tests pass |
| 4. Incidents and notifications | Transactional incidents, outbox, SMTP/webhook delivery | Outage/recovery notifications survive retries and restarts with documented delivery semantics |
| 5. Dashboard | Charts, metrics, histories, pause/resume, session settings | Browser workflows and metric definitions agree with stored results |
| 6. Production release | Native processes and Linux service files, HTTPS, backups, runbook | Fresh staging deployment and backup restore pass |
| 7. Release validation | Full fault suite, hour-long capacity test, staging soak | Recorded gates pass and limitations are documented |

Keep a working vertical slice after every milestone. The final acceptance demonstration is: sign in with MFA, create a monitor, observe checks, cause an outage, receive a notification, restart a worker, restore the target, receive recovery, and confirm history persists after server restart.

## References

- [Feasibility evidence](feasibility/results/REPORT.md)
- [PostgreSQL native installation](https://www.postgresql.org/download/)
- [Caddy service operation](https://caddyserver.com/docs/running)
- [Next.js self-hosting and reverse proxies](https://nextjs.org/docs/app/guides/self-hosting)
- [OWASP MFA guidance](https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html)
- [OWASP CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [OWASP SSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
