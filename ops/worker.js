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
