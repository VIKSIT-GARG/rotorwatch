# GCP scraper, database and API

Run one standard Compute Engine Ubuntu 24.04 VM. The process stays online while
scans run periodically. The script now plans `e2-standard-2` (2 vCPU, 8 GB) for
the combined scraper, OpenClaw and Chromium workload. Review current costs and
measure actual usage; `GCP_MACHINE_TYPE` can override the proposed machine.
Use one Python process, one gateway and persistent Docker volumes.

## Provision and upload

On your computer or Cloud Shell, sign in, select your own project and enable
Compute Engine. The VM script prints a plan by default; `--apply` creates the VM.

```bash
gcloud auth login
gcloud services enable compute.googleapis.com --project YOUR_PROJECT_ID
bash scripts/gcp-vm.sh YOUR_PROJECT_ID asia-south1-a
bash scripts/gcp-vm.sh YOUR_PROJECT_ID asia-south1-a --apply
gcloud compute scp drone-parts-assistant.zip drone-parts-assistant:~/ --project YOUR_PROJECT_ID --zone asia-south1-a
gcloud compute ssh drone-parts-assistant --project YOUR_PROJECT_ID --zone asia-south1-a
```

The startup script installs Docker, Compose and unzip, and creates
`/opt/drone-parts-assistant`. Wait for startup to complete. It does not fetch code
or put credentials into instance metadata. Confirm current costs in your GCP
calculator for the region, disk, external IP and traffic before provisioning.

Inside the VM:

```bash
unzip -q -o ~/drone-parts-assistant.zip -d ~/
sudo cp -a ~/drone-parts-assistant/. /opt/drone-parts-assistant/
cd /opt/drone-parts-assistant
sudo cp .env.example .env
sudo chmod 600 .env
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
sudo nano .env
```

Set `API_TOKEN` and a separate `OPENCLAW_GATEWAY_TOKEN`. Configure both channel
destinations and complete initialization/QR pairing in [OPENCLAW.md](OPENCLAW.md)
before starting the combined stack. That guide owns the OpenClaw first-run steps. The supplied interval is 900 seconds. Both integrations require their own account setup. The web API can run
independently, but an unpaired gateway is not a verified messaging setup.

```bash
sudo docker compose -f compose.yaml -f compose.openclaw.yaml build
sudo docker compose -f compose.yaml -f compose.openclaw.yaml run --rm assistant python -m drone_assistant check --no-discovery
sudo docker compose -f compose.yaml -f compose.openclaw.yaml up -d
sudo docker compose -f compose.yaml -f compose.openclaw.yaml ps
sudo docker compose -f compose.yaml -f compose.openclaw.yaml logs --tail 100 assistant
```

Check actual retailer access from this VM. Blocks and unresolved variants remain
unknown. Leave `PROXY_RESOLVES_DNS=false` with normal VM DNS. No access-control
bypass is implemented.

For Telegram, send `/start` to your bot, run
`sudo docker compose -f compose.yaml -f compose.openclaw.yaml run --rm assistant python -m drone_assistant telegram-id`
before starting the normal poller, and put your private ID into the allowlist.
The helper reads updates only. After starting the service, send `/start` again.

## HTTPS for the hosted frontend

The base Compose file binds port 8000 to VM loopback. Vercel/Render needs an
HTTPS API URL, so add the supplied Caddy reverse proxy.

1. Reserve a stable VM external IP in GCP and point an API hostname's DNS A
   record at it. Use a hostname you control, such as `api.your-domain.com`.
2. Add `API_DOMAIN=api.your-domain.com` to the VM `.env` file.
3. Add your exact frontend origin to `CORS_ORIGINS`, with no trailing slash.
   Multiple origins are comma-separated. Do not use wildcard CORS.
4. Allow TCP 80/443 to this VM in GCP, then start the HTTPS overlay.

From Cloud Shell, for the default VPC (adapt `--network` if yours differs):

```bash
gcloud compute instances add-tags drone-parts-assistant --tags rotorwatch-api --project YOUR_PROJECT_ID --zone asia-south1-a
gcloud compute firewall-rules create rotorwatch-api-https --network default --allow tcp:80,tcp:443 --target-tags rotorwatch-api --project YOUR_PROJECT_ID
```

Then inside the VM:

```bash
sudo docker compose -f compose.yaml -f compose.openclaw.yaml -f compose.https.yaml up -d
sudo docker compose -f compose.yaml -f compose.openclaw.yaml -f compose.https.yaml logs --tail 50 caddy
curl --fail https://api.your-domain.com/health
```

Caddy obtains and renews its certificate when DNS and inbound ports are ready.
Keep port 8000 off the public firewall. The API token protects data and mutations;
CORS limits browser origins but is not a substitute for authentication.

Open the frontend, select **Connect backend**, and enter the HTTPS URL plus
`API_TOKEN`. No provider secrets belong in frontend build settings.
See [FRONTEND.md](FRONTEND.md) for Vercel/Render settings.

For testing before configuring a domain, use an SSH tunnel:

```bash
gcloud compute ssh drone-parts-assistant --project YOUR_PROJECT_ID --zone asia-south1-a -- -L 8000:127.0.0.1:8000
```

Open `http://127.0.0.1:8000` on your computer and connect using that origin/token.
This tests the GCP backend through the tunnel without exposing HTTP publicly.

## Verify, back up and update

```bash
sudo docker compose -f compose.yaml -f compose.openclaw.yaml exec assistant python -m drone_assistant health
sudo docker compose -f compose.yaml -f compose.openclaw.yaml exec assistant python -m drone_assistant backup /app/data/backup.sqlite3
sudo docker cp "$(sudo docker compose -f compose.yaml -f compose.openclaw.yaml ps -q assistant)":/app/data/backup.sqlite3 ./backup.sqlite3
```

Health reports the event-loop heartbeat. The UI's observation timestamps,
retailer health and service logs establish source freshness. The process keeps
running after SSH disconnects and restarts after VM/Docker reboot, provided the
VM is running. Compose does not restart a container solely for an unhealthy
health check; configure external uptime monitoring if required.

Copy backups off the VM. For updates preserve `.env` and volumes, replace source,
then use `sudo docker compose -f compose.yaml -f compose.openclaw.yaml -f compose.https.yaml up -d --build`
(or omit the overlay when using only a tunnel). Back up v1 databases before the
v2 migration. Keep one replica; no distributed queue/worker ownership is present.

`docker compose down` preserves volumes. Do not use `down -v` unless you intend
to delete data. Stopping the container does not stop VM/disk/IP charges.

No GCP VM has been provisioned by this build: the project ID, account credentials
and API domain were not provided. Docker/gcloud were unavailable locally, so
actual image build, TLS issuance, region access and GCP uptime remain deployment
checks for your environment. Scripts were syntax-checked and CI includes Docker.
