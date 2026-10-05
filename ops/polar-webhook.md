# Polar → Buttondown wiring for Alamo Estate Deals ($5/mo)

Paid status lives in Polar; the subscriber list lives in Buttondown. This
webhook keeps them in sync. It mirrors TripSplit's `polar-webhook` edge
function (same Supabase project, new function — TripSplit is untouched).

## Ben-side setup

1. **Polar**: create product "Alamo Estate Deals", $5/mo recurring (same org
   "TripSplit.us" is fine, or a new org — your call).
2. **Polar → webhook**: add endpoint URL
   `https://<your-supabase-project>.supabase.co/functions/v1/alamo-polar-webhook`
   subscribed to `subscription.created`, `subscription.updated`,
   `subscription.canceled`, `subscription.revoked`. Copy the signing secret.
3. **Buttondown**: create the newsletter (buttondown.com), then API key from
   the settings page → you'll need it for the repo secret `BUTTONDOWN_API_KEY`
   too.
4. **Supabase** (dashboard > Edge Functions):
   - New function `alamo-polar-webhook`, paste `index.ts` below, deploy.
   - Secrets: `POLAR_WEBHOOK_SECRET` (step 2), `BUTTONDOWN_API_KEY` (step 3).
5. **Checkout link**: point your subscribe button at the Polar checkout link
   for the $5 product. After payment, Polar fires the webhook and the buyer
   lands on your Buttondown list automatically.

## index.ts

```typescript
// alamo-polar-webhook: Polar subscription events -> Buttondown subscribers.
// Secrets: POLAR_WEBHOOK_SECRET, BUTTONDOWN_API_KEY (Supabase dashboard).

const WEBHOOK_SECRET = Deno.env.get("POLAR_WEBHOOK_SECRET")!;
const BUTTONDOWN_KEY = Deno.env.get("BUTTONDOWN_API_KEY")!;

const PAID = new Set(["active", "trialing"]);
const ENDED = new Set(["canceled", "expired", "revoked"]);

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

async function verify(raw: string, headers: Headers): Promise<boolean> {
  // Standard Webhooks: signature = v1,<base64 HMAC-SHA256("<id>.<ts>.<body>")>
  const id = headers.get("webhook-id") ?? "";
  const ts = headers.get("webhook-timestamp") ?? "";
  const sig = (headers.get("webhook-signature") ?? "").replace(/^v1,/, "");
  const msg = `${id}.${ts}.${raw}`;
  const enc = new TextEncoder();
  const keys: Uint8Array[] = [enc.encode(WEBHOOK_SECRET)];
  const stripped = WEBHOOK_SECRET.startsWith("whsec_")
    ? WEBHOOK_SECRET.slice(6) : WEBHOOK_SECRET;
  try {
    const bin = atob(stripped);
    const kb = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) kb[i] = bin.charCodeAt(i);
    keys.push(kb);
  } catch { /* not base64, skip */ }
  for (const k of keys) {
    const key = await crypto.subtle.importKey("raw", k,
      { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const mac = new Uint8Array(await crypto.subtle.sign(
      "HMAC", key, enc.encode(msg)));
    if (timingSafeEqual(btoa(String.fromCharCode(...mac)), sig)) return true;
  }
  return false;
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
  const type: string = evt.type ?? "";
  const sub = evt.data ?? {};
  // Polar puts the buyer email at data.customer.email (subscription events)
  // or data.customer_email (checkout events) — read defensively.
  const email: string | undefined =
    sub?.customer?.email ?? sub?.customer_email ?? sub?.email;
  if (!email) return new Response(JSON.stringify({ ok: true, skipped: true }),
    { headers: { "Content-Type": "application/json" } });

  if (type.startsWith("subscription.")) {
    const status: string = sub.status ?? "";
    if (PAID.has(status)) {
      await buttondown("POST", "/subscribers",
        { email_address: email, type: "regular", tags: ["alamo-estate-deals"] });
    } else if (ENDED.has(status)) {
      // Stop future issues; keep them archived, not deleted.
      await buttondown("PATCH", `/subscribers/${encodeURIComponent(email)}`,
        { type: "unsubscribed" });
    }
  } else if (type === "checkout.completed" || type === "order.completed") {
    await buttondown("POST", "/subscribers",
      { email_address: email, type: "regular", tags: ["alamo-estate-deals"] });
  }
  return new Response(JSON.stringify({ ok: true }),
    { headers: { "Content-Type": "application/json" } });
});
```

## Notes
- First real payment is the end-to-end test (same as TripSplit's money loop):
  buy it yourself for $5, confirm the Buttondown subscriber appears, then
  refund/cancel in Polar.
- If Buttondown's PATCH shape for unsubscribing differs, the function logs
  the error — check Edge Function logs on the first churn.
- Unpaid/dunning: Polar retries on its own; the subscriber stays until the
  subscription actually cancels (Ben's TripSplit dunning decision applies
  here too — still open).
