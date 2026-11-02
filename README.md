# PulseGrid

Self-hosted HTTP monitoring with a Next.js dashboard, FastAPI API, a separate asyncio worker, PostgreSQL history, Redis security state, and transactional outage/recovery notifications.

The implementation lives in `apps/`, `infra/`, `scripts/` and `tests/`. The original `feasibility/` experiment and its evidence are preserved separately; prototype tests are not application coverage.

## Run locally

Requires Python 3.13, uv, Node 24, pnpm, PostgreSQL 17+ tools and Redis 7+. See [native installation and service controls](docs/OPERATIONS.md). From this directory:

```sh
uv sync --frozen
uv run python scripts/init-env.py
uv run python scripts/native-services.py up dev
uv run alembic -c apps/api/alembic.ini upgrade head
PYTHONPATH=apps/api uv run python -m pulsegrid.manage bootstrap you@example.com
bash scripts/dev.sh
```

Open **http://localhost:3000**. Bootstrap prompts for your password; no default account exists. Sign in and choose **Add service** on the Dashboard. Paste a public website or API URL; its name is filled in automatically. Password users can enroll an authenticator in Settings and save their recovery codes. Development data persists across restarts.

## Google sign-up and sign-in

Create a **Web application** OAuth client in [Google Auth Platform](https://console.cloud.google.com/auth/clients). Configure the consent screen and, while the app is in testing, add your Google account as a test user. Follow [Google's OpenID Connect setup](https://developers.google.com/identity/openid-connect/openid-connect).

Fill these fields in the root `.env` file (blank placeholders are included):

```dotenv
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
PUBLIC_ORIGIN=http://localhost:3000
```

Register **`http://localhost:3000/api/v1/auth/google/callback`** as an authorized redirect URI. In production, replace `http://localhost:3000` with the exact HTTPS `PUBLIC_ORIGIN`. The callback is derived from `PUBLIC_ORIGIN`; no frontend secret or extra redirect variable is required.

Run `uv run alembic -c apps/api/alembic.ini upgrade head`, then restart the API (or restart `bash scripts/dev.sh`). **Continue with Google** creates a new account on first use and signs in on later visits. Google sign-up does not require the bootstrap command. Leaving either Google field blank disables the Google button; password sign-in still works.

Google identities are matched by Google's stable account ID. An existing password account with the same email is not automatically linked; use its password to sign in. Google-only accounts manage their password and two-step verification at Google. Existing PulseGrid MFA remains required for any Google identity whose local account has MFA enabled. Only `openid email` access is requested; Google access/refresh tokens are not stored.

## Included workflows

Google sign-up and returning sign-in; Google account security settings; password/TOTP/recovery-code login; MFA enrollment/replacement/disable; password change; session revocation; HTTP GET/HEAD services; editing, pause/resume and confirmed history deletion; current/unknown/paused health; check-based uptime and coverage; time-to-headers charts, percentiles and histograms; persistent incidents; encrypted email/webhook destinations; saved alert coverage; delivery tests, retries and failure history.

## Everyday use

- **Dashboard:** see all services together, with problems first. Use **Add service** with only a URL and optional name. HTTP method, response codes, check frequency and timeout live in **Advanced settings**.
- **Services:** all services appear in one list. There is no group setup, filter or management. Services belong directly to your account. Enabled alert destinations cover all your services. Archive services to retain their history without continuing checks.
- **Alerts:** enable email alerts for outages and recoveries across all current and future services. Alerts automatically go to the email address you signed up with. The configured SMTP account is only the sender. **Send test email** is available once server email delivery is configured. If SMTP is missing, the screen says so; enabling alerts alone does not set up delivery.
- **Advanced alerts:** webhooks, additional destinations and delivery history remain in **Advanced alert settings**. New destinations cover all services; existing destinations keep their saved coverage and can be expanded to all services when edited.
- **Service details:** current status, uptime, response-time chart and recent outages appear first. **More details** contains coverage, percentiles, the histogram and individual checks. **No recent checks** always remains separate from **Down**.
- **Settings:** account security and session management.

## Documentation and verification

- [Complete project plan: scope, architecture, progress and next steps](PROJECT_PLAN.md)
- [Milestone checklist](docs/MILESTONES.md)
- [Current 13-table data model and migration rules](docs/DATA_MODEL.md)
- [Architecture, security, scheduling and delivery guarantees](docs/ARCHITECTURE.md)
- [Setup, configuration, deployment, backup/restore and rollback](docs/OPERATIONS.md)
- [Verification results and remaining release gates](docs/VERIFICATION.md)
- [Implementation plan](IMPLEMENTATION_PLAN.md)
- [Original feasibility report](feasibility/results/REPORT.md)

Production browser traffic enters through Caddy at the server's public domain/IP. Caddy forwards traffic to native API/frontend processes on loopback. PostgreSQL and Redis stay private. Production uses the Linux service files in `infra/systemd/`; local data lives under ignored `.local-services/`.

Run the production-code verification suite:

```sh
bash scripts/test.sh
pnpm --dir apps/web install --frozen-lockfile
pnpm --dir apps/web typecheck
pnpm --dir apps/web lint
pnpm --dir apps/web build
pnpm --dir apps/web exec playwright install chromium
bash scripts/e2e.sh
```

The fault suite intentionally stops **only** the dedicated `pulsegrid-verification` native PostgreSQL/Redis processes. The browser suite resets only `pulsegrid_test`; run it after pytest, not concurrently. Capacity uses its own database and runs for a real hour; see the verification guide for the exact command and saved measurements. Public deployment, external heartbeat, provider credentials and remote staging validation require an operator-supplied server/services.

Stop local dependencies with `uv run python scripts/native-services.py down dev`; database data is retained. Build the native production frontend with `bash scripts/build-web.sh`.
