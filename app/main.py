"""
LendFast training API server — application assembly.

  /                        landing page, links to the console
  /health                  liveness (no auth) — used by the container healthcheck
  /t/{slug}/               participant share page: base URL, keys, quick start
  /t/{slug}/docs           per-instance Swagger UI
  /t/{slug}/v1/...         the 15 training endpoints, isolated per instance
  /admin                   the facilitator console (password protected)

Multi-tenancy is a path prefix rather than a subdomain so that the course material
needs no changes: set baseUrl to https://<host>/t/<slug> in Postman and every
exercise in the workbook works exactly as written.
"""
from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import admin, api, db

HERE = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))

db.init_db()

app = FastAPI(
    title="LendFast Training API Server",
    version="1.0.0",
    description="Multi-instance sandbox API used in the API Fundamentals for Finance "
                "& Lending course. Each training instance lives at /t/{slug}/.",
    docs_url=None,
    redoc_url=None,
    openapi_url="/openapi.json",
)

app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
app.include_router(api.router)
app.include_router(admin.router)

INSTANCE_PATH = re.compile(r"^/t/([^/]+)(/.*)?$")


# --------------------------------------------------------------------------
# Every instance response carries the headers the course teaches participants to read
# --------------------------------------------------------------------------

@app.middleware("http")
async def instance_headers(request: Request, call_next):
    response = await call_next(request)

    match = INSTANCE_PATH.match(request.url.path)
    if not match:
        return response

    slug = match.group(1)
    instance = db.get_instance_by_slug(slug)
    if not instance:
        return response

    since = (datetime.now(timezone.utc).astimezone()
             - timedelta(minutes=api.RATE_WINDOW_MINUTES)).isoformat(timespec="seconds")
    used = 0
    try:
        with db.connect() as conn:
            used = conn.execute("SELECT COUNT(*) c FROM audit WHERE instance_id=? AND at > ?",
                                (instance["id"], since)).fetchone()["c"]
    except Exception:
        pass

    response.headers["X-RateLimit-Limit"] = str(api.RATE_LIMIT)
    response.headers["X-RateLimit-Remaining"] = str(max(0, api.RATE_LIMIT - used))
    response.headers["X-RateLimit-Window"] = f"{api.RATE_WINDOW_MINUTES}m"
    response.headers["X-Request-Id"] = "req_" + secrets.token_hex(6)
    response.headers["X-Environment"] = "training-sandbox"
    response.headers["X-Training-Instance"] = slug

    # log authenticated API traffic, the way the course's /v1/_audit exercise expects
    label = getattr(request.state, "key_label", None)
    if label and request.url.path.startswith(f"/t/{slug}/v1"):
        try:
            db.log_request(instance["id"], request.method, request.url.path, label, response.status_code)
        except Exception:
            pass
    return response


# --------------------------------------------------------------------------
# Root, health, participant landing page
# --------------------------------------------------------------------------

@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "service": "lendfast-training-api-server", "version": "1.0.0",
            "instances": db.stats()["active_instances"],
            "server_time": datetime.now(timezone.utc).isoformat(timespec="seconds")}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root(request: Request):
    return templates.TemplateResponse(request, "root.html", {"request": request})


@app.get("/t/{slug}", response_class=HTMLResponse, include_in_schema=False)
def landing(request: Request, slug: str):
    instance = db.get_instance_by_slug(slug)
    if not instance or instance.get("archived"):
        return HTMLResponse(
            "<html><body style='font-family:system-ui;padding:3rem;background:#0e1726;color:#e8eef7'>"
            "<h1>404 — no such training instance</h1>"
            "<p>Check the link, or ask the facilitator for the correct base URL.</p>"
            "</body></html>", status_code=404)
    base = admin.public_base(request) + f"/t/{instance['slug']}"
    keys = [k for k in db.list_keys(instance["id"]) if k["enabled"]] if instance["show_keys"] else []
    return templates.TemplateResponse(request, "landing.html", {
        "request": request, "instance": instance, "base_url": base, "keys": keys,
        "counts": {t: len(db.fetch_rows(instance["id"], t))
                   for t in ("customers", "applications", "loans", "payments")},
        "secret": instance["client_secret"] if instance["show_keys"] else None,
    })


@app.get("/instances", include_in_schema=False)
def instances_index() -> JSONResponse:
    """A machine-readable list of active instances, for scripting a reset across all of them."""
    rows = [{"slug": r["slug"], "name": r["name"], "archived": bool(r["archived"]),
             "frozen": bool(r["read_only"]), "base_url": f"/t/{r['slug']}"}
            for r in db.list_instances()]
    return JSONResponse({"instances": rows})
