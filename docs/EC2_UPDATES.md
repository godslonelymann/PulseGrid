# Updating the existing EC2 deployment

The Git checkout is `/home/ubuntu/PulseGrid`. Running services read code from
`/opt/pulsegrid`. A Git pull by itself does not update the running application.

After pushing your local changes to GitHub, SSH into EC2 and run:

```sh
cd /home/ubuntu/PulseGrid
git pull --ff-only
sudo bash scripts/deploy-ec2.sh
```

The update script builds the frontend separately with memory/CPU limits, creates
a local database/source backup, copies the release into `/opt/pulsegrid`, installs
locked Python dependencies, applies migrations and restarts only the three
PulseGrid services. There is a short maintenance window during activation.
It preserves `/etc/pulsegrid/pulsegrid.env`, the existing systemd configuration,
API port 8100 and Caddy configuration. The Catalogue and Airflow are not restarted.
If a command fails, inspect its error before restarting services. A failed
migration is not automatically rolled back. Release rollback copies are local.
After the update, follow the [operations guide](OPERATIONS.md#health-and-operations)
to activate the independent HTTPS and worker checks, and
[install and rehearse the daily encrypted S3 backup](OPERATIONS.md#backup-and-restore).

## Enable visitor IP rate limits on the existing host

The update script preserves the installed service and shared Caddy configuration.
On the first release with email sign-in, use `sudo systemctl edit pulsegrid-api`
to override the API command. Use an empty `ExecStart=` followed by a copy of the
effective `ExecStart` with `--no-proxy-headers` replaced by
`--proxy-headers --forwarded-allow-ips=127.0.0.1`. Keep the existing
`--host 127.0.0.1 --port 8100` and service paths. Reload systemd and restart
`pulsegrid-api`. Use `systemctl cat pulsegrid-api` to inspect the installed command.

Verify that the PulseGrid Caddy route proxies directly to `127.0.0.1:8100` and
derives `X-Forwarded-For` from the incoming connection. Caddy's default does this
when there are no trusted proxies in front of it. If the shared configuration
overrides the header, use `header_up X-Forwarded-For {remote_host}` in the
PulseGrid API proxy only. Run the staging source-IP checks before release.

## Optional conversion of an older password account

New users can choose **Continue with Google** or create an email account after configuring SMTP. The conversion below applies to older accounts provisioned by an operator whose email was never confirmed through PulseGrid.

To convert the original EC2 account while retaining its monitors, history and email-alert settings, run:

```sh
cd /opt/pulsegrid
sudo -u pulsegrid -H env ENV_FILE=/etc/pulsegrid/pulsegrid.env \
  .venv/bin/python scripts/run-env.py .venv/bin/python -m pulsegrid.manage \
  allow-google-link gaikwadanurag16@gmail.com
```

Within **15 minutes**, open `https://mypulsegrid.duckdns.org` and select
**Continue with Google** using that exact Google account. If the approval expires
or Redis restarts, run the command again.

The server verifies Google's identity, email, signature, audience, nonce and
browser-bound state before using the one-time operator approval. It keeps the
existing user ID and records, removes the old password hash and revokes previous
sessions. An existing local MFA requirement is preserved. Other accounts cannot
be linked just because their email matches. Password/MFA changes invalidate an
unused approval. A failed database commit may consume approval; rerun the command
before retrying in that case.

Keep the configured Google callback URL:
`https://mypulsegrid.duckdns.org/api/v1/auth/google/callback`.
Do not put Google or SMTP secrets in Git.

## Verification

```sh
curl -f https://mypulsegrid.duckdns.org/api/v1/health/ready
sudo systemctl status pulsegrid-api pulsegrid-worker pulsegrid-web
```

Check **Continue with Google**, email signup, login and password recovery with the configured providers. After conversion, verify that existing monitors remain visible and fresh checks continue arriving.

Legacy password API regressions use an explicit `ENABLE_LEGACY_PASSWORD_AUTH=true`
override in isolated verification profiles only. Leave this unset on EC2.
