# LendFast training API server

A hosted, multi-instance version of the mock banking API used in the
**API Fundamentals for Finance & Lending** course — so participants never have to run
anything locally. They get a URL and a key; you get a console.

It is the same 15 endpoints the course teaches, with the same response shapes, the same
error codes and the same deliberate teaching behaviours. The course's Postman collection
runs against a hosted instance **completely unchanged** — you only change one variable,
`baseUrl`.

```
Participant  →  https://api-training.example.com/t/march-2026-cohort/v1/customers
Facilitator  →  https://api-training.example.com/admin
```

---

## What it does

| Requirement | How this server meets it |
| --- | --- |
| **API server** | Every instance is served at `/t/<slug>/` with all 15 endpoints, per-instance Swagger docs at `/t/<slug>/docs`, and a health check |
| **Reset data** | One button per instance. Restores from that instance's seed snapshot — new applications, payments and idempotency keys all disappear |
| **Long-lived instances** | Instances persist across restarts, can auto-reset themselves every N hours so each cohort starts clean, and can be frozen read-only or archived without deleting anything |
| **Manage keys** | Create keys (read or write scope, generated or hand-picked), disable/enable/delete them, see when each was last used |
| **Customise the data** | Edit the instance's dataset as JSON in the console, validated before saving. Presets: standard (the course data), small, or large (60 customers / ~90 applications / ~80 loans for pagination exercises). Download and re-upload as JSON |

Plus: a request log per instance, a participant share page that lists the base URL and
keys, and an OAuth2 client-credentials endpoint with a per-instance client secret.

---

## Quick start

```bash
git clone https://github.com/ben0551/api-training-server.git
cd api-training-server

# set the console password BEFORE first boot, or one is generated and shown once
export ADMIN_PASSWORD='a-long-password-you-will-remember'

pip install -r requirements.txt
python3 -m uvicorn app.main:app --port 8080

# or, locally, with Docker
docker compose up -d --build
```

For a real webhost, see **Deploying to your webhost** below.

Then:

1. Open `http://localhost:8080/admin` and sign in.
2. **New instance** → give it a name (e.g. "March 2026 cohort") and a preset.
3. Copy the share URL it gives you and send it to participants.

### Verify it before you rely on it

```bash
python3 tests/verify_hosted.py http://localhost:8080 --admin-password "$ADMIN_PASSWORD"
```

That script creates a throwaway instance, runs the **entire 44-request course
collection** against it, exercises key disabling, freezing, resetting, isolation and
archiving, then deletes the instance. `23 passed, 0 failed` is what you want to see. Run
it after every deploy and before every session.

Verified against the built image: the container comes up healthy, the full verification
passes against it, and instances, keys, data, idempotency records and the request log all
survive a `docker restart`.

**Two things that will catch you out testing from another container:**

- A published port lives on the **docker host**, so `127.0.0.1:<port>` inside a different
  container is not it. Use the docker gateway: `http://172.31.0.1:<port>`.
- Port 8080 and 8099 are already taken on this host by other containers. Pick a free one
  before publishing — `docker ps --format '{{.Names}}\t{{.Ports}}'`.

---

## Deploying to your webhost

The server is a plain Python application. Two paths, both verified:

| Your host | Path | Guide |
| --- | --- | --- |
| Shared hosting with cPanel "Setup Python App", or Plesk | Passenger over **WSGI** — no port to open, no process to babysit | `deploy/cpanel.md` |
| A VPS or dedicated server you control | **uvicorn** behind nginx, managed by systemd | `deploy/vps.md` |

FastAPI is an ASGI framework, so the shared-hosting path bridges to WSGI with `a2wsgi`
(`app/wsgi.py`, and `passenger_wsgi.py` for Passenger). **That path is tested, not just
documented**: the whole application was run under a WSGI server and the full end-to-end
verification passed against it — 24 checks including the complete 44-request course
collection.

What you need to get right on either path:

- **`ADMIN_PASSWORD`** — set it before the first boot, or a random one is generated and
  shown once on the sign-in page. Put it in the host's environment variables, or in a
  `.env` file next to the project (`chmod 600`); the app reads one, and a real environment
  variable always wins over the file.
- **`DATA_DIR`** — where the SQLite database lives. On shared hosting this must be
  **outside `public_html`** and writable. Create it before the first start.
- **The document root must not be the project directory.** Nothing in the repo should be
  web-readable; only the app should be served.

Then verify the deployment for real, from your own machine:

```bash
python3 tests/verify_hosted.py https://training.zozac.com.au --admin-password '<password>'
```

`24 passed, 0 failed` means it is genuinely working. Anything else, read the host's error
log — on cPanel that is `stderr.log` in the application root.

> **Protect `/admin`.** If the domain is internet-reachable, the console can create
> instances, read every key and delete data. Put the host's own protection in front of
> `/admin` — cPanel's *Directory Privacy* or an `.htaccess` allow-list, or the nginx block
> in `deploy/nginx.conf.example` — and leave `/t/` public for participants. The console
> refuses to run with no password at all for this reason.

`docker-compose.yml` is still in the repo for **local development only**.


## Multi-tenancy: why a path prefix

Each instance is isolated by path: `/t/<slug>/...`. That choice is deliberate — it means
the existing course material needs **no changes at all**. In Postman, the
`LendFast Sandbox` environment has a single `baseUrl` variable:

```
http://localhost:8080                        →  a local instance
https://api-training.example.com/t/cohort-a  →  a hosted instance
```

Every request, test and assertion in the workbook then works exactly as written. No
subdomains, no DNS per cohort, no rewriting of exercises mid-course.

Isolation is enforced in the data layer, not the routing layer: every table is keyed by
`instance_id`, and a key is only ever looked up **within** its own instance. The same key
value (`demo-key-001`) exists in every instance and is a different credential in each.

---

## The admin console

| Page | What it is for |
| --- | --- |
| `/admin` | Every instance with its record counts, key state and last reset. Create a new one |
| `/admin/instances/<id>` | Share URL, data counts, all the actions, key summary, recent requests |
| `/admin/instances/<id>/keys` | Create, disable, re-enable and delete keys; see last-used times |
| `/admin/instances/<id>/data` | Edit the seed dataset as JSON, download it, apply it live |
| `/admin/audit` | The last 200 authenticated requests across every instance |
| `/admin/password` | Change the console password |

### Actions available on an instance

| Action | Effect |
| --- | --- |
| **Reset data to the snapshot** | Everything returns to the seeded state. Use between cohorts |
| **Snapshot current data** | Makes the *current* state the new reset point — useful after you have customised it by hand |
| **Regenerate the preset** | Rebuilds the standard/small/large dataset from scratch |
| **Freeze (read-only)** | Reads keep working, every write returns `403 INSTANCE_FROZEN`. For a demo or an assessment |
| **Archive** | The URL starts returning `404`; data and keys are kept. Restore any time |
| **Delete permanently** | Removes the instance, its data, keys and log |
| **Clear request log** | Empties the audit trail for that instance |
| **New client secret** | Rotates the OAuth client secret (the course default is `training-secret`) |
| **Hide/show keys on the share page** | Whether the participant landing page lists the keys |

---

## Customising the training data

The data editor takes the four lists the API is built on. Nothing saves unless the JSON
parses *and* every reference resolves — a payment must point at a real loan, a loan at a
real customer — so a bad edit cannot half-break an instance.

```json
{
  "customers":    [ { "id": "CUS-100001", "first_name": "…", "surname": "…", "state": "NSW",
                      "annual_income": 142000, "credit_score": 812, … } ],
  "applications": [ { "id": "APP-2026-000411", "customer_id": "CUS-100001", "product": "…",
                      "amount": 620000, "term_months": 360, "status": "APPROVED", … } ],
  "loans":        [ { "id": "LON-90001", "customer_id": "CUS-100001", "balance": 598412.77,
                      "rate_pct": 6.14, "repayment_amount": 3775.41, "status": "CURRENT", … } ],
  "payments":     [ { "id": "PAY-500001", "loan_id": "LON-90001", "amount": 3775.41, … } ]
}
```

**Keep these four properties** and every exercise in the workbook still works:

1. at least one application that is already `APPROVED` — it demonstrates the `409`
2. a loan with a **small balance** (a few thousand) — it makes "pay more than the balance"
   return `422` instead of succeeding
3. a loan with `arrears_days` above zero — so `?in_arrears=true` returns something
4. five or more customers — so pagination with `page_size=2` has several pages

Rename the products and states to match your own book and the room will engage far more.

---

## The API itself

All 15 endpoints, per-instance. Full interactive documentation is generated at
`/t/<slug>/docs`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness. No auth |
| POST | `/oauth/token` | Client-credentials token exchange |
| GET | `/v1/customers` | List customers — `page`, `page_size`, `surname`, `state`, `min_credit_score`, `sort`, `fields` |
| GET | `/v1/customers/{id}` | One customer, with a summary and links |
| GET | `/v1/applications` | List applications — `status`, `customer_id`, `product`, `submitted_after`, `min_amount` |
| GET | `/v1/applications/{id}` | One application, with status history |
| POST | `/v1/applications` | Lodge an application — **write key required** |
| PATCH | `/v1/applications/{id}/status` | Move the credit workflow on (`409` if already decided) |
| GET | `/v1/loans` | List loans — `customer_id`, `status`, `in_arrears` |
| GET | `/v1/loans/{id}` | One loan |
| GET | `/v1/loans/{id}/schedule` | Amortisation schedule — `months` |
| GET | `/v1/loans/{id}/payments` | Repayment history |
| POST | `/v1/loans/{id}/payments` | Record a repayment — send an `Idempotency-Key` |
| GET | `/v1/reports/portfolio-summary` | Portfolio aggregates — `group_by=product\|state\|status` |
| GET | `/v1/_audit` | The last 20 authenticated calls for this instance |
| POST | `/v1/_simulate/webhook` | Shows the payload an outbound webhook would carry |
| GET | `/v1/_simulate/status/{code}` | Force 400/401/403/404/429/500/503 |

Every instance is created with the three well-known training keys, so the workbook, the
slides and the Postman collection all work without edits:

| Key | Who | Scope |
| --- | --- | --- |
| `demo-key-001` | Ava Nguyen | read — GET only |
| `demo-key-002` | Ben Fischer | write — GET, POST, PATCH |
| `demo-key-003` | Service Account | read |

OAuth client secret: `training-secret` (changeable per instance from the console).

---

## Operations

**Storage.** One SQLite file at `/data/training.db`, WAL mode. Back up by copying the
file. Nothing else is required — no second container, no external database.

**Scaling.** This is a training sandbox for tens of concurrent participants, not a
production service. SQLite handles that comfortably (30 concurrent requests were tested
without a lock error). If you ever need more, the data layer is one module.

**Restarting.** Instances, keys, data and the audit log all survive a restart. Data
in memory does not — a payment posted before a restart is still there, because it is in
the database.

**Configuration.**

| Variable | Default | Meaning |
| --- | --- | --- |
| `ADMIN_PASSWORD` | *(generated, shown once)* | Console password. Set it before first boot |
| `DATA_DIR` | `/data` | Where the SQLite file lives |
| `DB_PATH` | `$DATA_DIR/training.db` | Override the file path directly |
| `RATE_LIMIT` | `5000` | Value of `X-RateLimit-Limit`, per instance per window |
| `RATE_WINDOW_MINUTES` | `15` | The window those headers describe |

**Lost the console password?** Delete the `admin_password_hash` row from the `settings`
table in the database and restart — a new one is generated and shown on the sign-in page.

**Rate-limit headers are real here.** `X-RateLimit-Remaining` is computed from that
instance's actual request count in the current window, so the course's
"read the headers" exercise shows a true number rather than a decoration.

---

## Security posture — read this

- The console is password-protected, refuses to start with no password, and sets its
  session cookie `Secure` when it sees `X-Forwarded-Proto: https`.
- **Keys are stored in plain text** in the database. That is deliberate: a facilitator has
  to read them out and hand them to a room. They protect fictional data in a sandbox and
  nothing else. Never reuse a real credential as a training key.
- The participant share page can list the keys (`show_keys`). It is on by default because
  the data is fictional and it saves a lot of classroom friction — turn it off per
  instance if you would rather distribute keys by hand.
- If the service is internet-reachable, the `/admin` path is the part worth protecting
  further. A LAN-only middleware on that router, or a second router rule that publishes
  only `/t/`, is the cheap hardening.

---

## Project layout

```
app/
  main.py          app assembly, instance headers middleware, share page
  api.py           the 15 endpoints, per-instance
  admin.py         the console (server-rendered, plain forms)
  db.py            SQLite schema and data access
  seed.py          the standard/small/large datasets + validation
  env.py           .env loader (real environment variables win)
  wsgi.py          ASGI -> WSGI bridge for shared hosting
  templates/       Jinja2 templates
  static/          one stylesheet
passenger_wsgi.py  entrypoint for cPanel / Plesk / Passenger
deploy/
  cpanel.md            shared-hosting walkthrough
  vps.md               own-server walkthrough
  training-api.service systemd unit
  nginx.conf.example   reverse proxy, with a /admin lock-down block
tests/
  verify_hosted.py     end-to-end verification against a running server
  collections/         a copy of the course collections, so the tests are standalone
Dockerfile             for local development
docker-compose.yml     for local development
requirements.txt         native hosting (uvicorn)
requirements-webhost.txt WSGI hosting (no uvicorn, adds a2wsgi)
```

---

## Licence and use

Internal training material. All customer, application, loan and payment data is
fictional. Do not put real customer data in it.
