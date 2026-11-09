#!/usr/bin/env python3
"""Assemble the daily Alamo Estate Deals issue (deal-threshold format).

Inputs: sales.json, finds.json, out/comp-brief.json (from comps.py).
Output: drafts/YYYY-MM-DD.html — the draft the writer reviews before send.

Format rule (Ben, Oct 2026): no asking prices. Most SA estate companies do
not publish per-item prices, so every find shows what it SELLS for (eBay
sold comps) plus the price that makes it a deal. Thin datasets are flagged
on the page, never faked.

The research note ships as a STARTER: the evidence quote plus a prompt for
the writer. The writer edits the draft before it sends — the send step is
manual dispatch, so nothing publishes unreviewed.

Usage:
    python3 issue.py sales.json finds.json out/comp-brief.json [--date YYYY-MM-DD]
"""
import datetime
import html
import json
import os
import sys

INTRO = ("Three finds from this week's San Antonio-area estate sales, each "
         "with real eBay sold comps. Most local companies don't publish "
         "per-item prices before the doors open — so instead of a sticker "
         "price, we give you the number that matters: what it actually sells "
         "for, and the price that makes it a deal.")

METHODOLOGY = ("Comps are from actual eBay sold listings (sold, not asking). "
               "Thin datasets are flagged, never faked. Photos belong to "
               "their estate-sale companies, linked from the original listings.")


def money(v):
    return f"${v:,.0f}"


def find_photo(find, sale):
    """The photo showing THIS find: vision photo_index when we have one
    (vision item or watchlist-located keyword item), else the sale cover."""
    urls = sale.get("photo_urls") or []
    if not urls:
        return ""
    try:
        idx = int(find.get("photo_index") or 0) - 1
    except (TypeError, ValueError):
        idx = -1
    if 0 <= idx < len(urls):
        return urls[idx]
    return urls[0]


def short_date(ds):
    try:
        m, d = (ds or "")[:10].split("-")[1:3]
        return f"{int(m)}/{int(d)}"
    except Exception:
        return ""


def sold_links_html(comp):
    """Links to the sold listings behind the summary: lowest, closest to
    the median, highest (deduped). Readers can spot-check the comps.

    Hard rule: when sold-state verification ran, ONLY the anchors that
    passed are linked. After a failed anchor is dropped and stats recompute,
    the new min/median/max were never /item-verified — linking them would
    publish unverified comps. When verification didn't run, falls back to
    min/median/max (the card discloses that; the writer is the control)."""
    v = comp.get("verification") or {}
    all_comps = [c for c in (comp.get("comps") or [])
                 if c.get("item_link")
                 and isinstance(c.get("sale_price"), (int, float))]
    if not all_comps:
        return ""
    if v.get("checked"):
        verified_ids = {p.get("item_id") for p in v.get("passed", [])}
        comps = [c for c in all_comps if c.get("item_id") in verified_ids]
    else:
        comps = all_comps
    if not comps:
        return ""
    median = comp.get("median") or 0
    by_price = sorted(comps, key=lambda c: c["sale_price"])
    rep = min(comps, key=lambda c: abs(c["sale_price"] - median))
    picks, seen = [], set()
    for c in (by_price[0], rep, by_price[-1]):
        key = c.get("item_id") or id(c)
        if key not in seen:
            seen.add(key)
            picks.append(c)
    picks.sort(key=lambda c: c["sale_price"])
    parts = []
    for c in picks:
        label = money(c["sale_price"])
        d = short_date(c.get("date_sold"))
        if d:
            label += f" · {d}"
        parts.append(
            f'<a href="{html.escape(c["item_link"])}">{label}</a>')
    return " · ".join(parts)


def is_verified_summary(s):
    """Strict verified-find test (Nov 6 review): a find counts as verified
    only if it has passing anchors, NO failed anchors, and n > 0.
    comps.py emits partial-failure summaries with passed AND failed
    anchors at n=0 — a passed-only check overclaims those as verified
    (intro says "each" while the card shows "No verified solds")."""
    if not s or s.get("n", 0) <= 0:
        return False
    v = s.get("verification") or {}
    return bool(v.get("passed")) and not v.get("failed")


def find_card(find, sale, summary, budget_skipped=False, brief_error=False):
    photo = find_photo(find, sale)
    comp = summary or {}
    n = comp.get("n", 0)
    v = comp.get("verification") or {}
    # Hard rule: comps publish ONLY if sold state was verified. If a find
    # has comps but verification did not run (no checked anchors, no error),
    # those comps are unverified — withhold stats, links, and thresholds
    # entirely, even though n > 0. Previously this branch rendered stats +
    # links with only a stripped writer-note ("treat comps as unconfirmed")
    # as disclosure, and the per-draft auto-send gate (verified_finds >= 1)
    # let a mixed draft ship the unverified find's data. Now the find
    # renders as withheld, so no unverified comp can publish via any path.
    verification_not_run = n > 0 and not v.get("checked") and not comp.get("error")
    if verification_not_run:
        n = 0
    # Defense-in-depth (Nov 5 review): comps.py now withholds stats on ANY
    # failed anchor (n=0), so a summary with n > 0 and failed anchors should
    # never arrive — but find_card must not trust that. If one does arrive
    # (stale/hand-edited brief, future comps.py change), withhold here too
    # instead of publishing stats with only a strippable writer-note.
    failed_anchors = v.get("failed") or []
    if n > 0 and failed_anchors:
        n = 0
    if n:
        comps_line = (f"{money(comp['min'])} · {money(comp['max'])} — "
                      f"median ≈ {money(comp['median'])} (n={n})")
        flags = comp.get("flags") or []
        if flags:
            comps_line += f" <em>({' ; '.join(html.escape(f) for f in flags)})</em>"
        v = comp.get("verification") or {}
        if v.get("checked"):
            npass = len(v.get("passed", []))
            nfail = len(v.get("failed", []))
            # Standing disclosure: stats span the full sold dataset; only
            # the anchors were /item-verified. Readers shouldn't infer every
            # comp behind the median was individually checked.
            vline = (f"Sold-state check: {npass}/{v['checked']} anchor comps "
                     f"confirmed (stats span the sold dataset; anchors verified)")
            if nfail:
                vline += (f' <span class="writer-note">(writer: {nfail} failed '
                          f'anchor(s) removed; remaining stats unverified — '
                          f'confirm before publishing)</span>')
            comps_line += f"<br>{vline}"
        links = sold_links_html(comp)
        if links:
            comps_line += f'<br><span class="soldlinks">Sold listings: {links}</span>'
        strong = max(1, round(comp["median"] * 0.85, -1))
        buy = max(1, round(comp["median"] * 0.70, -1))
        threshold = (f"Under ~{money(strong)} is strong. "
                     f"Under ~{money(buy)}, buy it on the spot.")
    else:
        v = comp.get("verification") or {}
        if verification_not_run:
            # Reader-visible (NOT a writer-note — send.py strips those).
            # Distinct from "No verified solds": verification didn't run at
            # all, so we don't know if solds exist, vs. verification ran and
            # found none. Stats/links/threshold withheld per hard rule.
            comps_line = ("Verification not run — stats and sold links withheld "
                          "until sold state is confirmed.")
        elif v.get("failed"):
            # (Nov 8 review): surface the per-anchor failure reasons — the
            # modal outcome is 0/3 or 1/3 verified, and the reason (e.g.
            # listing_state='active') is otherwise invisible from the repo.
            notes = "; ".join(
                f"{f.get('item_id', '?')}: {f.get('note', '?')}"
                for f in v["failed"])
            comps_line = (f"No verified solds — {len(v['failed'])} anchor comp(s) "
                          f"did not confirm as sold and were removed. "
                          f"<span class=\"writer-note\">(writer: do not publish "
                          f"without confirmed comps — {html.escape(notes)})</span>")
        elif comp.get("error"):
            comps_line = f"No comps ({html.escape(str(comp['error']))})."
        elif brief_error:
            # Missing/corrupt brief is a pipeline failure, not a no-market
            # signal. Don't let the reader infer the item has no eBay market.
            comps_line = ("Comp data unavailable — the comp lookup failed or "
                          "produced no usable data. Not a no-market signal.")
        elif budget_skipped:
            comps_line = ("Comps skipped — monthly trawl credit guard tripped. "
                          "<span class=\"writer-note\">(writer: not a no-market "
                          "signal; re-run when credits reset)</span>")
        else:
            comps_line = "No same-item solds in the dataset — query too narrow or no eBay market."
        flags = comp.get("flags") or []
        if flags:
            comps_line += f" <em>({' ; '.join(html.escape(f) for f in flags)})</em>"
        if verification_not_run:
            threshold = "No threshold: verification not run."
        else:
            threshold = "No threshold: needs a wider query or a different find."
    addr = sale.get('address', '') or (
        "Online auction" if sale.get('online') else "")
    parts = [sale.get('dates_text', ''), addr, sale.get('company', '')]
    sale_line = " · ".join(html.escape(p) for p in parts if p)
    auction_line = ""
    if sale.get("auction_url"):
        auction_line = (
            f'<p class="auction"><a href="{html.escape(sale["auction_url"])}">'
            f"Browse the auction lots →</a> "
            f'<span class="writer-note">(writer: check opening bids vs comps)</span></p>')
    if sale.get("phone"):
        sale_line += f" · {html.escape(sale['phone'])}"
    return f"""
    <div class="find">
      <div class="kicker">{html.escape(sale.get('title', '').upper())}</div>
      <h2>{html.escape(find['query'].title())}</h2>
      {f'<img src="{html.escape(photo)}" alt="{html.escape(find["query"])}">' if photo else ""}
      <p class="comps"><strong>Sold comps:</strong> {comps_line}</p>
      <p class="threshold"><strong>Deal threshold:</strong> {threshold} <span class="writer-note">(writer: adjust)</span></p>
      <p class="note"><strong>Why it matters:</strong> {"Spotted in the photos" if find.get("source") == "vision" else "Spotted in the listing"} — “{html.escape(find.get('evidence', ''))}” <span class="writer-note">(writer: add era/maker/value-driver note)</span></p>
      <p class="sale">{sale_line}<br><a href="{html.escape(sale.get('url', ''))}">View sale listing →</a></p>
      {auction_line}
    </div>"""


def build(sales, finds, brief, date_str, brief_error=False):
    by_id = {s.get("id"): s for s in sales}
    summaries = {s["id"]: s for s in brief.get("summaries", [])}
    budget_skipped = brief.get("budget_skipped", False)
    cards = []
    for find in finds:
        sale = by_id.get(find.get("sale_id"), {})
        cards.append(find_card(find, sale, summaries.get(find["id"]),
                               budget_skipped, brief_error))
    try:
        pretty = datetime.date.fromisoformat(date_str).strftime("%A, %B %-d, %Y")
    except ValueError:
        pretty = date_str
    # Footer honesty: the standard intro/methodology claim "real eBay sold
    # comps" is only true if at least one find has a verified comp. If
    # every find withheld stats (0 confirmed), say so instead of claiming
    # sold-listing comps the issue doesn't have.
    # "Each" honesty: INTRO claims EVERY find has real sold comps. That's
    # only true if ALL finds are verified — if some finds show "No verified
    # solds", the intro must not claim "each". Use a qualified intro for
    # mixed drafts.
    # (Nov 6 review): use the strict is_verified_summary test — passed-only
    # overclaims partial-failure finds (passed + failed anchors, n=0).
    find_summaries = [summaries.get(f.get("id")) for f in finds]
    any_verified = any(is_verified_summary(s) for s in find_summaries)
    all_verified = bool(find_summaries) and all(
        is_verified_summary(s) for s in find_summaries)
    if all_verified:
        intro = INTRO
        methodology = METHODOLOGY
    elif any_verified:
        intro = ("Finds from this week's San Antonio-area estate sales, "
                 "with real eBay sold comps where verified. Finds without "
                 "verified comps show no stats rather than unverified numbers.")
        # (Nov 5 review, L3): the mixed branch previously reused the
        # unqualified METHODOLOGY constant, which claims "Comps are from
        # actual eBay sold listings" for the whole issue even though some
        # finds have none. Qualify it to match the intro.
        methodology = ("Comps are from actual eBay sold listings (sold, not "
                       "asking) where verified. Finds without verified comps "
                       "show no stats rather than unverified numbers. Thin "
                       "datasets are flagged, never faked. Photos belong to "
                       "their estate-sale companies, linked from the original "
                       "listings.")
    else:
        intro = ("Finds from this week's San Antonio-area estate sales. "
                 "No verified eBay sold comps were available for these finds today — "
                 "stats are withheld rather than published unverified.")
        methodology = ("No verified sold-listing comps were available for this issue. "
                       "Photos belong to their estate-sale companies, linked from the "
                       "original listings.")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Alamo Estate Deals — {html.escape(pretty)}</title>
<style>
body{{font-family:Georgia,serif;background:#faf7f0;color:#1a1a1a;margin:0;padding:24px 12px}}
.wrap{{max-width:640px;margin:0 auto}}
header{{text-align:center;border-bottom:3px solid #b4552d;padding-bottom:16px;margin-bottom:24px}}
.wordmark{{font-size:28px;letter-spacing:3px;color:#b4552d;margin:0}}
.tagline{{font-style:italic;color:#555}}
.issue{{font-family:system-ui,sans-serif;font-size:13px;color:#777}}
.find{{background:#fff;border:1px solid #e5ddcf;border-radius:10px;padding:20px;margin-bottom:24px}}
.kicker{{font-family:system-ui,sans-serif;font-size:11px;letter-spacing:2px;color:#b4552d}}
.find h2{{margin:6px 0 12px;font-size:24px}}
.find img{{width:100%;border-radius:6px;margin-bottom:12px}}
.comps,.threshold,.note{{font-size:15px;line-height:1.55}}
.soldlinks{{font-family:system-ui,sans-serif;font-size:13px;color:#555}}
.soldlinks a{{color:#b4552d}}
.auction{{font-family:system-ui,sans-serif;font-size:13px;margin-top:8px}}
.auction a{{color:#b4552d}}
.sale{{font-family:system-ui,sans-serif;font-size:13px;color:#555;line-height:1.6}}
.sale a{{color:#b4552d}}
.writer-note{{color:#b4552d;font-style:italic}}
footer{{font-family:system-ui,sans-serif;font-size:12px;color:#888;border-top:1px solid #e5ddcf;padding-top:16px}}
</style></head>
<body><div class="wrap">
<header>
<p class="wordmark">ALAMO ESTATE DEALS</p>
<p class="tagline">Every find, every comp, every morning.</p>
<p class="issue">{html.escape(pretty)} · General edition · $5/mo</p>
</header>
<p><em>{intro}</em></p>
{''.join(cards) if cards else '<p>No finds cleared the bar today — see you tomorrow.</p>'}
<footer>
<p><strong>Alamo Estate Deals</strong> — $5/month. Every find, every comp, every morning.</p>
<p>{methodology}</p>
</footer>
</div></body></html>"""


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    sales = json.load(open(sys.argv[1]))
    finds = json.load(open(sys.argv[2]))
    brief_path = sys.argv[3]
    try:
        brief = json.load(open(brief_path))
        brief_error = False
    except (FileNotFoundError, json.JSONDecodeError):
        # Comp step skipped or failed: render finds with the n=0 path
        # rather than crashing the daily build. But carry brief_error so
        # the cards say "data unavailable" instead of "no eBay market" —
        # a missing brief is a pipeline failure, not a no-market signal.
        brief = {"summaries": []}
        brief_error = True
    date_str = datetime.date.today().isoformat()
    for i, a in enumerate(sys.argv[4:]):
        if a == "--date" and i + 1 < len(sys.argv[4:]):
            date_str = sys.argv[5 + i]
    os.makedirs("drafts", exist_ok=True)
    out = f"drafts/{date_str}.html"
    open(out, "w").write(build(sales, finds, brief, date_str, brief_error))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
