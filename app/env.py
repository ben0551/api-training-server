"""
Tiny .env loader.

Shared hosts set environment variables through a control panel, which is fine, but a
file is easier to keep out of version control and to chmod 600. This reads KEY=VALUE
lines from .env next to the project root (and from $DATA_DIR/.env) and fills in anything
not already set in the environment. Real environment variables always win.

Loaded before anything reads a setting, so it must be imported first.
"""
from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_dotenv(*extra_paths: str) -> list[str]:
    loaded = []
    candidates = [os.path.join(ROOT, ".env")] + list(extra_paths)
    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        candidates.append(os.path.join(data_dir, ".env"))

    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
            loaded.append(path)
        except OSError:
            continue
    return loaded


load_dotenv()
