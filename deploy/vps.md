# Deploying on a VPS or dedicated server

You control the machine, so run the app natively under uvicorn behind nginx. This is the
better path if you have it — streaming works, and you manage the process properly.

## 1. Code and virtualenv

```bash
sudo mkdir -p /opt/api-training-server /var/lib/api-training
sudo chown "$USER" /opt/api-training-server /var/lib/api-training
git clone https://github.com/ben0551/api-training-server.git /opt/api-training-server
cd /opt/api-training-server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`requirements.txt` includes uvicorn — that is the server on this path.

## 2. Environment

```bash
sudo tee /etc/api-training.env >/dev/null <<'EOF'
ADMIN_PASSWORD=a-long-password-you-will-remember
DATA_DIR=/var/lib/api-training
RATE_LIMIT=5000
RATE_WINDOW_MINUTES=15
EOF
sudo chmod 600 /etc/api-training.env
```

## 3. systemd unit

```bash
sudo cp /opt/api-training-server/deploy/training-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now training-api
systemctl status training-api --no-pager
curl -s http://127.0.0.1:8080/health
```

## 4. nginx reverse proxy

```bash
sudo cp /opt/api-training-server/deploy/nginx.conf.example \
        /etc/nginx/sites-available/api-training
sudo ln -s /etc/nginx/sites-available/api-training /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d training.zozac.com.au
```

## 5. Verify end to end

```bash
cd /opt/api-training-server
python3 tests/verify_hosted.py https://training.zozac.com.au --admin-password '<password>'
```

Expect `24 passed, 0 failed`.

## 6. Protect the console

nginx example includes a commented block that restricts `/admin` to your own addresses.
Turn it on unless you are happy with the console being world-reachable behind its
password — it can create instances, read every key and delete data.

## Backups

```bash
# one file is the whole system
sqlite3 /var/lib/api-training/training.db ".backup '/backup/training-$(date +%F).db'"
```

or just copy `training.db` (and its `-wal` file) while the service is stopped.
