# gymflow.coach — DNS + Render wiring

Domain bought at GoDaddy (year 1 cheap), DNS delegated to Cloudflare
(free, faster, free Email Routing), traffic terminates at Render.

```
Customer → Cloudflare (DNS + edge cache + SSL) → Render (Django + Postgres)
```

After 60 days at GoDaddy you can transfer the registration to
Cloudflare for ~£47 (adds 1 year). Until then, only the DNS lives at
Cloudflare — registration stays at GoDaddy.

## 1. Add gymflow.coach to Cloudflare (free)

1. https://dash.cloudflare.com/sign-up — create a free Cloudflare account.
2. **Add a site** → enter `gymflow.coach` → pick **Free** plan.
3. Cloudflare scans for existing DNS records. Since this is a fresh
   GoDaddy registration there'll be nothing — that's fine, hit
   **Continue**.
4. Cloudflare gives you 2 nameservers like
   `kimi.ns.cloudflare.com` and `ns1.cloudflare.com`. **Copy both.**

## 2. Point GoDaddy at Cloudflare's nameservers

1. https://dcc.godaddy.com/control/portfolio → click **gymflow.coach**.
2. Find **Nameservers** → **Change**.
3. Choose **I'll use my own nameservers**.
4. Paste the two Cloudflare nameservers from step 1.4. Save.
5. Back in Cloudflare, click **Done, check nameservers**.

DNS propagation: 5–60 min. Cloudflare emails you when it's ready.

## 3. Add the custom domain in Render

While DNS propagates, queue up Render so it's ready the moment
nameservers flip:

1. Render dashboard → **gymflow-api** service → Settings → **Custom Domains**.
2. **Add Custom Domain** → `gymflow.coach` → Save.
3. Render shows you a target — usually a CNAME like `gymflow-api-wxm9.onrender.com`
   plus an A record for the apex. **Note both values.**
4. Repeat for `www.gymflow.coach`.

## 4. Add DNS records at Cloudflare

Back in Cloudflare → gymflow.coach → **DNS** → **Records** → **Add record**.

For the **apex** (`gymflow.coach` itself), Render needs an A record
because root domains can't be CNAMEs:

| Type | Name | Target                          | Proxy status     |
|------|------|---------------------------------|------------------|
| A    | @    | (Render's apex IP from step 3.3)| **DNS only** ⚪  |

For **www**:

| Type  | Name | Target                                | Proxy status    |
|-------|------|---------------------------------------|-----------------|
| CNAME | www  | gymflow-api-wxm9.onrender.com         | **DNS only** ⚪ |

**IMPORTANT:** keep proxy status as **DNS only (grey cloud)** until
Render has issued the TLS cert. If you proxy through Cloudflare too
early, Render's Let's Encrypt cert challenge fails because Cloudflare
intercepts it. Once Render shows "Verified" + "Certificate issued"
you can flip the cloud orange to enable Cloudflare's CDN/WAF.

## 5. Wait for Render's cert

In Render's Custom Domains panel both entries should go from
**Verifying...** → **Verified** → **Certificate issued** within 5–15
min after DNS propagates. If it stalls 30+ min, click **Retry**.

You'll know it worked when `https://gymflow.coach` loads your landing
page in a browser.

## 6. Then do the Stripe + iOS swap

See **STRIPE_SETUP.md → Phase 7.7.2** for steps 13–18:
- Add `gymflow.coach` redirect URI in Stripe Connect
- Set `STRIPE_OAUTH_REDIRECT_URI` env var on Render
- Reconnect Stripe from the trainer dashboard
- Add a new webhook destination at `gymflow.coach/payments/webhooks/stripe/`
- Update iOS `APIConfig.swift` `localOverride` to nil

## 7. (Optional) Free email routing

Once gymflow.coach is on Cloudflare:
1. Cloudflare → gymflow.coach → **Email** → **Email Routing** → Enable.
2. Add a rule: `you@gymflow.coach` → forward to your existing inbox.
3. Cloudflare adds the required MX/TXT records for you.
4. In Gmail/whatever, set up "Send mail as you@gymflow.coach" so
   replies look like they came from your custom address.

Free, no per-mailbox fees, perfect for a solo founder.

## 8. (After 60 days) Transfer registration to Cloudflare

To save ~£40/yr ongoing:
1. GoDaddy → gymflow.coach → unlock the domain + request the
   **transfer auth code** (also called EPP code).
2. Cloudflare dashboard → **Domain Registration** → **Transfer Domains**
   → enter gymflow.coach + the auth code.
3. Pay ~£47 (= 1 year renewal at at-cost pricing); this adds a year
   to the existing expiry.
4. Approve the transfer request that arrives by email.
5. ~5–7 days later the domain is fully on Cloudflare.

GoDaddy will try to talk you out of it on the way out. Ignore.

## Troubleshooting

**"This site can't be reached" after nameserver flip** — DNS hasn't
propagated yet. `dig gymflow.coach NS +short` from your Mac terminal
should show Cloudflare's nameservers once it has.

**Render stuck on "Verifying..."** — Make sure proxy status at
Cloudflare is grey cloud (DNS only), not orange. If you accidentally
went orange, flip it grey and click **Retry** in Render.

**"NET::ERR_CERT_AUTHORITY_INVALID"** — Render's cert hasn't issued
yet. Wait 15 min and refresh.

**Existing trainer pages 404 at gymflow.coach but work at the
Render URL** — Django's `ALLOWED_HOSTS` rejects the new host.
`config/settings.py` has `ALLOWED_HOSTS = ["*"]` already, so this
shouldn't happen — but if it does, double-check the deploy actually
re-rolled.
