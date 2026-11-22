# Deploying on cPanel shared hosting

For hosts that offer **Setup Python App** (cPanel) or **Python** (Plesk). The app runs
under Passenger over WSGI, so there is no port to open and no process to babysit.

## 1. Upload the code

Put the project somewhere **outside** `public_html` — it must not be web-readable:

```
/home/<user>/apps/api-training-server
```

Git is easiest if the host has it:

```bash
cd ~/apps
git clone https://github.com/ben0551/api-training-server.git
```

Use a deploy key or a personal access token; the repo is private.

## 2. Create the Python application

cPanel → **Setup Python App** → *Create Application*:

| Field | Value |
| --- | --- |
| Python version | 3.11 or newer |
| Application root | `apps/api-training-server` |
| Application URL | the domain or subdomain you want, e.g. `training.zozac.com.au` |
| Application startup file | `passenger_wsgi.py` |
| Application Entry point | `application` |

Then add the environment variables under *Environment variables* (or put them in a `.env`
file — the app reads one from its own directory, see step 4):

```
ADMIN_PASSWORD=a-long-password-you-will-remember
DATA_DIR=/home/<user>/apps/api-training-data
RATE_LIMIT=5000
RATE_WINDOW_MINUTES=15
```

`DATA_DIR` must be **outside** `public_html` and writable — it holds the SQLite database.
Create it before the first start:

```bash
mkdir -p ~/apps/api-training-data
```

## 3. Install the dependencies

Use the virtualenv cPanel creates (it prints the path, e.g.
`/home/<user>/virtualenv/api-training-server/3.11/bin/activate`):

```bash
source /home/<user>/virtualenv/api-training-server/3.11/bin/activate
pip install -r ~/apps/api-training-server/requirements-webhost.txt
```

Do **not** install `uvicorn` here — Passenger is the server. (`a2wsgi` is what adapts the
ASGI app to WSGI; it is in `requirements-webhost.txt`.)

## 4. Optional but recommended: a .env file

Instead of the panel's environment variables, put them in
`~/apps/api-training-server/.env` and lock it down:

```bash
cat > ~/apps/api-training-server/.env <<'EOF'
ADMIN_PASSWORD=a-long-password-you-will-remember
DATA_DIR=/home/<user>/apps/api-training-data
EOF
chmod 600 ~/apps/api-training-server/.env
```

The app reads it on boot and real environment variables still win over it.

## 5. Restart and verify

cPanel → Setup Python App → **Restart**. Then:

```bash
curl -s https://training.zozac.com.au/health
# {"status":"ok","service":"lendfast-training-api-server",...}
```

If that fails, check `~/apps/api-training-server/stderr.log` (Passenger writes there) and
the domain's error log in cPanel.

## 6. Create the first instance and verify it properly

```bash
curl -s -X POST -u 'admin:<password>' ...
```

easier: open `https://training.zozac.com.au/admin`, sign in, and create an instance. Then
run the full end-to-end check from your machine:

```bash
python3 tests/verify_hosted.py https://training.zozac.com.au \
    --admin-password '<your password>'
```

`24 passed, 0 failed` means the deployment is genuinely working — including the whole
44-request course collection against a throwaway instance.

## Notes and limits of shared hosting

- **One SQLite file, WAL mode.** Fine for a training sandbox. Passenger may run a small
  number of worker processes; SQLite handles that, and a training session is not a
  high-write workload.
- **No streaming responses.** The WSGI bridge buffers, so long-lived server-sent events
  would not work. This API has none — every response is a normal JSON body.
- **Back up by copying `DATA_DIR/training.db`.** That file is every instance, key and
  record.
- **Lost the console password?** Delete the `admin_password_hash` row from the `settings`
  table (any SQLite client) and restart; a new password is generated and shown once on the
  sign-in page.
- **`/admin` is the part worth protecting.** If the domain is internet-reachable, put the
  host's own password protection (cPanel's *Directory Privacy*, or an `.htaccess`
  allow-list) in front of `/admin`, and leave `/t/` public for participants.
