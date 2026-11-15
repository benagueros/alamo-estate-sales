#!/usr/bin/env python3
"""Regression tests for pipeline/issue.py find_card withholding.

Covers the Nov 2026 FULL review fixes: dataset-derived flags ("thin",
"Best-Offer") must never render on a withheld (n=0) card, failure notes
carry reasons without eBay item IDs, and sale URLs with raw spaces are
percent-encoded in links.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import issue


def make_find(**kw):
    f = {"id": "s1-rolex", "query": "rolex datejust 36", "sale_id": "s1",
         "evidence": "Vision: Rolex Datejust. Photo 1: dial reads DATEJUST.",
         "source": "vision", "photo_index": 1}
    f.update(kw)
    return f


def make_sale(**kw):
    s = {"id": "s1", "title": "Estate Sale", "url": "https://example.com/sale/1",
         "address": "123 Main St, San Antonio, TX 78240",
         "dates_text": "Nov 14 – 15", "company": "Estate Co",
         "photo_urls": ["https://example.com/p1.jpg"]}
    s.update(kw)
    return s


def check(desc, got, expected=True):
    ok = got == expected
    print(f"{'OK ' if ok else 'FAIL'} {desc}: got {got!r}")
    return 0 if ok else 1


def main():
    failures = 0

    # --- withheld card with stale dataset flags: none of them render ---
    stale = {"n": 0,
             "verification": {"checked": 3, "passed": [],
                              "failed": [{"item_id": "111",
                                          "note": "listing_state='active': not a completed sale"}]},
             "flags": ["thin: only 3 same-item solds in the dataset",
                       "2 of 3 comp(s) were Best-Offer acceptances"]}
    html = issue.find_card(make_find(), make_sale(), stale)
    failures += check("withheld card shows 'No verified solds'",
                      "No verified solds" in html)
    failures += check("withheld card hides thin flag",
                      "thin:" not in html)
    failures += check("withheld card hides Best-Offer flag",
                      "Best-Offer" not in html)
    failures += check("withheld card hides item IDs from writer note",
                      "111" not in html)
    failures += check("withheld card keeps the failure reason",
                      "listing_state=" in html)
    failures += check("withheld card shows no threshold",
                      "No threshold:" in html)

    # --- withheld flag itself passes through ---
    withheld = {"n": 0,
                "verification": {"checked": 3, "passed": [], "failed": [
                    {"item_id": "111", "note": "listing_state='active'"}]},
                "flags": ["1 of 3 anchor(s) did not confirm — stats withheld"]}
    html = issue.find_card(make_find(), make_sale(), withheld)
    failures += check("withheld message flag renders",
                      "stats withheld" in html)

    # --- verified card renders stats, flags, and shared thresholds ---
    verified = {"n": 3, "min": 100, "max": 300, "median": 200,
                "verification": {"checked": 3,
                                 "passed": [{"item_id": "1"},
                                            {"item_id": "2"},
                                            {"item_id": "3"}],
                                 "failed": []},
                "flags": ["thin: only 3 same-item solds in the dataset"],
                "comps": []}
    html = issue.find_card(make_find(), make_sale(), verified)
    failures += check("verified card shows median",
                      "median ≈ $200" in html)
    failures += check("verified card shows shared thresholds",
                      "Under ~$170 is strong" in html
                      and "Under ~$140, buy it" in html)
    failures += check("verified card keeps thin flag",
                      "thin:" in html)

    # --- verification not run: withheld, distinct message ---
    not_run = {"n": 3, "min": 100, "max": 300, "median": 200, "comps": []}
    html = issue.find_card(make_find(), make_sale(), not_run)
    failures += check("verification-not-run withholds stats",
                      "Verification not run" in html
                      and "median" not in html)

    # --- defense-in-depth: n>0 with failed anchors withholds ---
    bad = {"n": 3, "min": 100, "max": 300, "median": 200,
           "verification": {"checked": 3,
                            "passed": [{"item_id": "1"}],
                            "failed": [{"item_id": "2", "note": "x"}]},
           "comps": []}
    html = issue.find_card(make_find(), make_sale(), bad)
    failures += check("n>0 with failed anchors withholds",
                      "No verified solds" in html
                      and "median ≈ $200" not in html)

    # --- sale URL with a raw space is percent-encoded in the href ---
    spaced = make_sale(url="https://www.estatesales.net/TX/San Antonio/78240/1.html")
    html = issue.find_card(make_find(), spaced, verified)
    failures += check("sale URL space encoded in href",
                      'href="https://www.estatesales.net/TX/San%20Antonio/78240/1.html"' in html)

    if failures:
        print(f"{failures} failure(s)")
        return 1
    print("all find_card cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
