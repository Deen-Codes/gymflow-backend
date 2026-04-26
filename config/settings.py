import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret-key")
DEBUG = True
ALLOWED_HOSTS = ["*"]

# -------------------------------------------------------------------
# CSRF + proxy trust for Render deploy.
#
# Django 4.x requires `CSRF_TRUSTED_ORIGINS` to list HTTPS origins
# explicitly — without it, every browser POST to a form on the
# deployed site (including /portal/login/) gets rejected with
# "CSRF token from POST incorrect."
#
# `SECURE_PROXY_SSL_HEADER` tells Django to trust the X-Forwarded-Proto
# header that Render's load balancer sets, so it knows the request
# was originally HTTPS even though the inner Gunicorn talks plain HTTP.
# Without this Django thinks every request is HTTP and the secure
# cookie + CSRF host-match logic gets confused.
# -------------------------------------------------------------------
CSRF_TRUSTED_ORIGINS = [
    "https://gymflow-api-wxm9.onrender.com",
    "https://*.gymflow.coach",
    "https://gymflow.coach",
]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    "rest_framework",
    "rest_framework.authtoken",  # Phase 0: token auth for the iOS client

    "apps.users",
    "apps.workouts",
    "apps.progress",
    "apps.nutrition",
    "apps.sites",                # Phase 7: PT landing pages + signups
    "apps.payments",             # Phase 7.7.1: Stripe Connect
]


# -------------------------------------------------------------------
# Stripe Connect (Phase 7.7.1)
#
# All four values are read from env vars on Render. In dev we let
# them default to empty strings so the app boots without Stripe
# configured — the Connect button shows a "Set STRIPE_* env vars"
# warning instead of crashing the dashboard.
#
# Required for production:
#   STRIPE_SECRET_KEY       — sk_live_… or sk_test_…
#   STRIPE_PUBLISHABLE_KEY  — pk_live_… or pk_test_…
#   STRIPE_CLIENT_ID        — ca_… (from Connect Settings, NOT API keys)
#   STRIPE_WEBHOOK_SECRET   — whsec_…  (set when webhooks land next turn)
#
# Platform fee: 5% goes to GymFlow on every subscription. Tweak via
# STRIPE_APPLICATION_FEE_PERCENT (decimal — 5 = 5%).
# -------------------------------------------------------------------
STRIPE_SECRET_KEY        = os.environ.get("STRIPE_SECRET_KEY",      "")
STRIPE_PUBLISHABLE_KEY   = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_CLIENT_ID         = os.environ.get("STRIPE_CLIENT_ID",       "")
STRIPE_WEBHOOK_SECRET    = os.environ.get("STRIPE_WEBHOOK_SECRET",  "")
STRIPE_APPLICATION_FEE_PERCENT = float(
    os.environ.get("STRIPE_APPLICATION_FEE_PERCENT", "5")
)

# Where Stripe redirects the trainer back to after they grant access.
# Must match a redirect URI registered in your Stripe Connect settings.
STRIPE_OAUTH_REDIRECT_URI = os.environ.get(
    "STRIPE_OAUTH_REDIRECT_URI",
    "https://gymflow-api-wxm9.onrender.com/payments/oauth/callback/",
)


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",

    # Phase 7.5 — Rewrite `<slug>.gymflow.com` requests to /p/<slug>/
    # so the existing public site routing serves them. No-op on the
    # apex, on reserved subdomains (www, api, app...), and locally
    # unless /etc/hosts has `<slug>.localhost` entries.
    "apps.sites.middleware.SubdomainSiteMiddleware",
]


ROOT_URLCONF = "config.urls"


TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=True,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_I18N = True
USE_TZ = True


STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# This tells Django where your source static files live
STATICFILES_DIRS = [
    BASE_DIR / "static",
]

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "users.User"


# Resend email setup
EMAIL_BACKEND = "apps.users.email_backend.ResendEmailBackend"
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "onboarding@resend.dev")


# -------------------------------------------------------------------
# DRF — Phase 0: token auth becomes the primary mechanism for the iOS
# app, while the Django dashboard continues to use the session cookie.
#
# Both authentication classes are present so the same endpoints can be
# called either from the trainer dashboard (session) or from the iOS
# client (Authorization: Token <key>). Permission default stays
# IsAuthenticated; per-view AllowAny still works.
# -------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}
