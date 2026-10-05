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

```javascript
// alamo-paddle-webhook — Cloudflare Worker.
// Paddle subscription events -> Buttondown subscribers.
// Variables (Settings → Variables and Secrets, encrypt the secrets):
//   PADDLE_WEBHOOK_SECRET (pdl_ntfset_...), PADDLE_API_KEY,
//   BUTTONDOWN_API_KEY, PADDLE_ENV ("production" | "sandbox")

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

async function hmacHex(key, msg) {
  const k = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(key),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign(
    "HMAC", k, new TextEncoder().encode(msg)));
  return [...mac].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function verify(raw, headers, secret) {
  // Paddle-Signature: ts=<unix>;h1=<hex>  (multiple h1 during rotation)
  // Signed payload is "<ts>:<raw body>" — colon, not period.
  // Docs: https://developer.paddle.com/webhooks/signature-verification
  const sig = headers.get("paddle-signature") ?? "";
  const parts = sig.split(";").map((p) => p.trim());
  const ts = parts.find((p) => p.startsWith("ts="))?.slice(3);
  const h1s = parts.filter((p) => p.startsWith("h1=")).map((p) => p.slice(3));
  if (!ts || h1s.length === 0) return false;
  if (Math.abs(Date.now() / 1000 - Number(ts)) > 300) return false; // replay
  const expected = await hmacHex(secret, `${ts}:${raw}`);
  return h1s.some((h) => timingSafeEqual(expected, h));
}

async function customerEmail(customerId, env) {
  const base = env.PADDLE_ENV === "sandbox"
    ? "https://sandbox-api.paddle.com"
    : "https://api.paddle.com";
  const r = await fetch(`${base}/customers/${customerId}`, {
    headers: { Authorization: `Bearer ${env.PADDLE_API_KEY}` },
  });
  if (!r.ok) {
    console.error("paddle customer lookup", r.status);
    return null;
  }
  return (await r.json())?.data?.email ?? null;
}

async function buttondown(method, path, key, body) {
  // No subscriber IP is available from a webhook, so bypass Buttondown's
  // signup firewall (5/hr limit — fine at this scale).
  const r = await fetch(`https://api.buttondown.com/v1${path}`, {
    method,
    headers: {
      Authorization: `Token ${key}`,
      "X-Buttondown-Bypass-Firewall": "true",
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) console.error("buttondown", r.status);
  return r.ok;
}

export default {
  async fetch(req, env) {
    const raw = await req.text();
    if (!(await verify(raw, req.headers, env.PADDLE_WEBHOOK_SECRET))
      return new Response("bad signature", { status: 401 });
    const evt = JSON.parse(raw);
    const type = evt.event_type ?? "";
    const customerId = evt.data?.customer_id;
    if (!customerId) return Response.json({ skipped: "no customer_id" });

    if (type === "subscription.activated" || type === "subscription.created") {
      const email = await customerEmail(customerId, env);
      if (email) await buttondown("POST", "/subscribers", env.BUTTONDOWN_API_KEY,
        { email_address: email, type: "regular", tags: ["alamo-estate-sales"] });
    } else if (type === "subscription.canceled") {
      // Stop future issues; keep them archived, not deleted.
      const email = await customerEmail(customerId, env);
      if (email) await buttondown("PATCH",
        `/subscribers/${encodeURIComponent(email)}`, env.BUTTONDOWN_API_KEY,
        { type: "unsubscribed" });
    }
    // subscription.updated / past_due: Paddle retries on its own; the
    // subscriber stays until the subscription actually cancels.
    return Response.json({ ok: true });
  },
};
```

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
