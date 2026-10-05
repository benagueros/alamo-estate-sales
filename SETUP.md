# Alamo Estate Deals — setup checklist

All the code is written. These are your moves, in order. Nothing here
spends money except the $5 test subscription at the end (refundable).

## 1. GitHub repo
- [x] Repo **`benagueros/alamo-estate-sales`** created; pipeline code pushed.
- [ ] Add the three workflow files **via the web UI** (the integration can't
      push `.github/workflows/**` — same limitation as TripSplit):
      copy `workflow/build-issue.yml` → `.github/workflows/build-issue.yml`,
      `workflow/send-issue.yml` → `.github/workflows/send-issue.yml`,
      `workflow/comp-check.yml` → `.github/workflows/comp-check.yml`.

## 2. Secrets (repo Settings → Secrets and variables → Actions)
- [ ] `TRAWL_API_KEY` — your trawl.dev key (already in your vault; paste the
      raw key here — GitHub encrypts it).
- [ ] `BUTTONDOWN_API_KEY` — from step 3.

## 3. Buttondown (the email sender)
- [ ] Create account + newsletter at buttondown.com (free tier covers the start).
- [ ] API key: Settings → API → copy into the repo secret above.

## 4. Paddle ($5/mo billing — Polar rejected this use case, Stripe is out)
- [ ] Sign up at paddle.com; create product **"Alamo Estate Deals"**,
      $5/month recurring. (Cost: 5% + $0.50 per payment = $0.75 on $5.)
- [ ] Follow `ops/paddle-webhook.md`: add the notification destination,
      deploy the `alamo-paddle-webhook` edge function (copy-paste into the
      Supabase dashboard, same as TripSplit's functions), set its three
      secrets.
- [ ] Point your subscribe button/page at the Paddle checkout link.

## 5. First run (proves everything)
- [ ] Actions → **"Build daily issue"** → Run workflow. It scrapes SA
      listings, picks candidates, pulls trawl comps (this also proves the
      trawl key from GitHub's network), and commits `drafts/YYYY-MM-DD.html`.
- [ ] Read the draft. Edit it in the repo if you want (research notes are
      starter text — make them yours).
- [ ] Actions → **"Send issue"** → Run workflow, enter the draft path.
      It creates a **draft** in Buttondown — review it there, then hit send
      yourself. Nothing ever goes out unreviewed.
- [ ] Money loop: buy the $5 sub yourself → confirm you land on the
      Buttondown list → cancel in Paddle → confirm you're unsubscribed.

## 6. Leave running
- The build workflow runs daily ~6am CDT on its own. Each morning: read the
  draft, tweak, run Send issue, hit send in Buttondown. ~10 minutes.

## Credit budget (trawl.dev free tier = 250/mo, no card)
~4 finds/day × ~1 credit + verification ≈ 150–200/mo. The build workflow
tracks spend in `state.json` and pauses comp-fetching at 200 with 50 in
reserve. If you ever want more headroom: $20/mo = 3,000 credits.

## Upgrade paths (later, not now)
- Photo-based find discovery (vision over listing photos) → emits the same
  `finds.json`; `identify.py` has the seam marked.
- Interest-based editions (mid-century / tools / jewelry…) → filter finds
  per subscriber tag at send time.
- One-click send: `pipeline/send.py` has the `POST /emails/{id}/send` path
  commented — remove the human gate only when you trust the drafts.
