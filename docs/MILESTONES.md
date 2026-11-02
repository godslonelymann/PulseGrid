# PulseGrid implementation checklist

Each gate is checked only after evidence exists. Feasibility tests are excluded.

- [x] 1. Foundation: schema, migrations, isolated development services, health
- [x] 2. Identity/configuration: password, MFA, rotation/revocation, ownership, forms
- [x] 3. Monitoring: pinned safe probing, leases, fencing, state transitions
- [x] 4. Alerts: transactional incidents/outbox, leased retries, failure visibility
- [x] 5. Dashboard: browser workflows, charts, metrics, account/session controls
- [x] 6. Packaging: native services, Caddy, backup/restore, release/rollback documentation
- [x] 7. Validation: production-code fault tests, hour capacity measurements, staging limitations

See `verification/` for captured runs and `docs/VERIFICATION.md` for scope and gaps.

## Current evidence

- Fresh Alembic databases and API readiness passed.
- Password/MFA/recovery, token rotation/reuse, ownership, state/metrics, worker fencing and transactional notifications pass real PostgreSQL/Redis tests.
- PostgreSQL/Redis stop/restart and natural expiry after SIGKILL passed.
- Two final Chromium scenarios passed, including configuration, MFA management, password change, outage/recovery across a hard worker restart, narrow layout, deep links, cross-tab refresh and revocation.
- Previous packaging and local staging results are archived under `verification/`. Native service/build/staging procedures replace those workflows; see `docs/VERIFICATION.md` for current results.
- The real 3,600-second capacity run passed: 12,000/12,000 checks, zero missing/duplicate results, p95 lag 211.786ms. See the declared workload/version limits in verification/capacity/NOTES.md.
- Remote production, external heartbeat, real SMTP and remote soak remain external operator gates, not claimed as passed.

The original local milestone evidence is preserved; native transition results are recorded separately. External deployment/observer/provider gates remain unchecked in `docs/RELEASE_CHECKLIST.md`; a remote production release is not claimed.
