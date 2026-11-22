"""
Passenger entrypoint for cPanel / Plesk shared hosting.

Point the domain's application root at this directory and Passenger will find
`application` here. See deploy/cpanel.md for the walkthrough.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.wsgi import application  # noqa: E402,F401
