# Local Finance

A self-hosted, single-user finance ledger and HTTPS dashboard. Python standard library only; no agent framework, telemetry, remote fonts, JavaScript, CDN, hosted database or cloud-AI fallback. Source code and synthetic tests only: no accounts, credentials, database, deployment inventory or chat history are included.

## Features and boundaries

- Read-only SimpleFIN imports, account balances and exact integer-minor-unit accounting with explicit currency exponents. Currencies are never silently combined.
- Pending/posted records, transfer/card-payment review, manual corrections, reusable merchant rules, readable merchant identities, category registry and human-confirmed examples.
- Explicitly approved budgets and alert thresholds, monthly/weekly reports, persisted bounded feed retries and independent categorization backlog processing.
- Optional local Ollama category suggestions and factual read-only question-to-query plans. Code computes results; the model cannot execute SQL, write budgets, act on accounts or silently exclude transactions.
- Login-protected HTTPS with CSRF protection, throttling, secure cookies, no request logs, escaped output, locally hosted CSS and restrictive response headers.
- Optional generic-only Telegram outbox delivery, and a standalone restart supervisor.

This is not financial advice or a bank-account management interface. Reports remain provisional until pending, duplicate and movement candidates have been reviewed. This is a single-user LAN application, not a hardened internet-facing service.

## Requirements

Linux or another POSIX system with Python **3.11+**, SQLite support in Python and the `openssl` CLI supporting `req -addext`. No pip install is needed. Notification/supervisor locking uses POSIX `fcntl`; native Windows is not supported (use Linux containers/WSL). OpenSSL generates the initial self-signed certificate. Docker is optional.

## First run (no bank or model required)

Run everything as the unprivileged account that will own the service and its data. Start from the repository directory:

```sh
cp config.example.env .env
chmod 600 .env
# Edit .env using your local editor before first init.
set -a
. ./.env
set +a
python3 finance.py init
python3 finance.py status
python3 finance.py serve
```

Open `https://localhost:8787`. The first `init` creates a private 0700 data directory and 0600 SQLite, authentication, password, certificate and key files. It **does not print the password**. Retrieve `login_password` inside the configured data directory using a trusted local editor, never by copying it into chat, logs, screenshots or issue reports. The password is random; only its salted PBKDF2 digest is used for authentication. The plaintext recovery file is private but not encrypted: protect the host and disk.

Trust the generated `tls.crt` explicitly on clients after verifying its fingerprint locally. Do not disable TLS verification. `init` is idempotent: it does not rotate existing credentials or certificates. To replace a self-signed certificate after a SAN change, stop this deployment, securely back up the existing TLS pair outside the repository, remove that pair locally and rerun `init`. Existing authentication/ledger files stay unchanged. Alternatively install your own matching `tls.crt`/`tls.key`, both 0600 and readable by the service UID. Certificate renewal is an operator responsibility.

No bank or model calls happen on ordinary `init`, `status` or `serve` without opt-in flags. Clicking model suggestion/question actions explicitly invokes the configured local model. Do not enable the scheduler or synchronize until provisioning is complete.

## Deployment configuration

The application reads environment variables only. It never automatically reads any `.env`, home-directory agent configuration or unrelated secrets. The shell snippet above explicitly exports the example configuration. Relative paths are resolved against the process working directory; absolute data paths are recommended for services/cron.

| Setting | Default / behavior |
| --- | --- |
| `FINANCE_DATA_DIR` | `./data`; all ledger, authentication, TLS, lock and session-independent state |
| `FINANCE_SIMPLEFIN_CREDENTIAL` | `FINANCE_DATA_DIR/simplefin_access_url`; private durable access URL, not setup token |
| `FINANCE_OLLAMA_URL` | `http://localhost:11434`; loopback or explicit RFC1918 IPv4 / ULA IPv6 LAN literal only; no public addresses, arbitrary DNS names, userinfo, path, query or fragment |
| `FINANCE_OLLAMA_MODEL` | `qwen3:8b`; set to your installed **local-weight** model, never a cloud-backed alias |
| `FINANCE_BIND` | `127.0.0.1`; IPv4 listener address, use your LAN address or `0.0.0.0` only behind a LAN firewall |
| `FINANCE_PORT` | `8787`; HTTPS listening port |
| `FINANCE_TLS_SANS` | `localhost,127.0.0.1`; comma-separated DNS hostnames/IP addresses for certificate and accepted DNS Host headers |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Unset: delivery disabled. Both required; ID is an explicit signed integer |
| `FINANCE_SCHEDULER` | Supervisor only: `1` adds `--scheduler`; otherwise off |
| `FINANCE_MODEL_ENABLED` | Supervisor only: `1` adds `--model`; otherwise off |
| `FINANCE_LAN_IP` | Compose only: published host interface, default `127.0.0.1` |

CLI global overrides precede the subcommand: `python3 finance.py --data /your/private/directory --credential /your/private/access-file status`. If overriding `--data`, also set `--credential` when using bank jobs; environment defaults are resolved before CLI parsing. `serve --bind` and `serve --port` override the corresponding environment settings.

### LAN access

Before first initialization, add your own private LAN IP and/or DNS name to `FINANCE_TLS_SANS`, retaining `localhost,127.0.0.1` for local health checks. Bind the service to the intended IPv4 interface. Allow the selected TCP port only from your trusted LAN using your host/router firewall. Never expose it through public port forwarding. Verify actual access from another intended client, hostname/certificate validation and login before considering it deployed; binding a container listener alone does not prove LAN reachability.

Private DNS SAN names are allowed for the dashboard, but model endpoints deliberately require localhost or a private literal IP to avoid DNS-rebinding ambiguity. The LAN model operator is inside the trust boundary. HTTP to a LAN model is **not encrypted**; use a trusted segment or validated HTTPS endpoint. Endpoint/metadata checks cannot prove that a malicious model server does not forward data: restrict that server's outbound network access and use trusted local weights.

## Secure SimpleFIN provisioning

Obtain a setup token directly from your own SimpleFIN account. Do not put it in command arguments, an environment variable, shell history, an issue or chat. With the intended service UID and configuration active:

```sh
python3 provision.py
# Or explicitly choose a NEW private destination:
# python3 provision.py --output /your/private/directory/simplefin_access_url
python3 finance.py sync
```

The CLI uses a hidden `getpass` terminal prompt (fails rather than echoing without a terminal), strictly decodes/validates the token, POSTs an empty body only to HTTPS `bridge.simplefin.org` or `beta-bridge.simplefin.org` `/simplefin/claim/...` on port 443, uses an explicit User-Agent and refuses redirects/proxies. It reserves a new 0600 output in a 0700 directory owned by the running UID, saves/fsyncs the response and directory before validating the returned access URL, and never prints secrets. It never overwrites an existing credential or automatically retries a one-time claim.

A failed/interrupted claim may leave an empty marker or an invalid response in the private output. Inspect it locally before deciding whether to replace/revoke the credential; do not repeatedly consume the same token. Permission/ownership must match the service UID. Do not loosen modes to make root-owned secrets readable. Never enable provider/network debugging around credentials.

`sync` contacts only the allowlisted SimpleFIN HTTPS host, imports a bounded overlapping 44-day window and stores provider notices privately. A successful import with provider attention notices is not proof that all accounts/history are complete. The aggregator still processes your bank data; local hosting does not remove that exposure.

## Local models and privacy

Install/pull an appropriate local Ollama model separately, without giving the app access to a model-management service. Choose its exact name via `FINANCE_OLLAMA_MODEL`. Every prompt path first requests `/api/show` using only the configured model name. It requires local architecture/family/completion metadata and rejects cloud/remote metadata (including remote keys), cloud-named models, malformed or missing metadata, failures and redirects. It then sends the financial prompt to that same configured endpoint/model. No fallback, automatic model pull or alternate provider exists. Model servers that omit required metadata fail closed.

```sh
python3 finance.py sync --model
python3 finance.py categorize --batches 20 --seconds 600
```

Categorization is independent of fetching: it can process an existing backlog without bank quota. Merchant strings are untrusted inputs, outputs use strict schemas/registered categories, and human decisions win over model suggestions. Dashboard Q&A sends questions and short in-session plan history to the configured local model; detailed results stay in the authenticated dashboard. No financial prompt or detailed answer is sent to Telegram.

## Scheduling, notifications and restart

For daily UTC jobs (random persisted minute after 03:00), bounded retries, weekly catch-up reports and optional model categorization:

```sh
python3 finance.py serve --scheduler
# Explicitly opt into model processing too:
# python3 finance.py serve --scheduler --model
# Or schedule a one-shot job in your own cron/service manager:
python3 finance.py tick
```

Do not run multiple schedulers. Job claims/retry counts persist across restarts. `serve` stays in the foreground; use a POSIX service manager or Compose for boot startup and crash restart. `python3 supervise.py` is an optional one-shot, locked repair probe that uses this repository's own script path, configured data/certificate/bind/port and Python interpreter. Schedule it yourself with the same environment; it neither installs cron nor relies on an agent runtime. It refuses to spawn repeatedly on an occupied unhealthy port. Background processes are not boot persistence.

Telegram is optional. Configure both settings privately only if you want the fixed notice **“Private finance update available. Open your local dashboard.”** delivered to the configured recipient:

```sh
python3 notify.py --status
python3 notify.py
```

Schedule `notify.py` separately (for example every five minutes) with the same data directory and explicitly exported settings; the application does not modify your crontab. No token is loaded from any file by default. Delivery coalesces the pending snapshot, validates the recipient/provider acknowledgement and only then marks those IDs delivered. Any non-generic outbox text blocks the whole batch. Errors leave it pending; HTTP 429 waits for the next invocation rather than looping. Delivery is at-least-once: a crash after provider acceptance but before database commit can duplicate a generic notice. Telegram necessarily learns your recipient identity and generic update timing. Removing either setting disables delivery without deleting pending notices.

## Docker Compose (optional)

The Dockerfile runs as UID/GID **10001:10001**. The application image contains only explicitly copied source/static files. `.dockerignore` is deny-by-default; runtime data is never a build input. Image building needs access to the base-image/package registries; normal app runtime has no package-install or update calls.

Compose uses an owned named volume mounted at `/data` for durable ledger/secrets/TLS, a read-only root filesystem and dropped capabilities. It defaults to a **loopback-only** host port and no scheduler/model jobs. Set your own LAN interface in `FINANCE_LAN_IP` only after adding corresponding SANs and applying firewall restrictions. In a container, localhost means the container itself: set `FINANCE_OLLAMA_URL` to your own trusted model server's private LAN IP, not the host's loopback address. Compose retains port 8787 inside the container; `FINANCE_PORT` changes the published host port.

```sh
# Edit/export configuration first; Compose also explicitly reads a local .env.
docker compose build
docker compose run --rm finance init
# Retrieve the volume's login_password using a trusted local editor, not logs/chat.
# Provision only when ready; -it supplies the hidden prompt's terminal:
docker compose run --rm -it --entrypoint python3 finance provision.py
docker compose up -d
# Inspect operational state only:
docker compose exec finance python3 finance.py status
```

To enable scheduled bank jobs, change Compose's command to `["serve", "--scheduler"]` after provisioning, optionally adding `"--model"`. Notification delivery remains a separate one-shot command: `docker compose exec finance python3 notify.py`, invoked by your scheduler if desired. Do not copy `.env` into the image or put tokens directly in versioned Compose YAML. Container environment settings are visible to administrators/Docker-inspect; use a private host and trusted administrators. A bind mount instead of the named volume must be created/chowned to UID 10001, mode 0700; never fix permission errors with 0777.

Do not use `docker compose down -v` unless intentionally destroying all persisted data. Back up the configured data directory/volume securely outside the source tree while the service is stopped (or use SQLite's backup API); protect backups at least as strictly as live credentials. Filesystem permissions are not encryption at rest.

## Tests and contributing

```sh
python3 -W error::ResourceWarning -m unittest discover -s tests -v
python3 -W error::ResourceWarning -m unittest test_notify test_supervise -v
```

Tests use synthetic records, temporary directories, injected provider/model/Telegram transports and ephemeral localhost HTTPS servers. The fresh-directory smoke initializes a new empty ledger, validates TLS using its generated certificate, logs in without printing the password and exercises authenticated pages/static paths. No real bank/model/Telegram connection is required. CI runs these commands without secrets. Docker/LAN deployment and a real feed need separate operator verification; passing synthetic tests is not evidence of live connectivity or complete bank coverage.

Keep every behavioral change test-first. Never add databases, credentials, key/certificate files, generated output, backups, screenshots, account exports, transcripts or personal deployment notes. `.gitignore` is defense in depth, not a substitute for reviewing the exact staged manifest and scanning for secrets. Restrict source releases to platform files and synthetic tests. Source is MIT licensed; third-party notices are in `THIRD_PARTY.md`.
