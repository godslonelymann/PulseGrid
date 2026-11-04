# PulseGrid

## What is the project?

PulseGrid is a self-hosted uptime monitor for websites and HTTP APIs. It checks whether services are responding and alerts you when they go down or recover.

## What does it do?

- Add a service by URL and set how often to check it. Edit, pause, or resume checks. Archive and restore services to keep their history, or delete a service and its history.
- View current health, uptime, check coverage, response times, and outage history. The dashboard shows when checks are missing or stale.
- Receive outage and recovery emails at your account address when email alerts are enabled and SMTP is configured. Send a test email from the dashboard or configure webhook alerts through the API. The worker retries failed deliveries and records their outcomes.
- Sign in with Google or a verified email and password. Manage password recovery, authenticator-based two-factor authentication, and active sessions.

## How does it work?

- The **Next.js** dashboard calls a **FastAPI** backend to manage services and display monitoring data. In production, **Caddy** routes browser traffic to the dashboard and API.
- **PostgreSQL** stores accounts, service settings, check schedules, results, incidents, and queued alerts. **Redis** stores temporary sign-in data and tracks rate limits.
- A separate **Python worker** takes due checks from PostgreSQL and sends HTTP requests with **HTTPX**. Checks use GET by default, with HEAD available through the API. The worker records the HTTP status and time to response headers, or the error if the request fails.
- Two consecutive failed checks open an incident. Two consecutive successful checks close it. The worker saves the incident change and queues alerts for enabled destinations in the same database transaction. It sends queued alerts by SMTP or webhook and updates their delivery status.

The authenticator input is adapted from [Rare UI](https://rareui.com).

## Working Flowchart

```mermaid
flowchart TD
    User["User"] --> Web["Next.js dashboard"]
    Web -->|"Sign in, manage services, request status"| API["FastAPI backend"]
    API <-->|"Temporary sign-in data and rate limits"| Redis["Redis"]
    API <-->|"Google sign-in"| Google["Google"]
    API -->|"Confirmation and reset links"| Email["SMTP email server"]
    API <-->|"Save settings and read monitoring history"| DB[("PostgreSQL")]
    API -->|"Health, metrics, and incidents"| Web

    DB -->|"Due checks for active services"| Worker["Python monitoring worker"]
    Worker -->|"HTTP GET or HEAD"| Target["Website or API"]
    Target -->|"Response or request failure"| Worker
    Worker -->|"Save result, update incident, schedule next check"| DB
    Worker -->|"Queue alerts on outage or recovery"| Queue["Alert queue in PostgreSQL"]
    API -->|"Queue a test alert"| Queue

    Queue --> Sender["Worker alert dispatcher"]
    Sender -->|"Send email alert"| Email
    Sender -->|"Send webhook alert"| Webhook["Webhook endpoint"]
    Sender -->|"Record delivery or schedule retry"| Queue
    Email -->|"Account links and alerts"| User
```
