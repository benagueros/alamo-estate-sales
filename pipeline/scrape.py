#!/usr/bin/env python3
"""Scrape estatesales.net San Antonio-area listings.

Discovery hits the metro listing page (https://www.estatesales.net/TX/San-Antonio)
over plain HTTP — its SaleEvent JSON-LD is server-rendered, so no browser is
needed to find sales. Detail pages (descriptions, photos) are Angular and
client-rendered, so those still go through Playwright (headless Chromium) on
GitHub Actions. Selectors were verified against the live DOM Oct 4, 2026
(avoid Angular's _ngcontent-* build attributes; they change).

Why the metro page and not per-zip pages: the per-zip pages ("sales AROUND
<zip>") return a huge radius — a 78240 search surfaced Houston, Austin, Fort
Worth and Dallas listings. The metro page lists actual SA-metro sales.

Emits sales.json: [{id, title, url, address, dates_text, company, phone,
description, photo_urls, picture_count}] for sales that have not ended yet.

Politeness: listing pages are explicitly crawlable per estatesales.net/robots.txt
(only /account, /homepages, /v2, /v3, /legacy, /api/* are disallowed — we
fetch the public listing/sale pages a browser would, never /api/*).
One listing fetch + headless Chromium with a normal UA, ~2s between sale
pages, one metro area, once a day. Only excerpts are kept and every record
links back to the source sale page.

Usage:
    python3 scrape.py --out sales.json [--metro URL] [--max-sales 25]

UPGRADE PATH: photo-based find discovery (vision over photo_urls) consumes
the photo_urls this already collects; no scrape changes needed.
"""
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone

METRO_URL = "https://www.estatesales.net/TX/San-Antonio"

# San Antonio metro: 782xx (city), 780xx (Boerne/Helotes/Fair Oaks),
# 781xx (New Braunfels/Schertz/Cibolo corridor). The metro page is generous
# (it lists Austin-area sales too) — this keeps it to our market.
METRO_ZIP_PREFIXES = ("782", "780", "781")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

DETAIL_JS = """() => ({
  description: ((document.querySelector('app-ck-editor-content .cke-r') || {})
             .innerText || '').trim(),
  photos: [...document.querySelectorAll('img[src*="picturescdn.estatesales.net"]')]
             .map(i => i.src).filter((v, i, a) => a.indexOf(v) === i),
  address: ((document.querySelector('a[href^="https://maps.google.com/maps"]') || {})
             .innerText || '').replace(/\\s+/g, ' ').trim(),
  company: ((document.querySelector('h3.line-clamp-1') || {})
             .innerText || '').trim(),
  phone: ((document.querySelector('a[href^="tel:"]') || {})
             .innerText || '').trim(),
})"""


def discover(metro_url):
    """Return candidate sales from the metro page's SaleEvent JSON-LD."""
    req = urllib.request.Request(metro_url, headers={"User-Agent": UA})
    html_text = urllib.request.urlopen(req, timeout=30).read().decode(
        "utf-8", "replace")
    sales, seen = [], set()
    for m in re.finditer(r'application/ld\+json">(.*?)</script>',
                         html_text, re.S):
        try:
            data = json.loads(m.group(1))
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if not isinstance(it, dict) or it.get("@type") != "SaleEvent":
                continue
            url = it.get("url", "") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            loc = it.get("location", {}) or {}
            addr = loc.get("address", {}) or {}
            org = it.get("organizer", {}) or {}
            images = it.get("image") or []
            zip_code = addr.get("postalCode", "") or ""
            if not zip_code:
                # some listings omit the postal code — it's in the URL too
                m2 = re.search(r"/TX/[^/]+/(\d{5})/", url)
                zip_code = m2.group(1) if m2 else ""
            sales.append({
                "url": url,
                "title": (it.get("name", "") or "").strip()[:160],
                "zip": zip_code,
                "online": loc.get("@type") == "VirtualLocation",
                "city": addr.get("addressLocality", "") or "",
                "address": re.sub(r"\s+", " ",
                                  loc.get("name", "") or "").strip(),
                "company": org.get("name", "") or "",
                "phone": org.get("telephone", "") or "",
                "thumb": images[0] if images else "",
                "start": it.get("startDate", "") or "",
                "end": it.get("endDate", "") or "",
            })
    return sales


def in_metro(sale):
    return sale["zip"].startswith(METRO_ZIP_PREFIXES)


def not_ended(sale):
    """Keep sales whose end date hasn't passed (UTC date comparison)."""
    try:
        end = datetime.fromisoformat(sale["end"].replace("Z", "+00:00"))
    except Exception:
        return True  # no date info — let the detail step decide
    return end.date() >= datetime.now(timezone.utc).date()


def dates_text(start, end):
    try:
        s = datetime.fromisoformat(start.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end.replace("Z", "+00:00"))
        f = lambda d: d.strftime("%b %-d")
        return f"{f(s)} \u2013 {f(e)}" if s.date() != e.date() else f(s)
    except Exception:
        return ""


def main():
    out, metro_url, max_sales = "sales.json", METRO_URL, 25
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--out" and i + 1 < len(args):
            out = args[i + 1]
        if a == "--metro" and i + 1 < len(args):
            metro_url = args[i + 1]
        if a == "--max-sales" and i + 1 < len(args):
            max_sales = int(args[i + 1])
        # --zips accepted and ignored: discovery is metro-wide now.

    sales = discover(metro_url)
    print(f"metro page: {len(sales)} sales listed")
    kept = []
    for s in sales:
        if not in_metro(s):
            print(f"  out-of-area: {s['url']}", file=sys.stderr)
            continue
        if not not_ended(s):
            print(f"  ended: {s['title'][:60]}", file=sys.stderr)
            continue
        kept.append(s)
    sales = kept[:max_sales]
    print(f"{len(sales)} sales to detail")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=UA)
        for s in sales:
            try:
                page.goto(s["url"], wait_until="domcontentloaded",
                          timeout=45000)
                # Fail fast on description-less pages (12s, not 30s): a sale
                # with no description gives identify.py nothing to mine, so
                # don't burn the timeout budget on it.
                page.wait_for_selector("app-ck-editor-content", timeout=12000)
                time.sleep(1)  # let lazy images resolve their src
                d = page.evaluate(DETAIL_JS)
            except Exception as exc:
                print(f"  SKIP {s['url']}: {exc}", file=sys.stderr)
                s["description"] = ""
                s["photo_urls"] = [s["thumb"]] if s.get("thumb") else []
                continue
            s["id"] = s["url"].rstrip("/").rsplit("/", 1)[-1]
            s["description"] = d["description"][:4000]
            # Online auctions (e.g. LiveAuctioneers) embed their catalog URL
            # in the page state — grab it for the "browse the lots" link.
            # (Their lot pages sit behind Incapsula, so we link out rather
            # than scrape estimates.)
            m = re.search(r'"auctionUrl":"(https?://[^"]+)"', page.content())
            s["auction_url"] = m.group(1) if m else ""
            photos = d["photos"] or ([s["thumb"]] if s.get("thumb") else [])
            s["photo_urls"] = [u for u in photos
                               if "placeholder" not in u and "logo" not in u][:12]
            s["picture_count"] = str(len(s["photo_urls"]))
            s["address"] = d["address"] or s.get("address", "")
            s["company"] = d["company"] or s.get("company", "")
            s["phone"] = d["phone"] or s.get("phone", "")
            s["dates_text"] = dates_text(s["start"], s["end"])
            s.pop("thumb", None)
            print(f"  ok {s['id']} ({len(s['photo_urls'])} photos)")
            time.sleep(2)
        browser.close()

    sales = [s for s in sales if len(s.get("description", "")) > 40]
    json.dump(sales, open(out, "w"), indent=2)
    print(f"Wrote {out} ({len(sales)} sales)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
