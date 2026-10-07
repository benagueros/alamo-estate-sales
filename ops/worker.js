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
  if (!r.ok) throw new Error(`paddle customer lookup ${r.status}`);
  const email = (await r.json())?.data?.email ?? null;
  if (!email) throw new Error("paddle customer has no email");
  return email;
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
  if (!r.ok) throw new Error(`buttondown ${r.status} ${path}`);
  return true;
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
      // A failure here must NOT return 200: Paddle only retries on 5xx,
      // and a 200 would silently drop a paying subscriber.
      let email;
      try {
        email = await customerEmail(customerId, env);
        await buttondown("POST", "/subscribers", env.BUTTONDOWN_API_KEY,
          { email_address: email, type: "regular", tags: ["alamo-estate-sales"] });
      } catch (e) {
        console.error("provision failed:", e.message);
        return new Response("subscriber provisioning failed", { status: 502 });
      }
    } else if (type === "subscription.canceled") {
      // Stop future issues; keep them archived, not deleted.
      // Same 5xx-on-failure rule: the cancel must eventually be processed.
      try {
        const email = await customerEmail(customerId, env);
        await buttondown("PATCH",
          `/subscribers/${encodeURIComponent(email)}`, env.BUTTONDOWN_API_KEY,
          { type: "unsubscribed" });
      } catch (e) {
        console.error("cancel failed:", e.message);
        return new Response("cancel processing failed", { status: 502 });
      }
    }
    // subscription.updated / past_due: Paddle retries on its own; the
    // subscriber stays until the subscription actually cancels.
    return Response.json({ ok: true });
  },
};
