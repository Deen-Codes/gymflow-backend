"""REPORT-A-BUG (May 2026, Deen QC).

User-submitted bug reports from the iOS Profile sheet. One endpoint:

  POST /api/users/bug-report/
    Body: {
      what_happened:  str (required),
      expected:       str (optional),
      app_version:    str (optional, iOS auto-fill),
      app_build:      str (optional, iOS auto-fill),
      os_version:     str (optional, iOS auto-fill),
      device_model:   str (optional, iOS auto-fill),
      recent_actions: list[str] (optional, iOS auto-fill),
      screenshot_base64: str (optional, base64-encoded JPEG/PNG/HEIC),
    }

  Returns: 201 with `{"id": <bug_id>}`.

Side-effects:
  • Creates a `BugReport` row.
  • Fires a Resend email to the Afletics inbox so reports show up in
    Deen's mailbox without needing to open Django admin. Email is
    plain-text + an embedded screenshot when present.

Failure handling — the row is the canonical record, so even if Resend
is down (rate-limited, network error, etc.) the bug report is still
saved. We just log + carry on. Email is a notification, not the data
store.
"""
from __future__ import annotations

import base64
import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import BugReport


log = logging.getLogger(__name__)


# Tighter cap than progress photos — bug reports don't need a full
# 10 MB allowance. ~3 MB raw is plenty for any screenshot.
MAX_SCREENSHOT_BYTES = 3 * 1024 * 1024

# Where bug-report notifications land. Pulled from settings so it can
# be tuned per environment without code changes (local dev → personal
# inbox, prod → triage inbox).
BUG_REPORT_INBOX = getattr(
    settings, "BUG_REPORT_INBOX", "hello@afletics.com",
)


@csrf_exempt
@api_view(["POST"])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def submit_bug_report(request):
    """Create a bug report + notify Deen via Resend."""
    data = request.data or {}

    what_happened = (data.get("what_happened") or "").strip()
    if not what_happened:
        return Response(
            {"detail": "Tell us what happened so we can fix it."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # 5,000-char ceiling — enough for a paragraph or three, prevents
    # someone from pasting a novel and bloating the table.
    if len(what_happened) > 5000:
        what_happened = what_happened[:5000]

    expected = (data.get("expected") or "").strip()
    if len(expected) > 5000:
        expected = expected[:5000]

    screenshot_b64 = (data.get("screenshot_base64") or "").strip()
    if screenshot_b64:
        # Validate base64 + cap size. Don't try to re-encode — bug
        # report screenshots are read-only artefacts; we trust the
        # bytes iOS sent.
        try:
            decoded = base64.b64decode(screenshot_b64, validate=True)
        except Exception:
            return Response(
                {"detail": "Screenshot couldn't be decoded. Try without an attachment."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(decoded) > MAX_SCREENSHOT_BYTES:
            return Response(
                {"detail": "Screenshot too large. Keep it under 3 MB."},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )

    # Auto-attached metadata. All optional — iOS sends what it knows.
    app_version  = (data.get("app_version")  or "")[:32]
    app_build    = (data.get("app_build")    or "")[:32]
    os_version   = (data.get("os_version")   or "")[:32]
    device_model = (data.get("device_model") or "")[:64]
    recent       = data.get("recent_actions") or []
    if not isinstance(recent, list):
        recent = []
    # Cap each action string + total list length so a runaway client
    # can't blow up the JSONField. 20 entries × 200 chars = 4 KB.
    recent = [str(item)[:200] for item in recent[:20]]

    report = BugReport.objects.create(
        user=request.user,
        what_happened=what_happened,
        expected=expected,
        app_version=app_version,
        app_build=app_build,
        os_version=os_version,
        device_model=device_model,
        recent_actions=recent,
        screenshot_base64=screenshot_b64,
    )

    # Email notification — best-effort. The DB row is the canonical
    # record so we never hard-fail the API call on a Resend hiccup.
    _send_notification_email(report, request.user, has_screenshot=bool(screenshot_b64))

    return Response({"id": report.id}, status=status.HTTP_201_CREATED)


def _send_notification_email(report: BugReport, user, *, has_screenshot: bool) -> None:
    """Email Deen + the triage inbox with the report contents.

    Plain text. We don't HTMLify — the table format reads fine in any
    client and Resend doesn't add render quirks on plain bodies.
    Screenshot bytes attach as an image/jpeg part when present.
    """
    subject = f"[Afletics bug] {report.what_happened[:60]}"
    body_lines = [
        f"Bug report #{report.id}",
        "",
        f"From:           {user.username or '(no username)'} <{user.email or 'no email'}>",
        f"Role:           {user.role}",
        f"App version:    {report.app_version or '—'}",
        f"App build:      {report.app_build or '—'}",
        f"OS version:     {report.os_version or '—'}",
        f"Device model:   {report.device_model or '—'}",
        f"Submitted at:   {report.created_at.isoformat()}",
        "",
        "── What happened ──",
        report.what_happened,
        "",
    ]
    if report.expected:
        body_lines += ["── What they expected ──", report.expected, ""]
    if report.recent_actions:
        body_lines += ["── Last actions ──"]
        for action in report.recent_actions:
            body_lines.append(f"  • {action}")
        body_lines.append("")
    if has_screenshot:
        body_lines += ["── Screenshot attached ──", ""]
    body_lines += [
        "── Triage ──",
        f"Django admin: /admin/users/bugreport/{report.id}/change/",
    ]
    body = "\n".join(body_lines)

    try:
        message = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[BUG_REPORT_INBOX],
            reply_to=[user.email] if user.email else None,
        )
        if has_screenshot:
            try:
                raw = base64.b64decode(report.screenshot_base64)
                message.attach(
                    f"bug-report-{report.id}-screenshot.jpg",
                    raw,
                    "image/jpeg",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("BugReport screenshot attach failed: %s", exc)
        message.send(fail_silently=True)
    except Exception as exc:  # noqa: BLE001 — never block on email
        log.warning("BugReport email send failed: %s", exc)
