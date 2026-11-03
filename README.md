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
bash scripts/dev.sh
```

Open **http://localhost:3000** and choose **Continue with Google** or **Create account**. Configure Google OAuth for Google sign-in and SMTP for email signup and recovery. Choose **Add service** on the Dashboard and paste a public website or API URL; its name is filled in automatically. Development data persists across restarts.

## Google sign-up and sign-in

Create a **Web application** OAuth client in [Google Auth Platform](https://console.cloud.google.com/auth/clients). Configure the consent screen and, while the app is in testing, add your Google account as a test user. Follow [Google's OpenID Connect setup](https://developers.google.com/identity/openid-connect/openid-connect).

Fill these fields in the root `.env` file (blank placeholders are included):

```dotenv
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
PUBLIC_ORIGIN=http://localhost:3000
```

Register **`http://localhost:3000/api/v1/auth/google/callback`** as an authorized redirect URI. In production, replace `http://localhost:3000` with the exact HTTPS `PUBLIC_ORIGIN`. The callback is derived from `PUBLIC_ORIGIN`; no frontend secret or extra redirect variable is required.

Run `uv run alembic -c apps/api/alembic.ini upgrade head`, then restart the API (or restart `bash scripts/dev.sh`). **Continue with Google** creates a new account on first use and signs in on later visits. No bootstrap command is needed. Leaving either Google field blank disables the Google button.

Google identities are matched by Google's stable account ID. Operator-approved conversion remains available for older accounts with password sign-in disabled. PulseGrid requests `openid email` and stores the Google account ID.

## Email sign-up and password recovery

Configure `SMTP_HOST` and `SMTP_FROM` with the other SMTP settings in the [operator runbook](docs/OPERATIONS.md#configuration). **Create account** sends a confirmation link that expires in an hour. Open it to choose a password of at least 12 characters, then **Log in**. **Forgot password?** sends a reset link that expires in 30 minutes. A successful reset ends every PulseGrid session on that account and keeps any enrolled MFA.

To add a password to a Google account, use **Settings → Account & security → Add a password**. Verify with the connected Google account, then open the email link in the same signed-in browser. PulseGrid asks for an authentication or recovery code if local MFA is enabled.

For the existing shared EC2 installation, see [pulling, deploying and converting the current account](docs/EC2_UPDATES.md). A Git pull alone does not rebuild or restart the running app in `/opt/pulsegrid`.

## Included workflows

Email and Google sign-up and returning sign-in; password recovery; account linking; MFA challenges; session revocation; HTTP GET/HEAD services; editing, pause/resume and confirmed history deletion; current/unknown/paused health; check-based uptime and coverage; time-to-headers charts, percentiles and histograms; persistent incidents; encrypted email/webhook destinations; saved alert coverage; delivery tests, retries and failure history.

## Everyday use

- **Dashboard:** see all services together, with problems first. Use **Add service** with a URL and optional name. Use **Advanced settings** to change the check frequency.
- **Services:** all services appear in one list. There is no group setup, filter or management. Services belong directly to your account. Enabled alert destinations cover all your services. Archive services to retain their history without continuing checks.
- **Settings:** Outage and recovery emails are on by default for Google and confirmed email signups. Turn them off in **Email alerts**, or use **Send test email** to check delivery and see the latest result. Sending mail requires SMTP configuration on the server.
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

## UI credits

The authenticator input adapts the OTP Input from [Rare UI](https://rareui.com). See the [Rare UI license](docs/RARE_UI_LICENSE.txt).
