#!/usr/bin/env python3
"""GoProxy dashboard log helper.

The live tests use this to verify which models opencode actually calls — the
dashboard records every proxied request with its model name, so a model switch
can be proven without restarting opencode.

Credentials (never commit them):
  GOPROXY_UI        default http://localhost:4881
  GOPROXY_UI_USER   default admin
  GOPROXY_UI_PASSWORD  required for the proxy assertions

Without a password the helpers return empty results and callers skip their
proxy assertions.
"""
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

UI = os.environ.get("GOPROXY_UI", "http://localhost:4881").rstrip("/")
USER = os.environ.get("GOPROXY_UI_USER", "admin")
PASSWORD = os.environ.get("GOPROXY_UI_PASSWORD", "")


def available():
    return bool(PASSWORD)


def _login():
    body = json.dumps({"username": USER, "password": PASSWORD}).encode()
    req = urllib.request.Request(
        f"{UI}/api/auth/login", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r).get("session")


_TOKEN = None


def token():
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = _login()
    return _TOKEN


def recent_requests(limit=300):
    """Newest-first request records: {time, model, status, latencyMs, tokens...}."""
    try:
        req = urllib.request.Request(
            f"{UI}/api/requests", headers={"Authorization": f"Bearer {token()}"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
        return data.get("requests", [])[:limit]
    except Exception:
        return []


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def requests_since(since):
    """Requests with time >= since (a timezone-aware datetime)."""
    out = []
    for r in recent_requests():
        try:
            if _parse(r["time"]) >= since:
                out.append(r)
        except Exception:
            continue
    return out


def models_since(since):
    return [r.get("model") for r in requests_since(since)]


def wait_for_model(model, since, timeout=600, poll=5):
    """Wait until the dashboard records a request for `model` after `since`.

    Returns (seen, models) — seen=True when the model appeared.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        models = models_since(since)
        if model in models:
            return True, models
        time.sleep(poll)
    return False, models_since(since)


def now():
    return datetime.now(timezone.utc)
