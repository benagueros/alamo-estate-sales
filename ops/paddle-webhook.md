# Paddle → Buttondown wiring for Alamo Estate Deals ($5/mo)

Paid status lives in Paddle; the subscriber list lives in Buttondown. This
webhook keeps them in sync. It mirrors the TripSplit polar-webhook pattern
(same Supabase project, new function — TripSplit is untouched).

Why Paddle: Polar rejected this use case outright ("tied to physical goods
commerce"), and Stripe is off the table. Paddle is the closest Polar analog —
merchant of record (handles sales tax, chargebacks), subscriptions, no
monthly fee. Cost on $5/mo: 5% + $0.50 = $0.75 per payment. Lemon Squeezy
would fit too, but it's Stripe-owned since 2024, so it's out on the same
grounds as Stripe.

## Ben-side setup

1. **Paddle**: sign up at paddle.com, create product **"Alamo Estate Deals"**
   with a $5/month recurring price. Grab the checkout link.
2. **Paddle → notifications**: Developer tools → Notifications → New
   destination. URL:
   `https://<your-supabase-project>.supabase.co/functions/v1/alamo-paddle-webhook`
   Subscribe to `subscription.created`, `subscription.activated`,
   `subscription.updated`, `subscription.canceled`. Copy the signing secret
   (`pdl_ntfset_…`).
3. **Paddle → API key**: Developer tools → API keys → create one (the
   webhook looks up the buyer's email via the Paddle API).
4. **Buttondown**: newsletter + API key (same as before).
5. **Supabase** (dashboard > Edge Functions):
   - New function `alamo-paddle-webhook`, paste `index.ts` below, deploy.
   - Secrets: `PADDLE_WEBHOOK_SECRET` (step 2), `PADDLE_API_KEY` (step 3),
     `BUTTONDOWN_API_KEY` (step 4).
6. **Checkout link**: point your subscribe button at the Paddle checkout
   link. After payment, Paddle fires the webhook and the buyer lands on
   your Buttondown list automatically.

## index.ts

```typescript
// alamo-paddle-webhook: Paddle subscription events -> Buttondown subscribers.
// Secrets: PADDLE_WEBHOOK_SECRET (pdl_ntfset_...), PADDLE_API_KEY,
//          BUTTONDOWN_API_KEY (Supabase dashboard > Edge Functions).

const WEBHOOK_SECRET = Deno.env.get("PADDLE_WEBHOOK_SECRET")!;
const PADDLE_API_KEY = Deno.env.get("PADDLE_API_KEY")!;
const BUTTONDOWN_KEY = Deno.env.get("BUTTONDOWN_API_KEY")!;

const ok = (o: unknown) => new Response(JSON.stringify(o), {
  status: 200, headers: { "Content-Type": "application/json" } });

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

async function hmacHex(key: string, msg: string): Promise<string> {
  const k = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(key),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign(
    "HMAC", k, new TextEncoder().encode(msg)));
  return [...mac].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function verify(raw: string, headers: Headers): Promise<boolean> {
  // Paddle-Signature: ts=<unix>;h1=<hex>  (multiple h1 during rotation)
  // Signed payload is "<ts>:<raw body>" — colon, not period.
  // Docs: https://developer.paddle.com/webhooks/signature-verification
  const sig = headers.get("paddle-signature") ?? "";
  const ts = sig.split(";").map((p) => p.trim())
    .find((p) => p.startsWith("ts="))?.slice(3);
  const h1s = sig.split(";").map((p) => p.trim())
    .filter((p) => p.startsWith("h1=")).map((p) => p.slice(3));
  if (!ts || h1s.length === 0) return false;
  if (Math.abs(Date.now() / 1000 - Number(ts)) > 300) return false; // replay
  const expected = await hmacHex(WEBHOOK_SECRET, `${ts}:${raw}`);
  return h1s.some((h) => timingSafeEqual(expected, h));
}

async function customerEmail(customerId: string): Promise<string | null> {
  // Subscription webhooks carry customer_id, not the email — look it up.
  const r = await fetch(`https://api.paddle.com/customers/${customerId}`, {
    headers: { "Authorization": `Bearer ${PADDLE_API_KEY}` },
  });
  if (!r.ok) {
    console.error("paddle customer lookup", r.status,
      await r.text().catch(() => ""));
    return null;
  }
  return (await r.json())?.data?.email ?? null;
}

async function buttondown(method: string, path: string, body?: unknown) {
  // No subscriber IP is available from a webhook, so bypass Buttondown's
  // signup firewall (5/hr limit — fine at this scale).
  const r = await fetch(`https://api.buttondown.com/v1${path}`, {
    method,
    headers: {
      "Authorization": `Token ${BUTTONDOWN_KEY}`,
      "X-Buttondown-Bypass-Firewall": "true",
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) console.error("buttondown", r.status, await r.text().catch(() => ""));
  return r.ok;
}

Deno.serve(async (req) => {
  const raw = await req.text();
  if (!(await verify(raw, req.headers)))
    return new Response("bad signature", { status: 401 });
  const evt = JSON.parse(raw);
  const type: string = evt.event_type ?? "";
  const customerId: string | undefined = evt.data?.customer_id;
  if (!customerId) return ok({ skipped: "no customer_id" });

  if (type === "subscription.activated" || type === "subscription.created") {
    const email = await customerEmail(customerId);
    if (email) await buttondown("POST", "/subscribers",
      { email_address: email, type: "regular", tags: ["alamo-estate-sales"] });
  } else if (type === "subscription.canceled") {
    // Stop future issues; keep them archived, not deleted.
    const email = await customerEmail(customerId);
    if (email) await buttondown("PATCH",
      `/subscribers/${encodeURIComponent(email)}`, { type: "unsubscribed" });
  }
  // subscription.updated / past_due: Paddle retries on its own; the
  // subscriber stays until the subscription actually cancels.
  return ok({ ok: true });
});
```

## Notes
- First real payment is the end-to-end test (same as TripSplit's money loop):
  buy it yourself for $5, confirm the Buttondown subscriber appears, then
  cancel in Paddle and confirm you're unsubscribed.
- If Paddle's customer-lookup shape differs, the function logs the error —
  check Edge Function logs on the first sale.
- Dunning: Paddle retries failed renewals on its own; the subscriber stays
  until the subscription actually cancels (same open question as TripSplit —
  Ben hasn't decided immediate vs. end-of-period downgrade there either).
