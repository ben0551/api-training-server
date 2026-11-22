#!/usr/bin/env python3
"""
Execute every request in every Postman collection in this folder, for real,
against the running training API, and check the status code each request's own
test script expects.

This is the verification step for the course material: if a lab request is
wrong, this fails loudly rather than being discovered in front of a room.

Usage:  python3 verify_collections.py [baseUrl]      (default http://localhost:8080)
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"

ENV = {
    "baseUrl": BASE,
    "apiKey": "demo-key-001",
    "writeKey": "demo-key-002",
    "clientSecret": "training-secret",
    "customerId": "CUS-100003",
    "appId": "APP-2026-000413",
    "loanId": "LON-90001",
    "newAppId": "",
    "bearerToken": "",
    "idemKey": "verify-payment-001",
}

# The environment file that ships with the collection is the source of truth for
# credentials, so pick up anything it defines (a cohort's own keys, a different
# client secret) rather than assuming the standard demo values. baseUrl always comes
# from the command line.
_env_file = os.path.join(HERE, "LendFast-Sandbox.postman_environment.json")
if os.path.isfile(_env_file):
    try:
        with open(_env_file, encoding="utf-8") as _fh:
            for _v in json.load(_fh).get("values", []):
                if _v.get("enabled", True) and _v.get("key") != "baseUrl" and _v.get("value"):
                    ENV[_v["key"]] = _v["value"]
    except Exception:
        pass
ENV["baseUrl"] = BASE


def subst(text: str) -> str:
    def repl(m):
        return str(ENV.get(m.group(1), m.group(0)))
    return re.sub(r"\{\{(\w+)\}\}", repl, text)


def expected_status(item) -> int | None:
    """Pull the status code out of the request's own test script."""
    for ev in item.get("event", []):
        script = "\n".join(ev["script"]["exec"])
        m = re.search(r"to\.have\.status\((\d+)\)", script)
        if m:
            return int(m.group(1))
    return None


def run(item, folder, results):
    r = item["request"]
    raw = subst(r["url"]["raw"])
    headers = {}
    for hd in r.get("header", []):
        if hd.get("disabled"):
            continue
        headers[hd["key"]] = subst(hd["value"])
    data = None
    if "body" in r and r["body"].get("raw"):
        data = subst(r["body"]["raw"]).encode()
    method = r["method"]
    try:
        req = urllib.request.Request(raw, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=20) as resp:
            code, body = resp.status, resp.read().decode()
            rheaders = dict(resp.headers)
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read().decode()
        rheaders = dict(e.headers)
    except Exception as e:                     # connection refused, DNS, TLS...
        results.append((folder, item["name"], method, raw, None, f"ERROR {e}", None))
        return

    # mimic the small amount of environment-writing the tests do, so later
    # requests in the same run resolve their {{...}} placeholders
    try:
        payload = json.loads(body)
        ENV["_last"] = payload
        if rheaders.get("Idempotency-Replayed") == "true":
            pass
        if isinstance(payload, dict) and payload.get("access_token"):
            ENV["bearerToken"] = payload["access_token"]
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            d = payload["data"]
            if str(d.get("id", "")).startswith("APP-"): ENV["newAppId"] = d["id"]
            if str(d.get("id", "")).startswith("CUS-"): ENV["customerId"] = d["id"]
    except Exception:
        pass

    want = expected_status(item)
    ok = (want is None) or (code == want)
    results.append((folder, item["name"], method, raw, code, "OK" if ok else f"EXPECTED {want}", want))


def walk(items, folder, results):
    for it in items:
        if "item" in it:
            walk(it["item"], it["name"], results)
        else:
            run(it, folder, results)


def main():
    all_results = []
    for fname in sorted(os.listdir(HERE)):
        if not fname.endswith(".postman_collection.json"):
            continue
        coll = json.load(open(os.path.join(HERE, fname), encoding="utf-8"))
        print(f"\n=== {coll['info']['name']}  ({fname})")
        res = []
        walk(coll["item"], "(root)", res)
        # the FX/Bearer placeholders are filled by an earlier request in the run
        all_results += res
        for folder, name, method, url, code, status, want in res:
            flag = "PASS" if status == "OK" else "FAIL"
            print(f"  [{flag}] {method:6} {code}  {name}")
            if status != "OK":
                print(f"         url: {url}")
                print(f"         {status}")
    fails = [r for r in all_results if r[5] != "OK"]
    print(f"\n{len(all_results)} requests executed, {len(fails)} unexpected.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
