# ALPHA//DESK Worker — Deployment Runbook (Hetzner / systemd)

This runbook deploys the standalone auto-trading worker
(`python -m backend.trading.engine.worker`) as a systemd service on a Linux VPS.
Default CLI mode is **shadow**; set `--mode paper` with `APCA_PAPER=true` for the
paper validation window. Do not switch to live until
`docs/PAPER_VALIDATION_RUNBOOK.md` is completed.

Related files:
- `deploy/alphadesk-worker.service` — the systemd unit.
- `.env.example` — required environment variables (Polygon, `ORDER_STORE_BACKEND`).
- `docs/ARCHITECTURE_DECISION.md` — keep research UI; upgrade execution/data.

---

## 1. Provision the VPS

Recommended: **Hetzner CPX21** (3 vCPU / 4 GB / 80 GB NVMe, ≈ €4.51/mo) — see the
VPS comparison in `UPGRADE_LOG.md`. Ubuntu 24.04 LTS image.

```bash
# On first login as root:
apt update && apt upgrade -y
apt install -y git python3.12 python3.12-venv python3-pip
```

## 2. Create a dedicated service user

Running as a non-login system user limits blast radius if the worker is compromised.

```bash
sudo adduser --system --group alphadesk
```

## 3. Clone the repo to /opt

```bash
sudo git clone <your-repo-url> /opt/stock-analyzer
sudo chown -R alphadesk:alphadesk /opt/stock-analyzer
```

## 4. Create the virtualenv and install deps

```bash
sudo -u alphadesk python3.12 -m venv /opt/stock-analyzer/.venv312
sudo -u alphadesk /opt/stock-analyzer/.venv312/bin/pip install --upgrade pip
sudo -u alphadesk /opt/stock-analyzer/.venv312/bin/pip install -r /opt/stock-analyzer/requirements.txt
```

## 5. Configure secrets (.env)

Copy the template and fill in real values. The file holds broker credentials, so
lock it down to the service user only.

```bash
sudo -u alphadesk cp /opt/stock-analyzer/.env.example /opt/stock-analyzer/.env
sudo -u alphadesk nano /opt/stock-analyzer/.env      # fill in real values
sudo chmod 600 /opt/stock-analyzer/.env
```

Required for trading (see `.env.example` for the full list):

| Variable | Purpose |
|---|---|
| `APCA_API_KEY_ID` | Alpaca API key ID |
| `APCA_API_SECRET_KEY` | Alpaca API secret |
| `APCA_PAPER` | **Must be `true`** during validation |
| `LLM_API_KEY` / `LLM_BASE_URL` | Optional — only if LLM enrichment is used |

Never commit `.env`. Rotate the Alpaca keys immediately if they are ever exposed.

## 6. Install the systemd unit

```bash
sudo cp /opt/stock-analyzer/deploy/alphadesk-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable alphadesk-worker.service
sudo systemctl start alphadesk-worker.service
```

## 7. Verify

```bash
# Service status
sudo systemctl status alphadesk-worker.service

# Live logs
sudo journalctl -u alphadesk-worker.service -f

# Heartbeat (updated each loop; check ts is recent and running=true)
cat /run/alphadesk/heartbeat.json
```

A healthy start logs `Worker initialized: strategy=stable universe=... paper=True`.
Outside US market hours the worker idles and only writes heartbeats — this is expected.

## 8. Common operations

```bash
# Change strategy: edit ExecStart --strategy {stable|aggressive|hybrid}
sudo nano /etc/systemd/system/alphadesk-worker.service
sudo systemctl daemon-reload && sudo systemctl restart alphadesk-worker.service

# Stop / start
sudo systemctl stop alphadesk-worker.service
sudo systemctl start alphadesk-worker.service

# Update code
cd /opt/stock-analyzer && sudo -u alphadesk git pull
sudo -u alphadesk /opt/stock-analyzer/.venv312/bin/pip install -r requirements.txt
sudo systemctl restart alphadesk-worker.service
```

## 9. Safety checklist before starting

- [ ] `APCA_PAPER=true` in `/opt/stock-analyzer/.env`
- [ ] `.env` is `chmod 600` and owned by `alphadesk`
- [ ] Smoke test passed: `sudo -u alphadesk APCA_PAPER=true PYTHONPATH=/opt/stock-analyzer:/opt/stock-analyzer/backend /opt/stock-analyzer/.venv312/bin/python -m backend.trading.engine.worker --once`
- [ ] Heartbeat file updates each loop
- [ ] Live trading (`APCA_PAPER=false` + `--allow-live`) stays OFF until the 30-day paper Sharpe > 1.0 gate is met

## 10. Live-trading guard (reminder)

The worker refuses to start with non-paper credentials unless `--allow-live` is
explicitly passed. Keep `--allow-live` out of the systemd unit during the entire
validation period. Promoting to live is a deliberate, separate change — not a default.
