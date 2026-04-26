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

---

## Webhook setup (Phase 7.7.1 batch 2)

The Subscribe button on the public PT site now creates a real Stripe
Checkout session. When a customer pays, Stripe needs to call back
into our app via a webhook so we can auto-create their User +
ClientProfile and record the subscription.

### 9. Add the webhook endpoint in Stripe

In Stripe dashboard → **Developers → Webhooks** → click **Add endpoint**.

- **Endpoint URL:**
  ```
  https://gymflow-api-wxm9.onrender.com/payments/webhooks/stripe/
  ```
- **Listen to events on:** *Connected accounts* (NOT "Your account" —
  the events fire on the trainer's connected account).
- **Events to send:**
  - `checkout.session.completed`
  - `customer.subscription.created`
  - `customer.subscription.updated`
  - `customer.subscription.deleted`
  - `invoice.payment_failed`

Click **Add endpoint**. You'll see the new webhook in the list.

### 10. Copy the signing secret

Click into the webhook → reveal the **Signing secret** (`whsec_…`).
This is what verifies that calls to `/payments/webhooks/stripe/`
actually came from Stripe and weren't an attacker.

### 11. Add it to Render env

| Key                       | Value                              |
|---------------------------|------------------------------------|
| `STRIPE_WEBHOOK_SECRET`   | `whsec_…`                          |

Render will auto-redeploy with the new env. Done.

### 12. Test the full flow

1. Make sure you've **connected Stripe** from your trainer Settings page.
2. Open your public site at `gymflow.coach/p/<your-slug>/`.
3. Scroll to your Pricing section and click **Subscribe** on a tier.
4. You'll be redirected to Stripe Checkout (test mode → use card
   `4242 4242 4242 4242`, any future expiry, any CVC, any postcode).
5. Pay → Stripe redirects you back to `/p/<slug>/subscribe/thanks/`.
6. Within a few seconds, the webhook fires. Check Render logs for
   `[Stripe webhook] ✅ Subscribed <username> to <plan name>`.
7. Hop into your trainer dashboard → **Clients** → you should see
   the new client in your roster.

If the webhook fails, the Stripe webhook log (back in Stripe → Webhooks)
shows the response body — that's where any errors will surface.

---

## Domain swap — gymflow.coach (Phase 7.7.2)

Once Cloudflare DNS is live and Render has issued a TLS cert for
`https://gymflow.coach`, you need to repoint Stripe at the new domain
or OAuth + webhooks will keep firing at the Render URL.

### 13. Add the new redirect URI in Stripe Connect

Stripe → Settings → **Connect Settings** → Redirect URIs → **Add URI**:

```
https://gymflow.coach/payments/oauth/callback/
```

Keep the old `https://gymflow-api-wxm9.onrender.com/payments/oauth/callback/`
URI in the list for now — it doesn't hurt and gives you a fallback if
DNS goes sideways.

### 14. Tell Render to redirect to the new URL

Render dashboard → gymflow-api → Environment → add (or update):

| Key                         | Value                                                |
|-----------------------------|------------------------------------------------------|
| `STRIPE_OAUTH_REDIRECT_URI` | `https://gymflow.coach/payments/oauth/callback/`     |

Save → Render redeploys.

### 15. Reconnect Stripe from the dashboard

The old OAuth grant has the Render URL baked in. After step 14:
1. Trainer dashboard → Settings → Stripe Connect → **Disconnect**.
2. Click **Connect with Stripe →**.
3. Complete the OAuth dance — you'll land back on `gymflow.coach/dashboard/settings/`.
4. Confirm the badge is back to "Connected" with the same `acct_…` ID.

### 16. Add the webhook destination on the new domain

Stripe → Developers → **Webhooks** → **Add destination**:

- **Endpoint URL:** `https://gymflow.coach/payments/webhooks/stripe/`
- **Listen to events on:** Connected accounts
- Same five events:
  - `checkout.session.completed`
  - `customer.subscription.created`
  - `customer.subscription.updated`
  - `customer.subscription.deleted`
  - `invoice.payment_failed`

Reveal the new signing secret → copy `whsec_…` → update `STRIPE_WEBHOOK_SECRET`
on Render. (You can delete the old Render-URL destination once the
new one is verified working.)

### 17. Update the iOS app

`GymFlow/Services/APIConfig.swift` → set `localOverride` to nil (or to
`"https://gymflow.coach"`). Rebuild & install on your phone.

### 18. Smoke test the full flow

1. Open `https://gymflow.coach/p/<your-slug>/` in a browser.
2. Subscribe → pay with `4242 4242 4242 4242`.
3. Render logs should show the webhook hit on the new path.
4. iOS app should still log in and load Home.
