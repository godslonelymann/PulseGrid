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
migration is not automatically rolled back. Release backups are local only;
off-server backups and restore rehearsals remain separate operator work.

## One-time conversion of the existing password account

New users sign up directly through **Continue with Google**. Do not bootstrap a
password account. Normal installations disable password sign-in and password
settings, including direct password-login API requests.

The account already created on EC2 must be converted once to keep its monitors,
history and email-alert settings. After deploying the update, run:

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

The sign-in page should contain only **Continue with Google**, with no email or
password form. After the one-time conversion, the existing monitors should still
be visible and fresh checks should continue arriving.

Legacy password API regressions use an explicit `ENABLE_LEGACY_PASSWORD_AUTH=true`
override in isolated verification profiles only. Leave this unset on EC2.
