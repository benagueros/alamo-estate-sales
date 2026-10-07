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
    the median, highest (deduped). Readers can spot-check the comps."""
    comps = [c for c in (comp.get("comps") or [])
             if c.get("item_link")
             and isinstance(c.get("sale_price"), (int, float))]
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


def find_card(find, sale, summary):
    photo = find_photo(find, sale)
    comp = summary or {}
    n = comp.get("n", 0)
    if n:
        comps_line = (f"{money(comp['min'])} · {money(comp['max'])} — "
                      f"median ≈ {money(comp['median'])} (n={n})")
        flags = comp.get("flags") or []
        if flags:
            comps_line += f" <em>({' ; '.join(html.escape(f) for f in flags)})</em>"
        links = sold_links_html(comp)
        if links:
            comps_line += f'<br><span class="soldlinks">Sold listings: {links}</span>'
        strong = round(comp["median"] * 0.85, -1)
        buy = round(comp["median"] * 0.70, -1)
        threshold = (f"Under ~{money(strong)} is strong. "
                     f"Under ~{money(buy)}, buy it on the spot.")
    else:
        comps_line = "No same-item solds in the dataset — query too narrow or no eBay market."
        threshold = "No threshold: needs a wider query or a different find."
    addr = sale.get('address', '') or (
        "Online auction" if sale.get('online') else "")
    parts = [sale.get('dates_text', ''), addr, sale.get('company', '')]
    sale_line = " · ".join(html.escape(p) for p in parts if p)
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
    </div>"""


def build(sales, finds, brief, date_str):
    by_id = {s.get("id"): s for s in sales}
    summaries = {s["id"]: s for s in brief.get("summaries", [])}
    cards = []
    for find in finds:
        sale = by_id.get(find.get("sale_id"), {})
        cards.append(find_card(find, sale, summaries.get(find["id"])))
    try:
        pretty = datetime.date.fromisoformat(date_str).strftime("%A, %B %-d, %Y")
    except ValueError:
        pretty = date_str
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
<p><em>{INTRO}</em></p>
{''.join(cards) if cards else '<p>No finds cleared the bar today — see you tomorrow.</p>'}
<footer>
<p><strong>Alamo Estate Deals</strong> — $5/month. Every find, every comp, every morning.</p>
<p>{METHODOLOGY}</p>
</footer>
</div></body></html>"""


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    sales = json.load(open(sys.argv[1]))
    finds = json.load(open(sys.argv[2]))
    brief = json.load(open(sys.argv[3]))
    date_str = datetime.date.today().isoformat()
    for i, a in enumerate(sys.argv[4:]):
        if a == "--date" and i + 1 < len(sys.argv[4:]):
            date_str = sys.argv[5 + i]
    os.makedirs("drafts", exist_ok=True)
    out = f"drafts/{date_str}.html"
    open(out, "w").write(build(sales, finds, brief, date_str))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
