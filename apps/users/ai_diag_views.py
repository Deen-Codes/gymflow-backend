"""
DIAG — minimal, no-auth diagnostic for the AI build 503.

We've now layered three timeout fixes (iOS 75s, Python requests 70s,
gunicorn worker 120s) and STILL get a generic 503 with no follow-up
error log line on the Render side. That means either:

  1. The latest backend code (the one with the explicit
     "ANTHROPIC_API_KEY missing" log line) isn't actually deployed.
  2. The env var IS missing and the request never reaches Anthropic.
  3. Something upstream of Django (Render's HTTP router / load
     balancer) is returning the 503 before our code runs.

This endpoint shortcuts the guessing. It:

  • Reports whether ANTHROPIC_API_KEY is set on this dyno (without
    revealing the value — only the first/last 4 chars and length).
  • Reports the running Anthropic model + URL + the deploy's
    Render commit SHA (free env var Render injects automatically).
  • Optionally fires a 1-token Anthropic /v1/messages ping and
    reports status code + wall-clock time. Use ?ping=1 to enable —
    default OFF so an accidental hit doesn't burn credit.

Auth: none. We expose ZERO secrets and the endpoint is rate-limited
by Render's own per-IP limits. Once we've diagnosed the 503 we'll
remove this — but for now it's worth more than a hundred
guess-and-deploy cycles.

Hit it at:   GET /api/users/_diag/ai/
With ping:   GET /api/users/_diag/ai/?ping=1
"""
import logging
import os
import time

from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

log = logging.getLogger(__name__)


def _redact(secret: str) -> str:
    """Return a fingerprint that proves the key is set without
    leaking the actual key. Examples:
        ""           → "<empty>"
        "sk-ant-..."  → "sk-a…XXXX (len=108)"
    """
    if not secret:
        return "<empty>"
    if len(secret) <= 8:
        return f"<{len(secret)} chars>"
    return f"{secret[:4]}…{secret[-4:]} (len={len(secret)})"


@csrf_exempt
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def ai_diag(request):
    # Pull the key two ways — through settings (in case settings.py
    # cached it) and directly from env (in case env was set after
    # settings imported).
    settings_key = getattr(settings, "ANTHROPIC_API_KEY", "") or ""
    env_key      = os.environ.get("ANTHROPIC_API_KEY", "") or ""

    # And the way ai_pt_views.py / ai_build_views.py actually use it
    # (resolved at module-import time):
    from .ai_pt_views import ANTHROPIC_API_KEY as imported_key

    out = {
        "ok": True,
        "key_via_settings_module":   _redact(settings_key),
        "key_via_os_environ":        _redact(env_key),
        "key_via_module_import":     _redact(imported_key),
        "key_lengths_match": (
            len(settings_key) == len(env_key) == len(imported_key)
            and len(env_key) > 0
        ),
        "anthropic_model": "claude-sonnet-4-6",
        "anthropic_url":   "https://api.anthropic.com/v1/messages",
        # Render injects these automatically on every deploy. If the
        # commit SHA reported here doesn't match what you just
        # pushed, the new code didn't actually deploy.
        "render_commit":   os.environ.get("RENDER_GIT_COMMIT", "<unset>")[:12],
        "render_branch":   os.environ.get("RENDER_GIT_BRANCH", "<unset>"),
        "render_service":  os.environ.get("RENDER_SERVICE_NAME", "<unset>"),
        "render_instance": os.environ.get("RENDER_INSTANCE_ID", "<unset>"),
        # Useful sanity check — confirms the build / start commands
        # actually shipped the latest code. Each new deploy gets a
        # fresh process, so a stale process_started_at means Render
        # didn't restart after the start-command change.
        "process_started_at_utc": _process_start_iso(),
    }

    if request.GET.get("ping") == "1":
        out["ping"] = _ping_anthropic(imported_key)

    return Response(out)


# Compute process start time once at module import. Subsequent calls
# reuse it so the same number is returned for the lifetime of the
# worker — that's the point.
import datetime as _dt
_PROCESS_STARTED_AT = _dt.datetime.utcnow().isoformat() + "Z"


def _process_start_iso() -> str:
    return _PROCESS_STARTED_AT


def _ping_anthropic(api_key: str) -> dict:
    """Fire the cheapest possible Anthropic call (1 token max) so
    we can verify end-to-end reachability + auth. Reports status
    code, wall-clock seconds, and a redacted error message if any."""
    import requests

    if not api_key:
        return {"status": "skipped", "reason": "no api key"}

    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "hi"}],
    }
    t0 = time.time()
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=20.0,
        )
    except requests.exceptions.Timeout:
        return {"status": "timeout", "elapsed_s": round(time.time() - t0, 2)}
    except Exception as exc:
        return {"status": "exception", "error": str(exc)[:200],
                "elapsed_s": round(time.time() - t0, 2)}

    elapsed = round(time.time() - t0, 2)
    info = {"status_code": resp.status_code, "elapsed_s": elapsed}
    if resp.status_code != 200:
        try:
            info["error"] = (resp.json().get("error") or {})
        except Exception:
            info["error_body"] = resp.text[:300]
    else:
        info["status"] = "ok"
    return info
