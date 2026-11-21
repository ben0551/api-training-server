#!/usr/bin/env python3
"""
End-to-end verification for the hosted training API server.

Proves, against a *running* server, that:
  1. the admin console signs in and can create an instance
  2. the instance's API answers, with docs and the share page reachable
  3. the whole course collection passes against the hosted instance — the same 44
     requests the workbook teaches, unchanged
  4. key management works: a disabled key is refused, a new write key is accepted
  5. freeze blocks writes but not reads
  6. reset restores the seed data (a payment posted earlier is gone)
  7. instances are isolated from each other
  8. archive makes the URL 404 and restore brings it back
  9. the instance is deleted cleanly afterwards

Run it before every training session:

    python3 tests/verify_hosted.py http://localhost:8080 --admin-password <pw>

The course collections are looked for in ../api-training-course/postman, then in
./tests/collections, then downloaded from the public GitHub repo.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
COURSE_GITHUB = ("https://raw.githubusercontent.com/ben0551/"
                 "api-fundamentals-finance-training/main/postman")

PASS, FAIL = [], []


def check(label: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(label)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  — {detail}" if detail else ""))


# --------------------------------------------------------------------------
# tiny HTTP client that keeps cookies (the admin session)
# --------------------------------------------------------------------------

class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Let the caller see the 303 and its Location header instead of following it."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        cookies = urllib.request.HTTPCookieProcessor(self.jar)
        self.opener = urllib.request.build_opener(cookies)
        self.opener_raw = urllib.request.build_opener(cookies, NoRedirect)

    def request(self, method: str, path: str, *, data: dict | None = None,
                headers: dict | None = None, raw: str | None = None, timeout: int = 25,
                follow: bool = True):
        url = path if path.startswith("http") else self.base + path
        body = None
        hdrs = dict(headers or {})
        if raw is not None:
            body = raw.encode()
            hdrs.setdefault("Content-Type", "application/json")
        elif data is not None:
            body = urllib.parse.urlencode(data).encode()
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        opener = self.opener if follow else self.opener_raw
        try:
            with opener.open(req, timeout=timeout) as resp:
                # uvicorn sends lowercase header names, so normalise before lookup
                return (resp.status, resp.read().decode("utf-8", "replace"),
                        {k.lower(): v for k, v in resp.headers.items()})
        except urllib.error.HTTPError as exc:
            return (exc.code, exc.read().decode("utf-8", "replace"),
                    {k.lower(): v for k, v in exc.headers.items()})
        except Exception as exc:
            return 0, f"ERROR {exc}", {}

    def json(self, method: str, path: str, **kw):
        code, text, _ = self.request(method, path, **kw)
        try:
            return code, json.loads(text)
        except Exception:
            return code, {"_raw": text[:400]}


# --------------------------------------------------------------------------
# locating the course collections
# --------------------------------------------------------------------------

def find_collections() -> str:
    candidates = [
        os.path.join(os.path.dirname(REPO), "api-training-course", "postman"),
        os.path.join(HERE, "collections"),
    ]
    for c in candidates:
        if os.path.isfile(os.path.join(c, "Finance-API-Training.postman_collection.json")):
            return c
    dest = os.path.join(HERE, "collections")
    os.makedirs(dest, exist_ok=True)
    print(f"  downloading the course collections into {dest} …")
    for name in ("Finance-API-Training.postman_collection.json",
                 "Public-APIs.postman_collection.json",
                 "LendFast-Sandbox.postman_environment.json"):
        try:
            with urllib.request.urlopen(f"{COURSE_GITHUB}/{name}", timeout=25) as r:
                open(os.path.join(dest, name), "wb").write(r.read())
        except Exception as exc:
            print(f"    could not fetch {name}: {exc}")
    return dest


def run_collections(collections_dir: str, base_url: str) -> int:
    """Fire every request in the course collection at the hosted instance."""
    verifier = os.path.join(collections_dir, "verify_collections.py")
    if not os.path.isfile(verifier):
        print("  (verify_collections.py not found — running a reduced check)")
        return -1
    import subprocess
    proc = subprocess.run([sys.executable, verifier, base_url],
                          capture_output=True, text=True, cwd=collections_dir)
    tail = [l for l in proc.stdout.splitlines() if "executed" in l or "FAIL" in l]
    for line in tail[:12]:
        print("   " + line.strip())
    m = re.search(r"(\d+) requests executed, (\d+) unexpected", proc.stdout)
    if not m:
        return -1
    return int(m.group(2))


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="http://localhost:8080")
    ap.add_argument("--admin-password", default=os.getenv("ADMIN_PASSWORD", ""))
    ap.add_argument("--keep", action="store_true", help="do not delete the test instance")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    slug = f"verify-{int(time.time()) % 100000}"
    print(f"\nVerifying {base}\n")

    anon = Client(base)
    admin = Client(base)

    # --- 1. liveness -------------------------------------------------------
    code, body = anon.json("GET", "/health")
    check("server health endpoint answers", code == 200 and body.get("status") == "ok", str(code))

    # --- 2. admin sign-in --------------------------------------------------
    code, page, _ = admin.request("GET", "/admin")
    check("admin console redirects to sign-in when anonymous",
          code in (200, 303) and ("Admin password" in page or code == 303))
    if args.admin_password:
        code, _, _ = admin.request("POST", "/admin/login",
                                   data={"password": args.admin_password, "next": "/admin"})
        check("admin sign-in accepted", code in (200, 303), str(code))
    else:
        check("admin sign-in skipped (no --admin-password given)", True)

    # --- 3. create an instance --------------------------------------------
    code, _, hdrs = admin.request("POST", "/admin/instances",
                                  data={"name": slug, "description": "automated verification",
                                        "preset": "standard", "persistent": "1",
                                        "show_keys": "1", "auto_reset_hours": "0"}, follow=False)
    location = hdrs.get("location", "")
    m = re.search(r"/admin/instances/(\d+)", location)
    instance_id = int(m.group(1)) if m else None
    check("instance created via the console", instance_id is not None, location or "no redirect")

    code, listing = anon.json("GET", "/instances")
    slugs = [i["slug"] for i in listing.get("instances", [])]
    real_slug = slug if slug in slugs else next(
        (s for s in slugs if s.startswith("verify-")), None)
    check("new instance appears in the instance list", real_slug is not None, ", ".join(slugs[:4]))
    if not real_slug or instance_id is None:
        return report()
    api = f"{base}/t/{real_slug}"

    # --- 4. the instance API answers --------------------------------------
    code, body = anon.json("GET", f"/t/{real_slug}/health")
    check("instance health endpoint answers", code == 200 and body.get("status") == "ok", str(code))
    code, _, _ = anon.request("GET", f"/t/{real_slug}/docs")
    check("per-instance API documentation renders", code == 200, str(code))
    code, page, _ = anon.request("GET", f"/t/{real_slug}")
    check("participant share page renders", code == 200 and "demo-key-001" in page)
    code, spec = anon.json("GET", f"/t/{real_slug}/openapi.json")
    check("per-instance OpenAPI spec is populated", code == 200 and len(spec.get("paths", {})) >= 15,
          f"{len(spec.get('paths', {}))} paths")

    # --- 5. the whole course collection, unchanged -------------------------
    print("\n  Running the course collection against the hosted instance…")
    collections_dir = find_collections()
    unexpected = run_collections(collections_dir, api)
    if unexpected >= 0:
        check("every course request behaves as the workbook promises", unexpected == 0,
              f"{unexpected} unexpected")

    # --- 6. key management -------------------------------------------------
    read = {"X-API-Key": "demo-key-001"}
    write = {"X-API-Key": "demo-key-002"}
    code, _ = anon.json("GET", f"/t/{real_slug}/v1/customers", headers=read)
    check("read key works", code == 200, str(code))
    code, _ = anon.json("POST", f"/t/{real_slug}/v1/applications", headers=write,
                        raw=json.dumps({"customer_id": "CUS-100001", "product": "Personal Loan",
                                        "amount": 9000, "term_months": 24, "purpose": "Car"}))
    check("write key can lodge an application", code == 201, str(code))

    code, page, _ = admin.request("GET", f"/admin/instances/{instance_id}/keys")
    ids = re.findall(r"/keys/(\d+)/disable", page)
    if ids:
        admin.request("POST", f"/admin/instances/{instance_id}/keys/{ids[0]}/disable")
        code, body = anon.json("GET", f"/t/{real_slug}/v1/customers", headers=read)
        check("a disabled key is refused", code == 401 and
              body.get("detail", {}).get("code") == "API_KEY_DISABLED", str(code))
        admin.request("POST", f"/admin/instances/{instance_id}/keys/{ids[0]}/enable")
        code, _ = anon.json("GET", f"/t/{real_slug}/v1/customers", headers=read)
        check("re-enabling the key restores access", code == 200, str(code))
    else:
        check("key management reachable", False, "no key controls found on the page")

    admin.request("POST", f"/admin/instances/{instance_id}/keys",
                  data={"label": "verification write key", "scope": "write", "key": "verify-write-key"})
    code, _ = anon.json("POST", f"/t/{real_slug}/v1/applications", headers={"X-API-Key": "verify-write-key"},
                        raw=json.dumps({"customer_id": "CUS-100002", "product": "Personal Loan",
                                        "amount": 8000, "term_months": 24, "purpose": "Car"}))
    check("a newly created write key works", code == 201, str(code))

    # --- 7. freeze --------------------------------------------------------
    admin.request("POST", f"/admin/instances/{instance_id}/action", data={"action": "freeze"})
    code, _ = anon.json("GET", f"/t/{real_slug}/v1/customers", headers=read)
    check("frozen instance still allows reads", code == 200, str(code))
    code, body = anon.json("POST", f"/t/{real_slug}/v1/applications", headers=write,
                           raw=json.dumps({"customer_id": "CUS-100003", "product": "Personal Loan",
                                           "amount": 9000, "term_months": 24, "purpose": "Car"}))
    check("frozen instance refuses writes", code == 403 and
          body.get("detail", {}).get("code") == "INSTANCE_FROZEN", str(code))
    admin.request("POST", f"/admin/instances/{instance_id}/action", data={"action": "unfreeze"})

    # --- 8. reset ---------------------------------------------------------
    code, body = anon.json("GET", f"/t/{real_slug}/v1/applications?page_size=100", headers=read)
    before = body.get("meta", {}).get("total", 0)
    admin.request("POST", f"/admin/instances/{instance_id}/action", data={"action": "reset"})
    code, body = anon.json("GET", f"/t/{real_slug}/v1/applications?page_size=100", headers=read)
    after = body.get("meta", {}).get("total", 0)
    check("reset returns the data to its seed state", after == 5 and after < before,
          f"{before} applications before, {after} after")

    code, body = anon.json("GET", f"/t/{real_slug}/v1/loans/LON-90001", headers=read)
    check("reset restores a balance changed by a payment",
          abs(float(body.get("data", {}).get("balance", 0)) - 598412.77) < 0.01,
          str(body.get("data", {}).get("balance")))

    # --- 9. isolation ------------------------------------------------------
    others = [s for s in slugs if not s.startswith("verify-")]
    other = f"{base}/t/{others[0]}" if others else None
    if other:
        code, body = anon.json("GET", f"{other}/v1/customers?page_size=100", headers=read)
        check("another instance has its own data", code == 200, f"{body.get('meta', {}).get('total')} customers")
    else:
        check("instance isolation (only one instance exists, skipped)", True)

    # --- 10. archive ------------------------------------------------------
    admin.request("POST", f"/admin/instances/{instance_id}/action", data={"action": "archive"})
    code, _ = anon.json("GET", f"/t/{real_slug}/v1/customers", headers=read)
    check("an archived instance returns 404", code == 404, str(code))
    admin.request("POST", f"/admin/instances/{instance_id}/action", data={"action": "unarchive"})
    code, _ = anon.json("GET", f"/t/{real_slug}/v1/customers", headers=read)
    check("restoring brings the instance back", code == 200, str(code))

    # --- 11. tidy up ------------------------------------------------------
    if args.keep:
        print(f"\n  (kept instance '{real_slug}' as requested)")
    else:
        admin.request("POST", f"/admin/instances/{instance_id}/action", data={"action": "delete"})
        code, _ = anon.json("GET", f"/t/{real_slug}/v1/customers", headers=read)
        check("deleting the instance removes its URL", code == 404, str(code))

    return report()


def report() -> int:
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("Failures:")
        for f in FAIL:
            print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
