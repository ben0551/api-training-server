"""
The admin console.

Server-rendered HTML with plain forms — no build step, no JavaScript framework, works
from a phone browser. Everything a facilitator needs between sessions:

  * create an instance (from a preset, or by cloning another instance's data)
  * reset an instance's data back to its seed snapshot, in one click
  * freeze an instance into read-only mode (or freeze it permanently and let it expire)
  * create, disable, re-enable and delete API keys
  * edit the seed dataset as JSON, validated before it is saved
  * read the request log to see what participants actually did

Auth is a single password (ADMIN_PASSWORD env, or generated and printed on first boot)
behind a signed cookie. The console is assumed to be internet-reachable once routed
through the reverse proxy, so there is no "no password" mode.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from . import db
from .seed import PRESET_LABELS, get_preset, validate_dataset

HERE = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))

router = APIRouter(prefix="/admin", tags=["admin"])

COOKIE = "ts_admin"
SESSION_HOURS = 12


# --------------------------------------------------------------------------
# session
# --------------------------------------------------------------------------

def _secret() -> bytes:
    return (db.get_setting("secret_key") or "insecure").encode()


def make_session() -> str:
    expiry = int(time.time()) + SESSION_HOURS * 3600
    payload = f"{expiry}:{secrets.token_hex(8)}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        expiry, nonce, sig = raw.split(":")
    except Exception:
        return False
    expected = hmac.new(_secret(), f"{expiry}:{nonce}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected) and int(expiry) > time.time()


def require_session(request: Request) -> bool:
    return valid_session(request.cookies.get(COOKIE))


def render(request: Request, template: str, **ctx: Any) -> HTMLResponse:
    ctx.setdefault("request", request)
    ctx.setdefault("instances", db.list_instances())
    ctx.setdefault("stats", db.stats())
    ctx.setdefault("presets", PRESET_LABELS)
    ctx.setdefault("flash", request.query_params.get("msg"))
    ctx.setdefault("err", request.query_params.get("err"))
    ctx.setdefault("secure", request.headers.get("x-forwarded-proto", "http") == "https")
    return templates.TemplateResponse(request, template, ctx)


def back(url: str, msg: str | None = None, err: str | None = None) -> RedirectResponse:
    params = {}
    if msg:
        params["msg"] = msg
    if err:
        params["err"] = err
    target = url + ("?" + urlencode(params) if params else "")
    return RedirectResponse(target, status_code=303)


# --------------------------------------------------------------------------
# login
# --------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/admin"):
    if require_session(request):
        return RedirectResponse(next, status_code=303)
    generated = db.get_setting("admin_password_once")
    return render(request, "login.html", next=next, generated_password=generated)


@router.post("/login")
def login(request: Request, password: str = Form(...), next: str = Form("/admin")):
    stored = db.get_setting("admin_password_hash") or ""
    if not db.verify_password(password, stored):
        return back("/admin/login", err="That password was not accepted.")
    # the generated password is shown once and then forgotten
    if db.get_setting("admin_password_generated") == "1" and db.get_setting("admin_password_once"):
        db.set_setting("admin_password_once", "")
    resp = back(next if next.startswith("/admin") else "/admin", msg="Signed in.")
    resp.set_cookie(COOKIE, make_session(), httponly=True, samesite="lax",
                    secure=request.headers.get("x-forwarded-proto", "http") == "https",
                    max_age=SESSION_HOURS * 3600, path="/")
    return resp


@router.get("/logout")
def logout():
    resp = back("/admin/login", msg="Signed out.")
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.get("/password", response_class=HTMLResponse)
def password_form(request: Request):
    if not require_session(request):
        return RedirectResponse("/admin/login?next=/admin/password", status_code=303)
    return render(request, "password.html")


@router.post("/password")
def password_change(request: Request, current: str = Form(...), new: str = Form(...),
                    confirm: str = Form(...)):
    if not require_session(request):
        return RedirectResponse("/admin/login", status_code=303)
    if not db.verify_password(current, db.get_setting("admin_password_hash") or ""):
        return back("/admin/password", err="The current password is wrong.")
    if len(new) < 8:
        return back("/admin/password", err="Use at least 8 characters.")
    if new != confirm:
        return back("/admin/password", err="The two new passwords do not match.")
    db.set_setting("admin_password_hash", db.hash_password(new))
    db.set_setting("admin_password_generated", "0")
    db.set_setting("admin_password_once", "")
    return back("/admin", msg="Password changed.")


# --------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not require_session(request):
        return RedirectResponse("/admin/login?next=/admin", status_code=303)
    return render(request, "dashboard.html")


@router.get("/audit", response_class=HTMLResponse)
def audit_view(request: Request):
    if not require_session(request):
        return RedirectResponse("/admin/login?next=/admin/audit", status_code=303)
    return render(request, "audit.html", rows=db.recent_audit_all(200))


# --------------------------------------------------------------------------
# instances
# --------------------------------------------------------------------------

@router.get("/instances/new", response_class=HTMLResponse)
def new_instance_form(request: Request):
    if not require_session(request):
        return RedirectResponse("/admin/login?next=/admin/instances/new", status_code=303)
    return render(request, "new_instance.html")


@router.post("/instances")
def create_instance(request: Request,
                    name: str = Form(...),
                    description: str = Form(""),
                    preset: str = Form("standard"),
                    clone_from: str = Form(""),
                    persistent: str = Form(""),
                    show_keys: str = Form(""),
                    auto_reset_hours: int = Form(0)):
    if not require_session(request):
        return RedirectResponse("/admin/login", status_code=303)
    if not name.strip():
        return back("/admin/instances/new", err="Give the instance a name.")
    instance_id = db.create_instance(
        name.strip(), description=description.strip(), preset=preset,
        persistent=bool(persistent), show_keys=bool(show_keys),
        auto_reset_hours=max(0, int(auto_reset_hours or 0)),
        clone_keys_from=int(clone_from) if clone_from.isdigit() else None)
    return back(f"/admin/instances/{instance_id}", msg="Instance created.")


@router.get("/instances/{instance_id}", response_class=HTMLResponse)
def instance_detail(request: Request, instance_id: int):
    if not require_session(request):
        return RedirectResponse(f"/admin/login?next=/admin/instances/{instance_id}", status_code=303)
    instance = db.get_instance(instance_id)
    if not instance:
        return back("/admin", err="No such instance.")
    return render(request, "instance.html", instance=instance,
                  keys=db.list_keys(instance_id), audit=db.recent_audit(instance_id, 12),
                  counts={t: len(db.fetch_rows(instance_id, t))
                          for t in ("customers", "applications", "loans", "payments")},
                  base_url=public_base(request) + f"/t/{instance['slug']}")


@router.post("/instances/{instance_id}/action")
def instance_action(request: Request, instance_id: int, action: str = Form(...)):
    if not require_session(request):
        return RedirectResponse("/admin/login", status_code=303)
    instance = db.get_instance(instance_id)
    if not instance:
        return back("/admin", err="No such instance.")

    if action == "reset":
        db.load_instance_data(instance_id, json.loads(instance["seed_json"]))
        return back(f"/admin/instances/{instance_id}",
                    msg="Data reset to the seed snapshot. Payments, new applications and "
                        "idempotency keys are gone.")
    if action == "reseed":
        data = get_preset(instance["preset"])
        db.update_instance(instance_id, seed_json=json.dumps(data))
        db.load_instance_data(instance_id, data)
        return back(f"/admin/instances/{instance_id}",
                    msg=f"Regenerated the '{instance['preset']}' dataset and reset the instance.")
    if action == "snapshot":
        db.update_instance(instance_id, seed_json=json.dumps(db.snapshot_dataset(instance_id)))
        return back(f"/admin/instances/{instance_id}",
                    msg="Current data saved as the new seed snapshot. 'Reset' will now restore "
                        "to this state.")
    if action == "freeze":
        db.update_instance(instance_id, read_only=1)
        return back(f"/admin/instances/{instance_id}", msg="Frozen — every write now returns 403.")
    if action == "unfreeze":
        db.update_instance(instance_id, read_only=0)
        return back(f"/admin/instances/{instance_id}", msg="Unfrozen — writes allowed again.")
    if action == "archive":
        db.update_instance(instance_id, archived=1)
        return back("/admin", msg=f"Archived '{instance['slug']}'. Its URL now returns 404.")
    if action == "unarchive":
        db.update_instance(instance_id, archived=0)
        return back(f"/admin/instances/{instance_id}", msg="Restored.")
    if action == "toggle_keys_page":
        db.update_instance(instance_id, show_keys=0 if instance["show_keys"] else 1)
        state = "shown" if not instance["show_keys"] else "hidden"
        return back(f"/admin/instances/{instance_id}", msg=f"Keys are now {state} on the share page.")
    if action == "clear_audit":
        db.clear_audit(instance_id)
        return back(f"/admin/instances/{instance_id}", msg="Request log cleared.")
    if action == "new_secret":
        secret = secrets.token_urlsafe(18)
        db.update_instance(instance_id, client_secret=secret)
        return back(f"/admin/instances/{instance_id}",
                    msg=f"New client secret: {secret} — update anything that used the old one.")
    if action == "delete":
        slug = instance["slug"]
        db.delete_instance(instance_id)
        return back("/admin", msg=f"Deleted '{slug}' and all of its data.")
    return back(f"/admin/instances/{instance_id}", err=f"Unknown action '{action}'.")


# --------------------------------------------------------------------------
# keys
# --------------------------------------------------------------------------

@router.get("/instances/{instance_id}/keys", response_class=HTMLResponse)
def keys_view(request: Request, instance_id: int):
    if not require_session(request):
        return RedirectResponse(f"/admin/login?next=/admin/instances/{instance_id}/keys", status_code=303)
    instance = db.get_instance(instance_id)
    if not instance:
        return back("/admin", err="No such instance.")
    return render(request, "keys.html", instance=instance, keys=db.list_keys(instance_id),
                  base_url=public_base(request) + f"/t/{instance['slug']}")


@router.post("/instances/{instance_id}/keys")
def create_key_route(request: Request, instance_id: int,
                     label: str = Form(...), scope: str = Form("read"),
                     key: str = Form("")):
    if not require_session(request):
        return RedirectResponse("/admin/login", status_code=303)
    if scope not in ("read", "write"):
        return back(f"/admin/instances/{instance_id}/keys", err="Scope must be read or write.")
    value = db.create_key(instance_id, label.strip() or "custom", scope=scope,
                          role="custom", key=key.strip() or None)
    return back(f"/admin/instances/{instance_id}/keys", msg=f"Key created: {value}")


@router.post("/instances/{instance_id}/keys/{key_id}/{action}")
def key_action(request: Request, instance_id: int, key_id: int, action: str):
    if not require_session(request):
        return RedirectResponse("/admin/login", status_code=303)
    if action == "enable":
        db.set_key_enabled(instance_id, key_id, True)
        return back(f"/admin/instances/{instance_id}/keys", msg="Key enabled.")
    if action == "disable":
        db.set_key_enabled(instance_id, key_id, False)
        return back(f"/admin/instances/{instance_id}/keys",
                    msg="Key disabled — it now returns 401 API_KEY_DISABLED.")
    if action == "delete":
        db.delete_key(instance_id, key_id)
        return back(f"/admin/instances/{instance_id}/keys", msg="Key deleted.")
    return back(f"/admin/instances/{instance_id}/keys", err=f"Unknown action '{action}'.")


# --------------------------------------------------------------------------
# data editor
# --------------------------------------------------------------------------

@router.get("/instances/{instance_id}/data", response_class=HTMLResponse)
def data_view(request: Request, instance_id: int):
    if not require_session(request):
        return RedirectResponse(f"/admin/login?next=/admin/instances/{instance_id}/data", status_code=303)
    instance = db.get_instance(instance_id)
    if not instance:
        return back("/admin", err="No such instance.")
    return render(request, "data.html", instance=instance,
                  dataset_json=json.dumps(json.loads(instance["seed_json"]), indent=2),
                  live_json=json.dumps(db.snapshot_dataset(instance_id), indent=2)[:1],
                  counts={t: len(db.fetch_rows(instance_id, t))
                          for t in ("customers", "applications", "loans", "payments")})


@router.post("/instances/{instance_id}/data")
def data_save(request: Request, instance_id: int, payload: str = Form(...),
              mode: str = Form("seed")):
    if not require_session(request):
        return RedirectResponse("/admin/login", status_code=303)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        return back(f"/admin/instances/{instance_id}/data",
                    err=f"That is not valid JSON: line {exc.lineno}, column {exc.colno} — {exc.msg}")
    problems = validate_dataset(data)
    if problems:
        return back(f"/admin/instances/{instance_id}/data",
                    err="Not saved. " + " | ".join(problems[:6]))
    if mode == "live":
        db.load_instance_data(instance_id, data)
        db.update_instance(instance_id, seed_json=json.dumps(data))
        return back(f"/admin/instances/{instance_id}",
                    msg="Dataset applied to the live instance and saved as its seed snapshot.")
    db.update_instance(instance_id, seed_json=json.dumps(data))
    return back(f"/admin/instances/{instance_id}/data",
                msg="Saved as the seed snapshot. Press 'Reset data' to load it into the instance.")


@router.get("/instances/{instance_id}/data.json")
def data_download(request: Request, instance_id: int, which: str = "seed"):
    if not require_session(request):
        return RedirectResponse("/admin/login", status_code=303)
    instance = db.get_instance(instance_id)
    if not instance:
        return JSONResponse({"error": "no such instance"}, status_code=404)
    data = json.loads(instance["seed_json"]) if which == "seed" else db.snapshot_dataset(instance_id)
    filename = f"{instance['slug']}-{which}.json"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def public_base(request: Request) -> str:
    """The URL a participant should use, honouring the proxy's forwarded headers."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"
