# Stripe Connect — one-time setup

The code is shipped. To turn it on, you need to do five things in the
Stripe dashboard + Render env vars. ~15 minutes total.

## 1. Create a Stripe account (if you haven't)

https://dashboard.stripe.com/register — use your gymflow business
details. Stay in **test mode** for now (toggle top-right of dashboard);
flip to live mode after you've tested the full flow.

## 2. Activate Connect

Go to https://dashboard.stripe.com/connect/accounts/overview → click
**Get started** → choose **Platform or marketplace** as the
integration type → answer the wizard (your role: software platform
that connects coaches with clients).

Once activated you can move to step 3.

## 3. Get your Connect Client ID

Go to https://dashboard.stripe.com/test/settings/connect (test mode)
or https://dashboard.stripe.com/settings/connect (live).

Scroll to **Onboarding options** → make sure **OAuth** is enabled.

Copy the **Client ID** (starts with `ca_…`). This is **NOT** an API
key — it's the OAuth client identifier.

## 4. Add the redirect URI

In the same Connect Settings page, find **Redirect URIs**. Click
**Add URI** and paste:

```
https://gymflow-api-wxm9.onrender.com/payments/oauth/callback/
```

Save.

(For local dev, also add `http://127.0.0.1:8000/payments/oauth/callback/`.)

## 5. Get your API keys

https://dashboard.stripe.com/test/apikeys — copy:
- **Publishable key** (`pk_test_…`)
- **Secret key** (`sk_test_…`) — click "Reveal"

## 6. Set Render env vars

Go to your Render dashboard → gymflow-api service → **Environment**.
Add these three (the webhook secret comes in the next batch):

| Key                       | Value                              |
|---------------------------|------------------------------------|
| `STRIPE_SECRET_KEY`       | `sk_test_…`                        |
| `STRIPE_PUBLISHABLE_KEY`  | `pk_test_…`                        |
| `STRIPE_CLIENT_ID`        | `ca_…`                             |

Optional:
| Key                              | Default | Use when                     |
|----------------------------------|---------|------------------------------|
| `STRIPE_APPLICATION_FEE_PERCENT` | `5`     | tweak the GymFlow cut        |
| `STRIPE_OAUTH_REDIRECT_URI`      | (auto)  | running on a custom domain   |

Save → Render will auto-redeploy with the new env.

## 7. Run migrations

Render's deploy hook should run `python manage.py migrate`
automatically. If not, hop into the **Shell** tab and run it manually.
You should see migrations `users.0004_trainerprofile_stripe_user_id`
and `payments.0001_initial` apply.

## 8. Test the connect flow

1. Log into your trainer dashboard
2. Settings → scroll to "Stripe Connect"
3. Click **Connect with Stripe →**
4. You'll bounce to Stripe — sign in (or sign up — Stripe walks you
   through creating a connected account)
5. Authorise GymFlow when prompted
6. You'll land back on Settings with a "Stripe connected" toast and
   the badge flips to **Connected** with your `acct_…` ID showing

## What's next

This batch only ships the connect flow. The actual Subscribe-from-
public-site → Stripe Checkout → webhook → auto-create-client flow
ships in the next batch. After that, your tiers go from "talk to me
manually" to "automatic billing on autopilot".

When you're ready to flip to live mode, swap the test keys in Render
env vars to live keys (`sk_live_…`, `pk_live_…`, live `ca_…`),
re-add the redirect URI in live-mode Connect settings, and reconnect
your Stripe account from the trainer dashboard.
