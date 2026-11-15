#!/usr/bin/env python3
"""Regression tests for comps.py: matching, recency, caps, verification.

Covers the Nov 2026 FULL review fixes: word-boundary same_item matching,
multi-word exclude phrases containing query words, default 90-day
date_from, max_pages cap, transient-404 retry, sanitized verify notes,
and presence-check field fallbacks in verify_listing.
"""
import datetime
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import comps


class FakeAPI:
    """api_get stand-in: plays back a script of results/exceptions."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, path, params, api_key):
        self.calls.append((path, params))
        action = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(action, Exception):
            raise action
        return action, {}


def check(desc, got, expected):
    ok = got == expected
    print(f"{'OK ' if ok else 'FAIL'} {desc}: got {got!r}, expected {expected!r}")
    return 0 if ok else 1


def main():
    failures = 0

    # --- same_item: word-boundary matching for query words too ---
    failures += check("query word 'box' does not match 'boombox'",
                      comps.same_item("Boombox stereo", ["box"], []), False)
    failures += check("query word 'box' matches 'wooden box'",
                      comps.same_item("Wooden box", ["box"], []), True)
    failures += check("all query words present",
                      comps.same_item("Rolex Datejust 36",
                                      ["rolex", "datejust", "36"], []), True)
    failures += check("missing query word rejects",
                      comps.same_item("Rolex Datejust",
                                      ["rolex", "datejust", "36"], []), False)
    failures += check("exclude word-boundary ('box' vs 'boombox')",
                      comps.same_item("Boombox stereo", ["boombox"], ["box"]),
                      True)

    # --- fetch_comps: exclude phrases, recency default, page cap ---
    captured = []
    fake = FakeAPI([{"credits_charged": 1, "results": []}])
    with mock.patch.object(comps, "api_get", fake):
        find = {"id": "m", "query": "matisse la danse print",
                "exclude": ["la danse print", "poster"]}
        comps.fetch_comps(find, "key")
    params = fake.calls[0][1]
    failures += check("multi-word exclude phrase containing a query word is dropped",
                      params.get("exclude"), "poster")
    failures += check("date_from defaults to ~90 days ago",
                      params.get("date_from"),
                      (datetime.date.today()
                       - datetime.timedelta(days=90)).isoformat())
    with mock.patch.object(comps, "api_get", fake):
        find = {"id": "m", "query": "rolex", "date_from": "2026-01-01",
                "max_pages": 99}
        comps.fetch_comps(find, "key")
    params = fake.calls[-1][1]
    failures += check("explicit date_from wins over the default",
                      params.get("date_from"), "2026-01-01")
    failures += check("max_pages capped at 5",
                      params.get("max_pages"), 5)
    with mock.patch.object(comps, "api_get", fake):
        find = {"id": "m", "query": "rolex", "max_pages": "abc"}
        comps.fetch_comps(find, "key")
    failures += check("non-numeric max_pages falls back to 2",
                      fake.calls[-1][1].get("max_pages"), 2)

    # --- verify_listing: transient 404 retries once, then succeeds ---
    removed = {"details": {"listing_state": "removed", "sale_price": 100}}
    transient = RuntimeError(
        'HTTP 404: {"error": "details not available yet"}')
    fake = FakeAPI([transient, removed])
    with mock.patch.object(comps, "api_get", fake), \
            mock.patch.object(comps.time, "sleep"):
        v = comps.verify_listing("123", "key")
    failures += check("transient 404 retried, then verified",
                      (v["verified"], v["attempts"]), (True, 2))

    # --- verify_listing: persistent 404 fails closed, note sanitized ---
    fake = FakeAPI([RuntimeError('HTTP 404: {"error": "not found", "x": 1}')])
    with mock.patch.object(comps, "api_get", fake):
        v = comps.verify_listing("123", "key")
    failures += check("persistent 404 verifies False, no retry",
                      (v["verified"], v["attempts"]), (False, 1))
    failures += check("404 note is short, no raw JSON body",
                      (v["note"], "{" in v["note"]),
                      ("listing details unavailable (HTTP 404)", False))

    # --- verify_listing: top-level fallback + presence checks ---
    fake = FakeAPI([{"listing_state": "removed"}])
    with mock.patch.object(comps, "api_get", fake):
        v = comps.verify_listing("123", "key")
    failures += check("top-level listing_state fallback verifies",
                      v["verified"], True)
    # Explicit nested sales=[] must NOT fall through to top-level sales.
    fake = FakeAPI([{"details": {"listing_state": "active", "sales": []},
                     "sales": [{"id": 9}]}])
    with mock.patch.object(comps, "api_get", fake):
        v = comps.verify_listing("123", "key")
    failures += check("explicit nested sales=[] wins over top-level sales",
                      (v["verified"], v["multi_quantity_sales"]), (False, 0))
    # No nested sales key: top-level sales count.
    fake = FakeAPI([{"details": {"listing_state": "active"},
                     "sales": [{"id": 9}]}])
    with mock.patch.object(comps, "api_get", fake):
        v = comps.verify_listing("123", "key")
    failures += check("missing nested sales falls back to top-level",
                      (v["verified"], v["multi_quantity_sales"]), (True, 1))
    # Active with no sales is not a completed sale.
    fake = FakeAPI([{"details": {"listing_state": "active", "sales": []}}])
    with mock.patch.object(comps, "api_get", fake):
        v = comps.verify_listing("123", "key")
    failures += check("active, no sales verifies False",
                      v["verified"], False)
    # QuotaSpent surfaces as a short note, not a raw exception.
    fake = FakeAPI([comps.QuotaSpent("monthly credits spent")])
    with mock.patch.object(comps, "api_get", fake):
        v = comps.verify_listing("123", "key")
    failures += check("quota spent note sanitized",
                      (v["verified"], v["note"]),
                      (False, "trawl credits spent"))

    # --- deal_thresholds: shared helper ---
    failures += check("thresholds for median 200",
                      comps.deal_thresholds(200), (170, 140))
    failures += check("thresholds for median 1000",
                      comps.deal_thresholds(1000), (850, 700))
    failures += check("thresholds floor at $1",
                      comps.deal_thresholds(0), (1, 1))

    if failures:
        print(f"{failures} failure(s)")
        return 1
    print("all comps cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
