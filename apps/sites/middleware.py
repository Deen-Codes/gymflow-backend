"""
Phase 7.5 — Subdomain routing for PT landing pages.

When a request arrives on `<slug>.gymflow.com` (or any apex defined
in `SUBDOMAIN_APEX_HOSTS`), and `<slug>` matches a TrainerProfile
slug that isn't on the reserved list, we rewrite the request path
to `/p/<slug>/...` and let the existing public site URL routing
serve it.

Path rewriting > a separate URLconf because:
  • all existing public site views (`/p/<slug>/`, `/p/<slug>/signup/`)
    keep working unchanged
  • CSRF, sessions, messages, etc. behave normally
  • adding a new public route only needs an entry in
    `apps.sites.public_urls` — no middleware change

Local dev: subdomains don't work natively. To test on localhost, add
to /etc/hosts:
    127.0.0.1   test-gym.localhost
    127.0.0.1   anothertrainer.localhost
…then visit http://test-gym.localhost:8000/.

Production: configure DNS so `*.gymflow.com` is an A/CNAME pointing
to the host running Django (Render's wildcard subdomain support
covers this). For SSL, Cloudflare Universal SSL with the Advanced
Certificate Manager add-on covers wildcard subdomains; or Let's
Encrypt wildcard via DNS-01 challenge.
"""
from django.utils.deprecation import MiddlewareMixin


# Apex hosts where the next dotted segment is treated as a subdomain.
# Add production hosts here when you go live (e.g. "gymflow.com").
# `localhost` is included so /etc/hosts entries like
# `test-gym.localhost` work in local dev.
SUBDOMAIN_APEX_HOSTS = (
    "gymflow.coach",   # primary apex (production)
    "gymflow.com",     # legacy / aspirational — kept so old links don't 404 if the domain is acquired later
    "gymflow.app",
    "localhost",
)

# Subdomains that must NEVER resolve to a trainer page. These are
# reserved for app infrastructure (the dashboard at app.gymflow.com,
# the API at api.gymflow.com, etc.). A trainer with one of these
# slugs would still be reachable via /p/<slug>/.
RESERVED_SUBDOMAINS = frozenset({
    "www",
    "api",
    "app",
    "admin",
    "dashboard",
    "static",
    "media",
    "mail",
    "ftp",
    "blog",
    "help",
    "docs",
    "support",
    "status",
    "cdn",
})


def _extract_subdomain(host):
    """Return the leading subdomain segment when `host` matches one of
    the configured apex hosts. Returns "" for the apex itself, an IP,
    or any unmatched host."""
    if not host:
        return ""
    # Strip port if present.
    host = host.split(":", 1)[0].lower()
    # IP literals (4 numeric groups) — never have subdomains.
    if all(part.isdigit() for part in host.split(".")):
        return ""
    for apex in SUBDOMAIN_APEX_HOSTS:
        if host == apex:
            return ""
        suffix = "." + apex
        if host.endswith(suffix):
            head = host[: -len(suffix)]
            # Only the FIRST segment is the subdomain — multi-level
            # subdomains (foo.bar.gymflow.com) are treated as foo.
            return head.split(".")[0]
    return ""


class SubdomainSiteMiddleware(MiddlewareMixin):
    """Rewrites `host=<slug>.gymflow.com, path=/foo` → `path=/p/<slug>/foo`
    so the existing public site routing serves it. No-op for the apex
    domain, reserved subdomains, and unknown subdomains."""

    def process_request(self, request):
        host = request.get_host()
        subdomain = _extract_subdomain(host)
        if not subdomain or subdomain in RESERVED_SUBDOMAINS:
            return None

        # Lazy import — middleware is imported at startup, models
        # may not be ready yet at module-load time.
        from apps.users.models import TrainerProfile
        if not TrainerProfile.objects.filter(slug=subdomain).exists():
            # Unknown subdomain — let it fall through to whatever the
            # apex would do. (Probably 404; that's fine.)
            return None

        # Rewrite path. Idempotent: if a path is already prefixed (e.g.
        # if some other middleware already resolved it) we don't double-up.
        prefix = f"/p/{subdomain}"
        current = request.path_info
        if current.startswith(prefix + "/") or current == prefix:
            return None

        # `request.path_info` is what the URL resolver looks at.
        # `request.path` stays the original for logging / templates.
        if not current.startswith("/"):
            current = "/" + current
        request.path_info = (prefix + current).rstrip("/") + "/"
        # Stash the trainer for any view that wants to know it came in
        # via subdomain (e.g. for analytics or canonical URL hints).
        request.subdomain_trainer_slug = subdomain
        return None
