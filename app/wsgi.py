"""
WSGI entrypoint — for hosts that run Python through WSGI rather than ASGI.

cPanel's "Setup Python App", Plesk's Python support, and Passenger all speak WSGI.
FastAPI is an ASGI framework, so a2wsgi bridges the two: each request is run through the
ASGI app synchronously.

Use this when:
  * cPanel / Plesk shared hosting with the Python app feature
  * Passenger (passenger_wsgi.py imports `application` from here)
  * any WSGI server such as gunicorn -k ... , waitress or mod_wsgi

Use uvicorn (see the Dockerfile / systemd unit) when you control the server — it is the
native path and supports streaming responses, which this bridge does not.
"""
from __future__ import annotations

from a2wsgi import ASGIMiddleware

from .env import load_dotenv

load_dotenv()

from .main import app  # noqa: E402  (must come after the env is loaded)

application = ASGIMiddleware(app)
