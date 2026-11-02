# Release checklist

Local evidence is recorded separately from external operator gates. Do not interpret a checked local gate as a public deployment claim.

- [x] Production application layout, shared domain/storage, independent worker and preserved feasibility evidence.
- [x] Fresh migrations, populated legacy migration, schema drift check, API readiness and dependency recovery.
- [x] 13-table model: direct ownership, versioned checks, archive/restore, database guardrails, retention and migration safety.
- [x] Owner bootstrap/recovery; password, TOTP and recovery codes; cookie/CSRF policy; refresh rotation/reuse; ownership and revocation.
- [x] Pinned destination connections, redirects disabled, deadline/body handling, lease renewal/fencing and hard-kill recovery.
- [x] Transactional results/incidents/outbox; webhook and SMTP fixtures; retry/exhaustion and ambiguous acceptance tests.
- [x] Browser account/configuration/history/notification flows, narrow layout and cross-tab refresh.
- [x] Frontend lint/types/build; Python lint/format; standalone production build and non-root Linux service files.
- [x] Caddy local HTTPS, Secure cookies and a public HTTPS monitor in the native production process topology.
- [x] Local PostgreSQL backup restoration, restored-session revocation and native database restart persistence.
- [x] One-hour controlled capacity gate and supplementary storage/query measurements passed; see `verification/capacity/report.json` and `NOTES.md` for the declared local baseline.
- [ ] Remote Linux staging deployment and soak, real server reboot, and public domain/IP certificate trust.
- [ ] Real SMTP/provider delivery and off-server backup copy/restoration with secured encryption keys.
- [ ] Independent off-server heartbeat and worker freshness alert installed.

The last three entries need the operator's target server, credentials/storage and independent observer. The code and procedures are provided; these external systems were not available in this session.
