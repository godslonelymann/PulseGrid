# PulseGrid feasibility experiment

Run started (UTC): 2026-11-02T13:50:16.900917+00:00

**Scope:** The original project directory was empty. These results test a newly built reference prototype, not an existing or production-ready PulseGrid.

Outcome: 15/15 experiments passed.

| Experiment | Result | Time (s) |
|---|---|---|
| HTTP success, latency, timeout, failure and redirect | PASS | 0.813 |
| Brief failure, outage, recovery and raw uptime | PASS | 0.125 |
| Alert retries and receiver deduplication | PASS | 0.019 |
| Incident history after engine recreation | PASS | 0.013 |
| Two concurrent workers without duplicate checks | PASS | 0.054 |
| Expired lease ownership fencing | PASS | 0.016 |
| Hard worker crash and replacement process | PASS | 3.459 |
| Stale results display UNKNOWN | PASS | 0.006 |
| Redis outage and fail-closed authentication | PASS | 2.037 |
| PostgreSQL outage and restart | PASS | 5.116 |
| Password, MFA, replay, token rotation and revocation | PASS | 0.212 |
| Simultaneous refresh-token reuse | PASS | 0.012 |
| Forbidden URL destination cases | PASS | 0.002 |
| 10 and 100 monitors with real 30-second intervals | PASS | 61.534 |
| 100,000-row storage sample | PASS | 0.653 |

## Measured capacity

Load targets are local HTTP fixtures: 80% immediate HTTP 200 and 20% HTTP 200 after a 250 ms delay. Real TLS, internet routing and DNS load are excluded. A batch starts after all monitors are due; this is fixed-delay scheduling, not a strict 30-second cadence.

- 10 monitors: 10 checks across 1 rounds; batch times [0.267] seconds; HTTP failures 0; sampled combined runner/fixture RSS [180.1] MiB. CPU 0.018 process-seconds over 0.269 seconds.
- 100 monitors: 300 checks across 3 rounds; batch times [0.32, 0.358, 0.363] seconds; HTTP failures 0; sampled combined runner/fixture RSS [181.5, 68.5, 68.9] MiB. CPU 0.549 process-seconds over 61.118 seconds.

## Storage sample

100,000 healthy result rows measured 180.47 incremental bytes per row including indexes. At 100 monitors every 30 seconds: approximately 49.57 MiB/day or 1.45 GiB/30 days. This is a projection, not a 30-day test. Excludes WAL, backups, incidents, outbox, long error text, database overhead outside this relation.

## Necessary design changes demonstrated by this prototype

- Store incident state and notification outbox together in PostgreSQL. Redis loss must not erase consecutive-failure history.
- Use database leases plus ownership tokens to prevent competing or stalled workers from saving duplicate results.
- Display stale results as UNKNOWN. During database unavailability, the API/UI must show an explicit unavailable state; that UI is not implemented here.
- Deliver alerts at least once and require event-ID deduplication at the destination. Exactly-once external delivery is not guaranteed.
- Serialize refreshes in browser clients. The strict token-reuse policy also revokes legitimate simultaneous refreshes.

## Not established by this experiment

- Full Next.js dashboard, SSE, bucket authorization, public monitoring API, TLS timing/certificate handling, or multi-user isolation.
- Production cookie/CSRF design, MFA enrollment/recovery/encrypted secrets, general authentication rate limits, or a security audit. The test API returns tokens in JSON and uses an environment-provided TOTP secret for one fixture user.
- Complete SSRF defense: destination preflight and redirects were tested, but DNS rebinding protection requires pinned connections or enforced network egress policy.
- Long-duration stability, database disk limits, internet-scale targets, multiple machines or regions, email/SMS delivery, or independent monitoring of PulseGrid.
- Lease renewal: this spike uses a 3-second lease for short fixtures; production lease duration must exceed queue plus probe time or be renewed.

## Reproduce

From the project root, run `bash feasibility/run.sh`. It uses only containers in the dedicated `pulsegrid-feasibility` Compose project, loopback ports, and an isolated test database. The script removes its own containers and test database volume on exit.

Raw evidence: `feasibility/results/results.json`. Dependency versions: `uv.lock`.

## Experiment setup correction

The first attempt passed the initial monitoring/crash tests but dependency restart tests exposed changing Docker-assigned host ports; subsequent tests could not reach the restarted services. The harness now allocates explicit free loopback ports before startup so they remain stable across restarts. Original failed-run evidence is retained in `first-attempt.json`. These were harness failures, not evidence that dependency recovery had passed.

The next attempt found a prototype bug: the HTTP API borrowed a dead PostgreSQL connection after the database restarted, causing the first successful MFA attempt to return HTTP 500. Connection health is now checked when borrowing from the pool. Failed-run evidence is retained in `second-attempt.json`; the final full rerun includes authentication after the database restart.

Implementation references: [HTTPX timeout behavior](https://www.python-httpx.org/advanced/timeouts/), [PostgreSQL row locks](https://www.postgresql.org/docs/current/sql-select.html), [OWASP SSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).
