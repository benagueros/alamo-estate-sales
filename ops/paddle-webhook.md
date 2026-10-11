# Paddle → Buttondown wiring for Alamo Estate Deals ($5/mo)

Paid status lives in Paddle; the subscriber list lives in Buttondown. This
Cloudflare Worker keeps them in sync: it receives Paddle subscription
events, looks up the buyer's email, and subscribes/unsubscribes them in
Buttondown.

Why a Worker instead of Supabase: Ben's Supabase projects are maxed out on
the free tier, and TripSplit's project stays TripSplit's. He already has
Cloudflare (unspunwire.com), and Workers are free at this scale.

Why Paddle: Polar rejected this use case outright ("tied to physical goods
commerce"), and Stripe is off the table. Paddle is the closest Polar analog —
merchant of record (handles sales tax, chargebacks), subscriptions, no
monthly fee. Cost on $5/mo: 5% + $0.50 = $0.75 per payment. Lemon Squeezy
would fit too, but it's Stripe-owned since 2024, so it's out on the same
grounds as Stripe.

## Ben-side setup

1. **Paddle**: sign up at paddle.com, create product **"Alamo Estate Deals"**
   with a $5/month recurring price. Copy the price ID (`pri_...`).
2. **Worker**: Cloudflare dashboard → Workers & Pages → Create →
   Create Worker → name it `alamo-paddle-webhook` → Deploy → Edit code →
   paste `worker.js` below → Save and deploy.
3. **Worker variables**: worker → Settings → Variables and Secrets → Add:
   - `PADDLE_WEBHOOK_SECRET` = the secret from step 4 (encrypt it)
   - `PADDLE_API_KEY` = Paddle API key (encrypt it)
   - `BUTTONDOWN_API_KEY` = Buttondown API key (encrypt it)
   - `PADDLE_ENV` = `production` (plain text is fine; use `sandbox` for testing)
4. **Paddle → notifications**: Developer tools → Notifications → New
   destination. URL = your worker's URL
   (`https://alamo-paddle-webhook.<your-subdomain>.workers.dev` — shown on
   the worker's page). Subscribe to `subscription.created`,
   `subscription.activated`, `subscription.updated`, `subscription.canceled`.
   Copy the signing secret (`pdl_ntfset_…`) into the worker variable.
5. **Paddle → API key**: Developer tools → API keys → create one → into the
   worker variable. (The webhook looks up the buyer's email via the Paddle
   API — subscription events carry `customer_id`, not the email.)
6. **Paddle → checkout**: Developer tools → Authentication → client-side
   token (paste into the subscribe page, `docs/index.html`, with the price
   ID). Checkout → Checkout settings → default payment link = your
   subscribe page URL; add its domain under approved domains.
7. **Subscribe page**: `docs/index.html` in the repo (live via GitHub Pages
   at `https://benagueros.github.io/alamo-estate-sales/` once Pages is
   enabled: repo Settings → Pages → main → `/docs`). Paddle has no hosted
   checkout — the page loads the Paddle.js overlay.

## worker.js

**Source of truth: `ops/worker.js` in this repo.** Open it on GitHub and
copy the whole file (or fetch the raw URL), then paste into the Cloudflare
Worker editor → Save and deploy.

Do NOT use any saved, cached, or previously-downloaded copy of the worker —
it is updated in the repo (resubscribe handling, retry semantics, and
idempotency have all changed since earlier versions), and a stale paste
will silently deploy old behavior. When in doubt, re-copy from
`ops/worker.js` on main.

## Sandbox testing (recommended first)

Paddle's sandbox is separate — own products, keys, secrets, no real money.
Set worker variable `PADDLE_ENV` = `sandbox`, use the sandbox webhook
secret + API key, and in `docs/index.html` set `PADDLE_ENV = "sandbox"`
with the sandbox client-side token and price ID. Pay with a sandbox test
card, confirm the Buttondown subscriber appears, cancel in the sandbox
dashboard, confirm unsubscription. Then flip everything to production
values.

## Notes
- First real payment is the end-to-end test (same as TripSplit's money loop):
  buy it yourself for $5, confirm the Buttondown subscriber appears, then
  cancel in Paddle and confirm you're unsubscribed.
- If Paddle's customer-lookup shape differs, the worker logs the error —
  check the worker's logs on the first sale (Observability tab).
- Dunning: Paddle retries failed renewals on its own; the subscriber stays
  until the subscription actually cancels.
