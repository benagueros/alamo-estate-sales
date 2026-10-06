#!/usr/bin/env python3
"""Scrape estatesales.net San Antonio-area listings.

The site is Angular and client-side rendered — plain HTTP fetches return
only the "Loading Sale..." app shell, so this uses Playwright (headless
Chromium) on GitHub Actions. Selectors were verified against the live DOM
Oct 4, 2026 (avoid Angular's _ngcontent-* build attributes; they change).

Emits sales.json: [{id, title, url, address, dates_text, company, phone,
description, photo_urls, picture_count}] for upcoming sales.

Politeness: listing pages are explicitly crawlable per estatesales.net/robots.txt
(only /account, /homepages, /v2, /v3, /legacy, /api/* are disallowed — we
fetch the public listing/sale pages a browser would, never /api/*).
Headless Chromium with a normal UA, ~2s between sale pages, one metro area,
once a day. Only excerpts are kept and every record links back to the
source sale page.

Usage:
    python3 scrape.py --out sales.json [--zips 78240,78209] [--max-sales 25]

UPGRADE PATH: photo-based find discovery (vision over photo_urls) consumes
the photo_urls this already collects; no scrape changes needed.
"""
import json
import re
import sys
import time

BASE = "https://www.estatesales.net"
LISTING = BASE + "/TX/San-Antonio/{zip}"

# San Antonio metro: 782xx (city), 780xx (Boerne/Helotes/Fair Oaks),
# 781xx (New Braunfels/Schertz/Cibolo corridor). The zip search pages leak
# "nearby" results from Austin (787xx), DFW (761xx), etc. — not our market.
METRO_ZIP_PREFIXES = ("782", "780", "781")


def metro_zip(url):
    m = re.search(r"/TX/[^/]+/(\d{5})/", url or "")
    return m.group(1) if m else ""

# Verified Oct 4, 2026 against the live DOM.
LISTING_CARDS_JS = """() => [...document.querySelectorAll('a.sale-row')]
  .map(a => ({
    url: a.href,
    title: (a.querySelector('.sale-row__details > h3') || {}).innerText || '',
    dates: ((a.querySelector('.sale-row__dates') || {}).innerText || '')
             .replace(/\\s+/g, ' ').trim(),
    picture_count: (((a.querySelector('.sale-row__img__count') || {})
             .innerText || '').replace(/[^0-9]/g, '') || '0'),
    address: ((a.querySelector('.sale-row__address address') || {})
             .innerText || '').replace(/\\s+/g, ' ').trim(),
    company: ((a.querySelector('.sale-row__listed-by') || {})
             .innerText || '').replace(/^Listed\\s+by\\s+/i, '').trim(),
    thumb: (a.querySelector('img.sale-row__img__image') || {}).src || '',
  }))"""

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


def main():
    from playwright.sync_api import sync_playwright

    out, zips, max_sales = "sales.json", ["78240"], 25
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--out" and i + 1 < len(args):
            out = args[i + 1]
        if a == "--zips" and i + 1 < len(args):
            zips = args[i + 1].split(",")
        if a == "--max-sales" and i + 1 < len(args):
            max_sales = int(args[i + 1])

    sales = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"))
        seen = set()
        for z in zips:
            try:
                page.goto(LISTING.format(zip=z), wait_until="domcontentloaded",
                          timeout=45000)
                page.wait_for_selector("a.sale-row", timeout=30000)
                cards = page.evaluate(LISTING_CARDS_JS)
            except Exception as exc:
                print(f"listing page {z} failed: {exc}", file=sys.stderr)
                continue
            for c in cards:
                c["title"] = c["title"].strip()[:160]
                z = metro_zip(c["url"])
                if (c["url"] not in seen and "/TX/" in c["url"]
                        and z.startswith(METRO_ZIP_PREFIXES)):
                    seen.add(c["url"])
                    sales.append(c)
                elif z and not z.startswith(METRO_ZIP_PREFIXES):
                    print(f"  out-of-area: {c['url']}", file=sys.stderr)
            print(f"zip {z}: {len(cards)} cards")
            time.sleep(1)
        sales = sales[:max_sales]
        print(f"{len(sales)} sales to detail")

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
            photos = d["photos"] or ([s["thumb"]] if s.get("thumb") else [])
            s["photo_urls"] = [u for u in photos
                               if "placeholder" not in u and "logo" not in u][:12]
            s["picture_count"] = s.get("picture_count") or str(len(photos))
            s["address"] = d["address"] or s.get("address", "")
            s["company"] = d["company"] or s.get("company", "")
            s["phone"] = d["phone"]
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
