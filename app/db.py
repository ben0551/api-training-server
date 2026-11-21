"""
SQLite storage for the training API server.

One file holds everything: instances, their keys, their data, idempotency records,
issued tokens and the audit log. No second container, no external service — which
matters when the whole thing is a training sandbox that has to survive a restart
and be backed up by copying one file.

Access pattern: a short-lived connection per request (FastAPI runs sync endpoints in
a threadpool, so a shared connection would need locking). WAL mode keeps readers
from blocking on a write.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import string
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
DB_PATH = os.getenv("DB_PATH", os.path.join(DATA_DIR, "training.db"))

# The three well-known keys every new instance starts with, so all the existing
# course material (workbook, slides, Postman collection) works unchanged.
DEFAULT_KEYS = [
    {"key": "demo-key-001", "label": "Ava Nguyen", "role": "analyst", "scope": "read"},
    {"key": "demo-key-002", "label": "Ben Fischer", "role": "originator", "scope": "write"},
    {"key": "demo-key-003", "label": "Service Account", "role": "readonly", "scope": "read"},
]
DEFAULT_CLIENT_SECRET = "training-secret"

SCHEMA = """
CREATE TABLE IF NOT EXISTS instances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    slug            TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    persistent      INTEGER NOT NULL DEFAULT 1,   -- 1 = keep across restarts, never auto-expire
    read_only       INTEGER NOT NULL DEFAULT 0,   -- 1 = freeze writes (exam / demo mode)
    show_keys       INTEGER NOT NULL DEFAULT 1,   -- 1 = landing page lists the keys
    archived        INTEGER NOT NULL DEFAULT 0,
    auto_reset_hours INTEGER NOT NULL DEFAULT 0,  -- 0 = never auto-reset
    last_reset_at   TEXT NOT NULL,
    client_secret   TEXT NOT NULL,
    preset          TEXT NOT NULL DEFAULT 'standard',
    seed_json       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id   INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    key           TEXT NOT NULL,
    label         TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'custom',
    scope         TEXT NOT NULL DEFAULT 'read',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT,
    UNIQUE (instance_id, key)
);

CREATE TABLE IF NOT EXISTS customers (
    instance_id   INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    id            TEXT NOT NULL,
    payload       TEXT NOT NULL,
    seq           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instance_id, id)
);
CREATE TABLE IF NOT EXISTS applications (
    instance_id   INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    id            TEXT NOT NULL,
    customer_id   TEXT,
    payload       TEXT NOT NULL,
    seq           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instance_id, id)
);
CREATE TABLE IF NOT EXISTS loans (
    instance_id   INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    id            TEXT NOT NULL,
    customer_id   TEXT,
    payload       TEXT NOT NULL,
    seq           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instance_id, id)
);
CREATE TABLE IF NOT EXISTS payments (
    instance_id   INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    id            TEXT NOT NULL,
    loan_id       TEXT,
    payload       TEXT NOT NULL,
    seq           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (instance_id, id)
);
CREATE TABLE IF NOT EXISTS idempotency (
    instance_id   INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    key           TEXT NOT NULL,
    payment_id    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (instance_id, key)
);
CREATE TABLE IF NOT EXISTS tokens (
    instance_id   INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    token         TEXT NOT NULL,
    api_key       TEXT NOT NULL,
    expires_at    REAL NOT NULL,
    PRIMARY KEY (instance_id, token)
);
CREATE TABLE IF NOT EXISTS audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id   INTEGER NOT NULL,
    at            TEXT NOT NULL,
    method        TEXT NOT NULL,
    path          TEXT NOT NULL,
    key_label     TEXT,
    status        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_instance ON audit(instance_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_apps_instance ON applications(instance_id, seq);
CREATE INDEX IF NOT EXISTS idx_loans_instance ON loans(instance_id, seq);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _bootstrap_settings(conn)


def _bootstrap_settings(conn: sqlite3.Connection) -> None:
    """Create a secret key and (if none was supplied) an admin password.

    Refusing to run with no admin password is the important part: the console is
    internet-reachable once routed through the proxy, so a blank password is a
    live exposure, not a convenience.
    """
    row = conn.execute("SELECT v FROM settings WHERE k='secret_key'").fetchone()
    if not row:
        conn.execute("INSERT INTO settings (k, v) VALUES ('secret_key', ?)", (secrets.token_urlsafe(32),))

    row = conn.execute("SELECT v FROM settings WHERE k='admin_password_hash'").fetchone()
    if not row:
        supplied = os.getenv("ADMIN_PASSWORD")
        password = supplied or "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14))
        conn.execute("INSERT INTO settings (k, v) VALUES ('admin_password_hash', ?)", (hash_password(password),))
        conn.execute("INSERT INTO settings (k, v) VALUES ('admin_password_generated', ?)",
                     ("0" if supplied else "1",))
        if not supplied:
            conn.execute("INSERT INTO settings (k, v) VALUES ('admin_password_once', ?)", (password,))


# --------------------------------------------------------------------------
# passwords (stdlib pbkdf2 — no extra dependency for a training app)
# --------------------------------------------------------------------------

def hash_password(password: str, salt: str | None = None) -> str:
    import hashlib
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    import hashlib
    try:
        _, salt, expected = stored.split("$")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return secrets.compare_digest(dk.hex(), expected)


def get_setting(k: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
        return row["v"] if row else None


def set_setting(k: str, v: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO settings (k, v) VALUES (?, ?) "
                     "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


# --------------------------------------------------------------------------
# instances
# --------------------------------------------------------------------------

def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return (s or "instance")[:48]


def unique_slug(name: str) -> str:
    base = slugify(name)
    with connect() as conn:
        taken = {r["slug"] for r in conn.execute("SELECT slug FROM instances")}
    if base not in taken:
        return base
    for i in range(2, 500):
        candidate = f"{base}-{i}"
        if candidate not in taken:
            return candidate
    return f"{base}-{secrets.token_hex(3)}"


def create_instance(name: str, *, description: str = "", preset: str = "standard",
                    persistent: bool = True, show_keys: bool = True,
                    auto_reset_hours: int = 0, dataset: dict | None = None,
                    slug: str | None = None, clone_keys_from: int | None = None) -> int:
    from .seed import get_preset
    data = dataset if dataset is not None else get_preset(preset)
    slug = slug or unique_slug(name)
    ts = now_iso()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO instances
               (slug, name, description, created_at, persistent, show_keys, auto_reset_hours,
                last_reset_at, client_secret, preset, seed_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (slug, name, description, ts, int(persistent), int(show_keys), int(auto_reset_hours),
             ts, DEFAULT_CLIENT_SECRET, preset, json.dumps(data)))
        instance_id = int(cur.lastrowid)

        keys = DEFAULT_KEYS
        if clone_keys_from:
            rows = conn.execute("SELECT key, label, role, scope FROM api_keys WHERE instance_id=?",
                                (clone_keys_from,)).fetchall()
            if rows:
                keys = [dict(r) for r in rows]
        for k in keys:
            conn.execute(
                "INSERT INTO api_keys (instance_id, key, label, role, scope, enabled, created_at) "
                "VALUES (?,?,?,?,?,1,?)",
                (instance_id, k["key"], k.get("label", ""), k.get("role", "custom"),
                 k.get("scope", "read"), ts))
    load_instance_data(instance_id, data)
    return instance_id


def list_instances(include_archived: bool = True) -> list[dict[str, Any]]:
    with connect() as conn:
        where = "" if include_archived else "WHERE archived=0"
        rows = conn.execute(f"SELECT * FROM instances {where} ORDER BY archived, id DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["key_count"] = conn.execute(
                "SELECT COUNT(*) c FROM api_keys WHERE instance_id=?", (r["id"],)).fetchone()["c"]
            d["enabled_key_count"] = conn.execute(
                "SELECT COUNT(*) c FROM api_keys WHERE instance_id=? AND enabled=1", (r["id"],)).fetchone()["c"]
            for table in ("customers", "applications", "loans", "payments"):
                d[table] = conn.execute(
                    f"SELECT COUNT(*) c FROM {table} WHERE instance_id=?", (r["id"],)).fetchone()["c"]
            d["requests"] = conn.execute(
                "SELECT COUNT(*) c FROM audit WHERE instance_id=?", (r["id"],)).fetchone()["c"]
            out.append(d)
        return out


def get_instance(instance_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM instances WHERE id=?", (instance_id,)).fetchone()
        return dict(row) if row else None


def get_instance_by_slug(slug: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM instances WHERE slug=?", (slug,)).fetchone()
        return dict(row) if row else None


def update_instance(instance_id: int, **fields: Any) -> None:
    allowed = {"name", "description", "persistent", "read_only", "show_keys",
               "archived", "auto_reset_hours", "client_secret", "preset",
               "last_reset_at", "seed_json"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(int(v) if isinstance(v, bool) else v)
    if not sets:
        return
    vals.append(instance_id)
    with connect() as conn:
        conn.execute(f"UPDATE instances SET {', '.join(sets)} WHERE id=?", vals)


def delete_instance(instance_id: int) -> None:
    with connect() as conn:
        for table in ("customers", "applications", "loans", "payments", "idempotency",
                      "tokens", "api_keys", "audit"):
            conn.execute(f"DELETE FROM {table} WHERE instance_id=?", (instance_id,))
        conn.execute("DELETE FROM instances WHERE id=?", (instance_id,))


# --------------------------------------------------------------------------
# instance data
# --------------------------------------------------------------------------

def load_instance_data(instance_id: int, data: dict) -> None:
    """Replace every row for an instance from a dataset dict. This is 'reset'."""
    ts = now_iso()
    with connect() as conn:
        for table in ("customers", "applications", "loans", "payments", "idempotency", "tokens"):
            conn.execute(f"DELETE FROM {table} WHERE instance_id=?", (instance_id,))
        for seq, row in enumerate(data.get("customers", [])):
            conn.execute("INSERT INTO customers (instance_id, id, payload, seq) VALUES (?,?,?,?)",
                         (instance_id, row["id"], json.dumps(row), seq))
        for seq, row in enumerate(data.get("applications", [])):
            conn.execute("INSERT INTO applications (instance_id, id, customer_id, payload, seq) VALUES (?,?,?,?,?)",
                         (instance_id, row["id"], row.get("customer_id"), json.dumps(row), seq))
        for seq, row in enumerate(data.get("loans", [])):
            conn.execute("INSERT INTO loans (instance_id, id, customer_id, payload, seq) VALUES (?,?,?,?,?)",
                         (instance_id, row["id"], row.get("customer_id"), json.dumps(row), seq))
        for seq, row in enumerate(data.get("payments", [])):
            conn.execute("INSERT INTO payments (instance_id, id, loan_id, payload, seq) VALUES (?,?,?,?,?)",
                         (instance_id, row["id"], row.get("loan_id"), json.dumps(row), seq))
        conn.execute("UPDATE instances SET last_reset_at=? WHERE id=?", (ts, instance_id))


def fetch_rows(instance_id: int, table: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            f"SELECT payload FROM {table} WHERE instance_id=? ORDER BY seq, id", (instance_id,)).fetchall()
        return [json.loads(r["payload"]) for r in rows]


def fetch_one(instance_id: int, table: str, row_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            f"SELECT payload FROM {table} WHERE instance_id=? AND UPPER(id)=UPPER(?)",
            (instance_id, row_id)).fetchone()
        return json.loads(row["payload"]) if row else None


def upsert_row(instance_id: int, table: str, row: dict[str, Any], seq: int | None = None) -> None:
    fk = {"applications": "customer_id", "loans": "customer_id", "payments": "loan_id"}.get(table)
    with connect() as conn:
        if seq is None:
            seq = (conn.execute(f"SELECT COALESCE(MAX(seq), 0) + 1 s FROM {table} WHERE instance_id=?",
                                (instance_id,)).fetchone()["s"])
        cols = "instance_id, id, payload, seq" + (f", {fk}" if fk else "")
        ph = "?,?,?,?" + (",?" if fk else "")
        vals = [instance_id, row["id"], json.dumps(row), seq] + ([row.get(fk)] if fk else [])
        conn.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({ph})", vals)


def snapshot_dataset(instance_id: int) -> dict:
    return {t: fetch_rows(instance_id, t) for t in ("customers", "applications", "loans", "payments")}


def maybe_auto_reset(instance: dict) -> bool:
    """Reset an instance whose auto_reset_hours have elapsed. Returns True if it did."""
    hours = instance.get("auto_reset_hours") or 0
    if not hours:
        return False
    try:
        last = datetime.fromisoformat(instance["last_reset_at"])
    except Exception:
        return False
    age_hours = (datetime.now(timezone.utc).astimezone() - last).total_seconds() / 3600
    if age_hours < hours:
        return False
    load_instance_data(instance["id"], json.loads(instance["seed_json"]))
    return True


# --------------------------------------------------------------------------
# keys
# --------------------------------------------------------------------------

def list_keys(instance_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM api_keys WHERE instance_id=? ORDER BY id", (instance_id,)).fetchall()
        return [dict(r) for r in rows]


def find_key(instance_id: int, key: str) -> dict[str, Any] | None:
    if not key:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM api_keys WHERE instance_id=? AND key=?",
                           (instance_id, key)).fetchone()
        return dict(row) if row else None


def create_key(instance_id: int, label: str, scope: str = "read", role: str = "custom",
               key: str | None = None) -> str:
    key = key or f"tr_{secrets.token_urlsafe(24)}"
    with connect() as conn:
        conn.execute(
            "INSERT INTO api_keys (instance_id, key, label, role, scope, enabled, created_at) "
            "VALUES (?,?,?,?,?,1,?)",
            (instance_id, key, label, role, scope, now_iso()))
    return key


def set_key_enabled(instance_id: int, key_id: int, enabled: bool) -> None:
    with connect() as conn:
        conn.execute("UPDATE api_keys SET enabled=? WHERE instance_id=? AND id=?",
                     (int(enabled), instance_id, key_id))


def update_key(instance_id: int, key_id: int, *, label: str | None = None,
               scope: str | None = None) -> None:
    sets, vals = [], []
    if label is not None:
        sets.append("label=?"); vals.append(label)
    if scope is not None:
        sets.append("scope=?"); vals.append(scope)
    if not sets:
        return
    vals += [instance_id, key_id]
    with connect() as conn:
        conn.execute(f"UPDATE api_keys SET {', '.join(sets)} WHERE instance_id=? AND id=?", vals)


def delete_key(instance_id: int, key_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM api_keys WHERE instance_id=? AND id=?", (instance_id, key_id))


def touch_key(instance_id: int, key: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE api_keys SET last_used_at=? WHERE instance_id=? AND key=?",
                     (now_iso(), instance_id, key))


# --------------------------------------------------------------------------
# tokens (OAuth-style bearer tokens)
# --------------------------------------------------------------------------

def issue_token(instance_id: int, api_key: str, ttl_seconds: int = 3600) -> str:
    token = "tok_" + secrets.token_hex(16)
    with connect() as conn:
        conn.execute("DELETE FROM tokens WHERE instance_id=? AND expires_at < ?", (instance_id, time.time()))
        conn.execute("INSERT INTO tokens (instance_id, token, api_key, expires_at) VALUES (?,?,?,?)",
                     (instance_id, token, api_key, time.time() + ttl_seconds))
    return token


def resolve_token(instance_id: int, token: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT api_key, expires_at FROM tokens WHERE instance_id=? AND token=?",
                           (instance_id, token)).fetchone()
    if not row or row["expires_at"] < time.time():
        return None
    return row["api_key"]


# --------------------------------------------------------------------------
# idempotency + audit
# --------------------------------------------------------------------------

def idempotent_lookup(instance_id: int, key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT payment_id FROM idempotency WHERE instance_id=? AND key=?",
                           (instance_id, key)).fetchone()
        return row["payment_id"] if row else None


def idempotent_store(instance_id: int, key: str, payment_id: str) -> None:
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO idempotency (instance_id, key, payment_id, created_at) "
                     "VALUES (?,?,?,?)", (instance_id, key, payment_id, now_iso()))


def log_request(instance_id: int, method: str, path: str, key_label: str | None, status: int) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO audit (instance_id, at, method, path, key_label, status) "
                     "VALUES (?,?,?,?,?,?)", (instance_id, now_iso(), method, path, key_label, status))


def recent_audit(instance_id: int, limit: int = 25) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit WHERE instance_id=? ORDER BY id DESC LIMIT ?",
            (instance_id, limit)).fetchall()
        return [dict(r) for r in rows]


def recent_audit_all(limit: int = 200) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT a.*, i.slug, i.name FROM audit a
               JOIN instances i ON i.id = a.instance_id
               ORDER BY a.id DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]


def clear_audit(instance_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM audit WHERE instance_id=?", (instance_id,))


def stats() -> dict[str, Any]:
    with connect() as conn:
        return {
            "instances": conn.execute("SELECT COUNT(*) c FROM instances").fetchone()["c"],
            "active_instances": conn.execute("SELECT COUNT(*) c FROM instances WHERE archived=0").fetchone()["c"],
            "keys": conn.execute("SELECT COUNT(*) c FROM api_keys").fetchone()["c"],
            "keys_enabled": conn.execute("SELECT COUNT(*) c FROM api_keys WHERE enabled=1").fetchone()["c"],
            "requests": conn.execute("SELECT COUNT(*) c FROM audit").fetchone()["c"],
            "requests_24h": conn.execute(
                "SELECT COUNT(*) c FROM audit WHERE at > ?",
                (datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()[:10],)).fetchone()["c"],
        }
