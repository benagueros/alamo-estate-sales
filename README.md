# Alamo Estate Deals — pipeline

San Antonio estate-sale newsletter: every morning, scrape the week's SA
listings, identify candidate finds, pull eBay **sold** comps via trawl.dev,
assemble the issue in deal-threshold format, and stage it for send.
$5/mo via Paddle, delivered via Buttondown. Nothing publishes without a
human review.

## How it runs (GitHub Actions)

| Workflow | Trigger | What it does |
|---|---|---|
| `build-issue.yml` | daily ~6am CDT + manual | scrape → identify → comps → draft HTML → commit to `drafts/` |
| `send-issue.yml` | manual dispatch | publishes a draft to Buttondown as a **draft email** — Ben reviews and hits send in the dashboard |
| `comp-check.yml` | manual dispatch | one-off comp lookup for ad-hoc finds (the original tool) |

The comp step runs on Actions because trawl.dev's API is intermittently
unreachable from the Hatch VM (verified Oct 4, 2026). The first Actions run
proves the `TRAWL_API_KEY` secret end to end.

## Pipeline scripts

- `pipeline/scrape.py` — estatesales.net SA listings → `sales.json`.
  The site is Angular/client-rendered, so the scraper drives headless
  Chromium via Playwright (selectors verified against the live DOM Oct 4,
  2026). Polite: one metro area, once daily, ~2s between pages; never
  touches /api/* (disallowed in robots.txt).
- `pipeline/identify.py` — description text mining → `finds.json`
  (max 4 candidates/issue; brand/model signal dictionary).
- `comps.py` — trawl.dev `/sold` → relevance filter → medians →
  `out/comp-brief.json`. `--verify` does sold-state verification via
  `/item` (Ben's rule: required before any comp publishes), best-offer
  comps first. Monthly credit guard lives in the workflow (`state.json`,
  pauses at 200 of the 250 free credits).
- `pipeline/issue.py` — `sales.json` + `finds.json` + comp brief →
  `drafts/YYYY-MM-DD.html` in deal-threshold format (what it sells for +
  the price that makes it a deal; thin datasets flagged, never faked).
- `pipeline/send.py` — draft HTML → Buttondown draft email.

## Billing

Paddle $5/mo product (Polar rejected this use case; Stripe is off the
table); `ops/paddle-webhook.md` has the Cloudflare Worker that subscribes
buyers to Buttondown automatically.

## Setup

See `SETUP.md` — Ben's checklist (repo, secrets, workflows via web UI,
Buttondown, Paddle, first run).

## Key rules (Ben, Oct 2026)

- Deal-threshold format is THE format: no asking prices, ever.
- Sold-state verification required before any comp publishes.
- Free-tier-first: trawl.dev 250 credits/mo must cover the pipeline.
