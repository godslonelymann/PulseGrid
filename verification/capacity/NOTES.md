# Capacity scope and version boundary

This run uses the production `pulsegrid.worker.runner` implementation, not `feasibility/`. It started while the browser/authentication work continued. The monitor, schedule, result, incident and outbox schemas and scheduling/state execution rules remained unchanged during the run. The fixtures each resolve to one explicitly allowed IPv4 address.

Later work added the durable authentication-only TOTP field (`0002`), API metric retention clipping/last-check presentation, UI workflows, and extra denial-only cases for Azure/IPv6 transition addresses. Those newly prohibited address classes are covered in the final backend tests; the hour run does not exercise them. The capacity database retains revision `0001`, with the same monitoring schema.

The declared capacity environment uses native Python 3.13.15 and PostgreSQL 17.6 in the isolated capacity container. Final production image pins were refreshed to current patch images after this test started and verified separately in local staging. The final production package therefore still needs its own deployment-machine hour test and remote soak before a capacity promise. This local run is evidence for the 100-monitor engineering baseline on the recorded machine, not a certification of public internet/TLS or another deployment configuration.

HTTP mix is 70 immediate successes, 20 successes delayed 250ms, five HTTP 503 responses and five two-second endpoints with a one-second timeout. No notification rules are attached in this capacity run, so its outbox measurement is idle backlog; alert failure/retry and delivery behavior are exercised by separate integration/browser tests. Builds, browser tests and local staging also ran on the host during measurement.
