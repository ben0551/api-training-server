"""
The instance-scoped API — the same 15 endpoints the course teaches, now backed by
SQLite and isolated per training instance.

Everything is mounted under /t/{slug}/, so the Postman collections and the workbook
need no changes at all: point baseUrl at https://<host>/t/<slug> and every exercise
in the course works exactly as written.

Response shapes, error codes and headers are deliberately identical to the
single-file version in the course pack — the 44-request verification suite passes
against this unchanged.
"""
from __future__ import annotations

import math
import os
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Body, FastAPI, Header, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import db

RATE_LIMIT = int(os.getenv("RATE_LIMIT", "5000"))       # per instance, per 15 minutes
RATE_WINDOW_MINUTES = int(os.getenv("RATE_WINDOW_MINUTES", "15"))

router = APIRouter(prefix="/t/{slug}", tags=["training"])


# --------------------------------------------------------------------------
# Instance resolution + auth
# --------------------------------------------------------------------------

def get_instance_or_404(slug: str) -> dict[str, Any]:
    instance = db.get_instance_by_slug(slug)
    if not instance or instance.get("archived"):
        raise HTTPException(status_code=404, detail={
            "code": "INSTANCE_NOT_FOUND",
            "message": f"No training instance with the name '{slug}'.",
            "hint": "Ask the facilitator for the correct base URL.",
        })
    if db.maybe_auto_reset(instance):
        instance = db.get_instance(instance["id"]) or instance
    return instance


def authenticate(request: Request, instance: dict, x_api_key: str | None,
                 authorization: str | None, need_write: bool = False) -> dict[str, Any]:
    """401 = who are you?  403 = I know you, and you still may not do that.

    Accepts either  X-API-Key: demo-key-002  or  Authorization: Bearer <token>
    where <token> came from POST /t/<slug>/oauth/token.
    """
    resolved = x_api_key
    if not resolved and authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            resolved = db.resolve_token(instance["id"], value)
            if resolved is None:
                raise HTTPException(
                    status_code=401,
                    detail={"code": "INVALID_TOKEN",
                            "message": "That bearer token is unknown or has expired.",
                            "hint": "Get a new one from POST /oauth/token"},
                    headers={"WWW-Authenticate": "Bearer"})

    if not resolved:
        raise HTTPException(
            status_code=401,
            detail={"code": "MISSING_API_KEY",
                    "message": "Send your API key in the X-API-Key header.",
                    "hint": "In Postman: Headers tab -> X-API-Key -> demo-key-001"},
            headers={"WWW-Authenticate": "ApiKey"})

    row = db.find_key(instance["id"], resolved)
    if not row:
        raise HTTPException(status_code=401, detail={
            "code": "INVALID_API_KEY",
            "message": "That API key is not recognised for this training instance.",
            "hint": "Keys are scoped to one instance — check the base URL as well as the key."})

    if not row["enabled"]:
        raise HTTPException(status_code=401, detail={
            "code": "API_KEY_DISABLED",
            "message": f"The key '{row['label'] or row['key'][:12]}' has been disabled by the facilitator.",
            "hint": "Ask for a new key, or use one of the other training keys."})

    if need_write:
        if instance.get("read_only"):
            raise HTTPException(status_code=403, detail={
                "code": "INSTANCE_FROZEN",
                "message": "This training instance is in read-only mode right now.",
                "hint": "The facilitator can unfreeze it from the admin console."})
        if row["scope"] != "write":
            raise HTTPException(status_code=403, detail={
                "code": "INSUFFICIENT_SCOPE",
                "message": f"{row['label'] or 'This key'} holds a '{row['scope']}' key. "
                           f"Writing needs a 'write' key.",
                "hint": "Try X-API-Key: demo-key-002"})

    db.touch_key(instance["id"], resolved)
    request.state.key_label = row["label"] or row["key"][:12]
    return row


def paginate(items: list[dict], page: int, page_size: int, *, base: str, query: dict) -> dict:
    total = len(items)
    total_pages = max(1, math.ceil(total / page_size))
    if page < 1 or page > total_pages:
        raise HTTPException(status_code=400, detail={
            "code": "PAGE_OUT_OF_RANGE",
            "message": f"page must be between 1 and {total_pages}"})
    start = (page - 1) * page_size
    window = items[start:start + page_size]

    def link(p: int) -> str:
        q = "&".join(f"{k}={v}" for k, v in {**query, "page": p, "page_size": page_size}.items()
                     if v is not None)
        return f"{base}?{q}"

    return {"meta": {"page": page, "page_size": page_size, "total": total,
                     "total_pages": total_pages, "count": len(window),
                     "next": link(page + 1) if page < total_pages else None,
                     "previous": link(page - 1) if page > 1 else None},
            "data": window}


def project(item: dict, fields: str | None) -> dict:
    if not fields:
        return item
    wanted = [f.strip() for f in fields.split(",") if f.strip()]
    try:
        return {k: item[k] for k in wanted}
    except KeyError as exc:
        raise HTTPException(status_code=400, detail={
            "code": "UNKNOWN_FIELD", "message": f"No such field: {exc.args[0]}",
            "hint": f"Available: {', '.join(item.keys())}"}) from exc


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

class ApplicationCreate(BaseModel):
    customer_id: str = Field(examples=["CUS-100001"])
    product: Literal["Fixed Rate Home Loan", "Variable Rate Home Loan", "Investment Loan", "Personal Loan"]
    amount: float = Field(gt=1000, le=5_000_000)
    term_months: int = Field(ge=6, le=480)
    purpose: Literal["Purchase", "Refinance", "Investment", "Car", "Other"]
    rate_pct: float | None = Field(default=None, ge=0, le=30)

    @field_validator("amount")
    @classmethod
    def round_money(cls, v: float) -> float:
        return round(v, 2)


class StatusUpdate(BaseModel):
    status: Literal["IN_REVIEW", "APPROVED", "DECLINED", "REFERRED"]
    note: str | None = Field(default=None, max_length=280)


class PaymentCreate(BaseModel):
    amount: float = Field(gt=0, le=100_000)
    method: Literal["DIRECT_DEBIT", "BPAY", "CARD", "TRANSFER", "CASH"]
    reference: str = Field(min_length=2, max_length=30)
    paid_at: datetime | None = None


# --------------------------------------------------------------------------
# Health, docs, landing
# --------------------------------------------------------------------------

@router.get("/health", summary="Is this training instance up?")
def health(slug: str) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    return {"status": "ok", "service": "lendfast-training-api", "version": "1.0.0",
            "instance": instance["slug"], "instance_name": instance["name"],
            "server_time": datetime.now(timezone.utc).isoformat(timespec="seconds")}


@router.get("/openapi.json", include_in_schema=False)
def instance_openapi(slug: str) -> JSONResponse:
    """The API description with this instance's base URL baked in, so the docs page
    is clickable without the reader having to know the path prefix."""
    spec = dict(app_openapi())
    # the app's own spec uses the literal "/t/{slug}" prefix, so strip that and then
    # point the generated docs at this instance's real path
    template_prefix = "/t/{slug}"
    spec["paths"] = {p[len(template_prefix):]: v
                     for p, v in spec["paths"].items() if p.startswith(template_prefix)}
    spec["servers"] = [{"url": f"/t/{slug}"}]
    spec["info"] = dict(spec["info"], title=f"LendFast Training API — {slug}")
    return JSONResponse(spec)


@router.get("/docs", include_in_schema=False)
def instance_docs(slug: str) -> HTMLResponse:
    get_instance_or_404(slug)
    return get_swagger_ui_html(
        openapi_url=f"/t/{slug}/openapi.json",
        title=f"LendFast Training API — {slug}")


def app_openapi() -> dict:
    from .main import app
    return app.openapi()


# --------------------------------------------------------------------------
# Utility endpoints
# --------------------------------------------------------------------------

@router.get("/v1/_audit", summary="The last requests this instance's server saw")
def audit(slug: str, request: Request,
          x_api_key: str | None = Header(default=None),
          authorization: str | None = Header(default=None)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    authenticate(request, instance, x_api_key, authorization)
    rows = db.recent_audit(instance["id"], limit=20)
    return {"count": len(rows), "data": rows}


@router.get("/v1/_simulate/status/{code}", summary="Force an HTTP status code")
def simulate_status(slug: str, code: int, request: Request,
                    x_api_key: str | None = Header(default=None),
                    authorization: str | None = Header(default=None)) -> JSONResponse:
    instance = get_instance_or_404(slug)
    authenticate(request, instance, x_api_key, authorization)
    bodies = {
        400: {"code": "BAD_REQUEST", "message": "The request was malformed."},
        401: {"code": "UNAUTHORISED", "message": "Token expired or missing."},
        403: {"code": "FORBIDDEN", "message": "Your role cannot access this resource."},
        404: {"code": "NOT_FOUND", "message": "No such resource."},
        409: {"code": "CONFLICT", "message": "The record's state does not allow that."},
        422: {"code": "UNPROCESSABLE", "message": "That failed a business rule."},
        429: {"code": "RATE_LIMITED", "message": "Slow down. Retry after 2 seconds.",
              "retry_after_seconds": 2},
        500: {"code": "INTERNAL_ERROR", "message": "Unexpected server error.",
              "reference": "ERR-" + secrets.token_hex(4).upper()},
        503: {"code": "SERVICE_UNAVAILABLE", "message": "Downstream core banking system offline."},
    }
    if code not in bodies:
        raise HTTPException(status_code=400, detail={
            "code": "UNSUPPORTED_SIMULATION", "message": f"Cannot simulate {code}"})
    hdrs = {"X-Request-Id": "req_" + secrets.token_hex(6)}
    if code == 429:
        hdrs["Retry-After"] = "2"
    return JSONResponse(status_code=code, content={"error": bodies[code]}, headers=hdrs)


@router.post("/v1/_simulate/webhook", summary="Show the shape of an outbound webhook")
def simulate_webhook(slug: str, request: Request,
                     application_id: str = Body(embed=True, examples=["APP-2026-000415"]),
                     callback_url: str = Body(embed=True, examples=["https://webhook.site/your-url"]),
                     x_api_key: str | None = Header(default=None),
                     authorization: str | None = Header(default=None)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    authenticate(request, instance, x_api_key, authorization)
    row = db.fetch_one(instance["id"], "applications", application_id)
    if not row:
        raise HTTPException(status_code=404, detail={
            "code": "APPLICATION_NOT_FOUND", "message": f"No application {application_id}"})
    return {
        "message": f"In production the bank would POST the payload below to {callback_url} "
                   "the moment a decision lands. Nothing was actually sent.",
        "would_post_to": callback_url,
        "headers": {"Content-Type": "application/json",
                    "X-LendFast-Signature": "sha256=9f2b... (HMAC of the body)"},
        "body": {"event": "application.decision.ready",
                 "event_id": "evt_" + secrets.token_hex(5),
                 "occurred_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                 "data": {"application_id": row["id"], "status": row["status"],
                          "amount": row["amount"], "product": row["product"],
                          "customer_id": row["customer_id"]}},
    }


# --------------------------------------------------------------------------
# Auth — the OAuth2 client-credentials dance, simplified
# --------------------------------------------------------------------------

@router.post("/oauth/token", summary="Exchange client credentials for a bearer token")
def token(slug: str,
          grant_type: str = Body(embed=True),
          client_id: str = Body(embed=True),
          client_secret: str = Body(embed=True)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    if grant_type != "client_credentials":
        raise HTTPException(status_code=400, detail={
            "code": "UNSUPPORTED_GRANT_TYPE", "message": "Only client_credentials is supported here."})
    row = db.find_key(instance["id"], client_id)
    if not row or not row["enabled"] or client_secret != instance["client_secret"]:
        raise HTTPException(status_code=401, detail={
            "code": "INVALID_CLIENT", "message": "client_id/client_secret pair rejected."})
    tok = db.issue_token(instance["id"], client_id, ttl_seconds=3600)
    return {"access_token": tok, "token_type": "Bearer", "expires_in": 3600,
            "scope": row["scope"], "issued_for": row["label"] or client_id}


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------

@router.get("/v1/customers", summary="List customers (paginated)")
def list_customers(slug: str, request: Request,
                   page: int = 1, page_size: int = 3,
                   surname: str | None = None,
                   state: str | None = None,
                   min_credit_score: int | None = None,
                   sort: str = "surname",
                   fields: str | None = None,
                   x_api_key: str | None = Header(default=None),
                   authorization: str | None = Header(default=None)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    authenticate(request, instance, x_api_key, authorization)
    rows = db.fetch_rows(instance["id"], "customers")
    if surname:
        rows = [r for r in rows if surname.lower() in str(r.get("surname", "")).lower()]
    if state:
        rows = [r for r in rows if str(r.get("state", "")).upper() == state.upper()]
    if min_credit_score is not None:
        rows = [r for r in rows if (r.get("credit_score") or 0) >= min_credit_score]
    if rows and sort not in rows[0]:
        raise HTTPException(status_code=400, detail={
            "code": "UNKNOWN_SORT_FIELD", "message": f"Cannot sort by '{sort}'"})
    rows.sort(key=lambda r: r.get(sort) or "")
    result = paginate(rows, page, page_size, base="/v1/customers",
                      query={"surname": surname, "state": state, "sort": sort, "fields": fields})
    result["data"] = [project(r, fields) for r in result["data"]]
    return result


@router.get("/v1/customers/{customer_id}", summary="Get one customer")
def get_customer(slug: str, customer_id: str, request: Request,
                 x_api_key: str | None = Header(default=None),
                 authorization: str | None = Header(default=None)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    authenticate(request, instance, x_api_key, authorization)
    row = db.fetch_one(instance["id"], "customers", customer_id)
    if not row:
        raise HTTPException(status_code=404, detail={
            "code": "CUSTOMER_NOT_FOUND", "message": f"No customer with id {customer_id}"})
    apps = [a for a in db.fetch_rows(instance["id"], "applications") if a["customer_id"] == row["id"]]
    loans = [l for l in db.fetch_rows(instance["id"], "loans") if l["customer_id"] == row["id"]]
    return {"data": row,
            "links": {"applications": f"/v1/applications?customer_id={row['id']}",
                      "loans": f"/v1/loans?customer_id={row['id']}"},
            "summary": {"application_count": len(apps),
                        "open_applications": len([a for a in apps
                                                  if a["status"] not in ("APPROVED", "DECLINED")]),
                        "active_loans": len(loans),
                        "total_balance": round(sum(l["balance"] for l in loans), 2)}}


# --------------------------------------------------------------------------
# Applications (originations)
# --------------------------------------------------------------------------

@router.get("/v1/applications", summary="List loan applications")
def list_applications(slug: str, request: Request,
                      page: int = 1, page_size: int = 5,
                      status: str | None = None,
                      customer_id: str | None = None,
                      product: str | None = None,
                      submitted_after: date | None = None,
                      min_amount: float | None = None,
                      x_api_key: str | None = Header(default=None),
                      authorization: str | None = Header(default=None)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    authenticate(request, instance, x_api_key, authorization)
    rows = db.fetch_rows(instance["id"], "applications")
    if status:
        wanted = [s.strip().upper() for s in status.split(",")]
        rows = [r for r in rows if r["status"] in wanted]
    if customer_id:
        rows = [r for r in rows if r["customer_id"] == customer_id]
    if product:
        rows = [r for r in rows if product.lower() in r["product"].lower()]
    if submitted_after:
        rows = [r for r in rows if r["submitted_at"][:10] > submitted_after.isoformat()]
    if min_amount is not None:
        rows = [r for r in rows if r["amount"] >= min_amount]
    rows.sort(key=lambda r: r["submitted_at"], reverse=True)
    return paginate(rows, page, page_size, base="/v1/applications",
                    query={"status": status, "customer_id": customer_id, "product": product})


@router.get("/v1/applications/{application_id}", summary="Get one application")
def get_application(slug: str, application_id: str, request: Request,
                    x_api_key: str | None = Header(default=None),
                    authorization: str | None = Header(default=None)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    authenticate(request, instance, x_api_key, authorization)
    row = db.fetch_one(instance["id"], "applications", application_id)
    if not row:
        raise HTTPException(status_code=404, detail={
            "code": "APPLICATION_NOT_FOUND", "message": f"No application with id {application_id}"})
    hist = [{"at": row["submitted_at"], "status": "SUBMITTED", "by": "online-channel"}]
    if row["status"] != "SUBMITTED":
        hist.append({"at": row.get("decision_at") or row["submitted_at"],
                     "status": row["status"], "by": row.get("assessor") or "system"})
    return {"data": row, "status_history": hist,
            "links": {"customer": f"/v1/customers/{row['customer_id']}"}}


def _next_id(rows: list[dict], prefix: str, digits: int, floor: int) -> str:
    numbers = []
    for r in rows:
        tail = str(r.get("id", ""))[len(prefix):]
        if tail.isdigit():
            numbers.append(int(tail))
    return f"{prefix}{max(max(numbers) + 1 if numbers else 0, floor):0{digits}d}"


@router.post("/v1/applications", status_code=201, summary="Submit a new loan application")
def create_application(slug: str, payload: ApplicationCreate, request: Request,
                       x_api_key: str | None = Header(default=None),
                       authorization: str | None = Header(default=None)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    who = authenticate(request, instance, x_api_key, authorization, need_write=True)
    if not db.fetch_one(instance["id"], "customers", payload.customer_id):
        raise HTTPException(status_code=422, detail={
            "code": "CUSTOMER_NOT_FOUND",
            "message": f"customer_id {payload.customer_id} does not exist.",
            "hint": "Create nothing — GET /v1/customers first and use a real id."})
    existing = db.fetch_rows(instance["id"], "applications")
    new = {"id": _next_id(existing, "APP-2026-", 6, 411), "customer_id": payload.customer_id,
           "product": payload.product, "amount": round(payload.amount, 2),
           "term_months": payload.term_months,
           "rate_pct": payload.rate_pct if payload.rate_pct is not None else 6.24,
           "purpose": payload.purpose, "status": "SUBMITTED",
           "submitted_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
           "decision_at": None, "assessor": None}
    db.upsert_row(instance["id"], "applications", new)
    return JSONResponse(status_code=201, content={
        "data": new,
        "links": {"self": f"/v1/applications/{new['id']}",
                  "customer": f"/v1/customers/{new['customer_id']}"},
        "receipt": {"received_by": who["label"] or who["key"][:12], "channel": "training-sandbox"}},
        headers={"Location": f"/v1/applications/{new['id']}"})


@router.patch("/v1/applications/{application_id}/status",
              summary="Move an application through the credit workflow")
def update_status(slug: str, application_id: str, payload: StatusUpdate, request: Request,
                  x_api_key: str | None = Header(default=None),
                  authorization: str | None = Header(default=None)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    who = authenticate(request, instance, x_api_key, authorization, need_write=True)
    row = db.fetch_one(instance["id"], "applications", application_id)
    if not row:
        raise HTTPException(status_code=404, detail={
            "code": "APPLICATION_NOT_FOUND", "message": f"No application with id {application_id}"})
    if row["status"] in ("APPROVED", "DECLINED"):
        raise HTTPException(status_code=409, detail={
            "code": "INVALID_STATE_TRANSITION",
            "message": f"{row['id']} is already {row['status']} — a decision is final in this sandbox."})
    row["status"] = payload.status
    if payload.status in ("APPROVED", "DECLINED"):
        row["decision_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        row["assessor"] = who["label"] or who["key"][:12]
    db.upsert_row(instance["id"], "applications", row)
    return {"data": row, "note": payload.note, "changed_by": who["label"] or who["key"][:12]}


# --------------------------------------------------------------------------
# Loans (servicing)
# --------------------------------------------------------------------------

@router.get("/v1/loans", summary="List booked loans")
def list_loans(slug: str, request: Request,
               page: int = 1, page_size: int = 10,
               customer_id: str | None = None,
               status: str | None = None,
               in_arrears: bool | None = None,
               x_api_key: str | None = Header(default=None),
               authorization: str | None = Header(default=None)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    authenticate(request, instance, x_api_key, authorization)
    rows = db.fetch_rows(instance["id"], "loans")
    if customer_id:
        rows = [r for r in rows if r["customer_id"] == customer_id]
    if status:
        rows = [r for r in rows if r["status"] == status.upper()]
    if in_arrears is not None:
        rows = [r for r in rows if (r.get("arrears_days", 0) > 0) == in_arrears]
    return paginate(rows, page, page_size, base="/v1/loans",
                    query={"customer_id": customer_id, "status": status})


@router.get("/v1/loans/{loan_id}", summary="Get one loan")
def get_loan(slug: str, loan_id: str, request: Request,
             x_api_key: str | None = Header(default=None),
             authorization: str | None = Header(default=None)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    authenticate(request, instance, x_api_key, authorization)
    row = db.fetch_one(instance["id"], "loans", loan_id)
    if not row:
        raise HTTPException(status_code=404, detail={
            "code": "LOAN_NOT_FOUND", "message": f"No loan with id {loan_id}"})
    return {"data": row, "links": {"schedule": f"/v1/loans/{row['id']}/schedule",
                                   "payments": f"/v1/loans/{row['id']}/payments"}}


@router.get("/v1/loans/{loan_id}/schedule", summary="Amortisation schedule")
def loan_schedule(slug: str, loan_id: str, request: Request, months: int = 12,
                  x_api_key: str | None = Header(default=None),
                  authorization: str | None = Header(default=None)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    authenticate(request, instance, x_api_key, authorization)
    loan = db.fetch_one(instance["id"], "loans", loan_id)
    if not loan:
        raise HTTPException(status_code=404, detail={
            "code": "LOAN_NOT_FOUND", "message": f"No loan with id {loan_id}"})
    months = max(1, min(months, 360))
    rows, balance = [], float(loan["balance"])
    monthly_rate = float(loan["rate_pct"]) / 100 / 12
    left = max(1, int(loan["term_months"]) - int(loan.get("payments_made") or 0))
    pmt = float(loan.get("repayment_amount") or 0) or (
        balance * monthly_rate / (1 - (1 + monthly_rate) ** -left))
    start = datetime.now(timezone.utc).astimezone().date().replace(day=1)
    for i in range(months):
        interest = round(balance * monthly_rate, 2)
        principal = round(pmt - interest, 2)
        balance = round(max(0.0, balance - principal), 2)
        due = (start + timedelta(days=32 * (i + 1))).replace(day=1)
        rows.append({"installment": int(loan.get("payments_made") or 0) + i + 1, "due_date": due.isoformat(),
                     "opening_balance": round(balance + principal, 2), "interest": interest,
                     "principal": principal, "total_repayment": round(pmt, 2),
                     "closing_balance": balance})
    return {"data": rows, "meta": {"loan_id": loan["id"], "rows": len(rows),
                                   "rate_pct": loan["rate_pct"], "repayment_amount": round(pmt, 2)}}


@router.get("/v1/loans/{loan_id}/payments", summary="Repayment history")
def loan_payments(slug: str, loan_id: str, request: Request,
                  x_api_key: str | None = Header(default=None),
                  authorization: str | None = Header(default=None)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    authenticate(request, instance, x_api_key, authorization)
    loan = db.fetch_one(instance["id"], "loans", loan_id)
    if not loan:
        raise HTTPException(status_code=404, detail={
            "code": "LOAN_NOT_FOUND", "message": f"No loan with id {loan_id}"})
    rows = [p for p in db.fetch_rows(instance["id"], "payments") if p["loan_id"] == loan["id"]]
    rows.sort(key=lambda p: p["paid_at"], reverse=True)
    return {"meta": {"loan_id": loan["id"], "total": len(rows),
                     "total_paid": round(sum(p["amount"] for p in rows if not p.get("reversed")), 2)},
            "data": rows}


@router.post("/v1/loans/{loan_id}/payments", status_code=201,
             summary="Record a repayment (idempotency-key protected)")
def record_payment(slug: str, loan_id: str, payload: PaymentCreate, request: Request,
                   idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                   x_api_key: str | None = Header(default=None),
                   authorization: str | None = Header(default=None)) -> JSONResponse:
    instance = get_instance_or_404(slug)
    who = authenticate(request, instance, x_api_key, authorization, need_write=True)
    loan = db.fetch_one(instance["id"], "loans", loan_id)
    if not loan:
        raise HTTPException(status_code=404, detail={
            "code": "LOAN_NOT_FOUND", "message": f"No loan with id {loan_id}"})

    if idempotency_key:
        prior_id = db.idempotent_lookup(instance["id"], idempotency_key)
        if prior_id:
            prior = db.fetch_one(instance["id"], "payments", prior_id)
            return JSONResponse(status_code=200, content={
                "data": prior, "replayed": True,
                "note": "Same Idempotency-Key as before — the original payment was NOT taken again."},
                headers={"Idempotency-Replayed": "true"})

    if payload.amount > float(loan["balance"]):
        raise HTTPException(status_code=422, detail={
            "code": "AMOUNT_EXCEEDS_BALANCE",
            "message": f"Payment {payload.amount} exceeds outstanding balance {loan['balance']}."})

    payments = db.fetch_rows(instance["id"], "payments")
    payment = {"id": _next_id(payments, "PAY-", 6, 500001), "loan_id": loan["id"],
               "amount": round(payload.amount, 2),
               "paid_at": (payload.paid_at or datetime.now(timezone.utc).astimezone()
                           ).isoformat(timespec="seconds"),
               "method": payload.method, "reference": payload.reference, "reversed": False,
               "recorded_by": who["label"] or who["key"][:12]}
    db.upsert_row(instance["id"], "payments", payment)
    loan["balance"] = round(float(loan["balance"]) - payment["amount"], 2)
    if loan["balance"] == 0:
        loan["status"] = "CLOSED"
    db.upsert_row(instance["id"], "loans", loan)
    if idempotency_key:
        db.idempotent_store(instance["id"], idempotency_key, payment["id"])
    return JSONResponse(status_code=201, content={
        "data": payment,
        "loan": {"id": loan["id"], "new_balance": loan["balance"], "status": loan["status"]},
        "replayed": False},
        headers={"Location": f"/v1/loans/{loan['id']}/payments"})


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

@router.get("/v1/reports/portfolio-summary",
            summary="Portfolio aggregates, as a reporting layer would expose them")
def portfolio_summary(slug: str, request: Request,
                      group_by: Literal["product", "state", "status"] = "product",
                      x_api_key: str | None = Header(default=None),
                      authorization: str | None = Header(default=None)) -> dict[str, Any]:
    instance = get_instance_or_404(slug)
    authenticate(request, instance, x_api_key, authorization)
    customers = {c["id"]: c for c in db.fetch_rows(instance["id"], "customers")}
    loans = db.fetch_rows(instance["id"], "loans")
    buckets: dict[str, dict] = {}
    for loan in loans:
        key = {"product": loan["product"], "status": loan["status"],
               "state": customers.get(loan["customer_id"], {}).get("state", "?")}[group_by]
        b = buckets.setdefault(key, {"group": key, "loans": 0, "balance": 0.0,
                                     "original_amount": 0.0, "in_arrears": 0})
        b["loans"] += 1
        b["balance"] = round(b["balance"] + float(loan["balance"]), 2)
        b["original_amount"] = round(b["original_amount"] + float(loan["original_amount"]), 2)
        b["in_arrears"] += 1 if (loan.get("arrears_days") or 0) > 0 else 0
    data = sorted(buckets.values(), key=lambda b: -b["balance"])
    for b in data:
        b["arrears_pct"] = round(100 * b["in_arrears"] / b["loans"], 2)
    return {"meta": {"group_by": group_by,
                     "as_at": datetime.now(timezone.utc).astimezone().date().isoformat(),
                     "source_system": "training-datawarehouse",
                     "instance": instance["slug"],
                     "totals": {"loans": len(loans),
                                "balance": round(sum(float(l["balance"]) for l in loans), 2)}},
            "data": data}
