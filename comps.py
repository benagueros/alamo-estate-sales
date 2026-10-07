#!/usr/bin/env python3
"""Sold-comp fetcher for the Alamo Estate Deals newsletter.

Reads a finds.json (one short conjunctive query per newsletter find), pulls
eBay sold comps from trawl.dev, applies client-side relevance filtering, and
emits a comp brief in the newsletter's deal-threshold format.

Runs anywhere with Python 3 stdlib only -- designed for GitHub Actions
(trawl's API is not reliably reachable from the Hatch VM, so the VM is not
the execution route).

Usage:
    TRAWL_API_KEY=... python3 comps.py finds.json [--verify] [--out DIR]

Credit math (free tier = 250/mo, no card; per trawl.dev/agent-setup/SKILL.md):
  - /sold costs 1 credit per page of 100 results RETURNED, capped by
    max_pages. Zero-result searches and errors are free.
  - 429 WITH Retry-After = per-second rate: wait and retry once (free).
    429 WITHOUT it = monthly credits spent: STOP, do not retry-loop.
  - --verify fetches /item per surviving comp: 1 credit per successful call
    (404s and removed-listing 200s are free). Capped at 10 comps.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.trawl.dev/ebay/v1"
THIN_N = 5            # fewer than this = flag dataset as thin
VERIFY_CAP = 10       # max /item verifications per run


class QuotaSpent(Exception):
    pass


def api_get(path, params, api_key):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "x-api-key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp), resp.headers
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(2000).decode("utf-8", "replace")
        except Exception:
            body = ""
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After")
            if retry_after:  # per-second rate: wait once, retry once (free)
                try:
                    time.sleep(int(retry_after) + 1)
                except (TypeError, ValueError):
                    time.sleep(61)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.load(resp), resp.headers
            raise QuotaSpent(f"monthly credits spent (no Retry-After); body={body}")
        raise RuntimeError(f"HTTP {exc.code}: {body}")


def same_item(title, query_words, exclude):
    t = (title or "").lower()
    if any(w not in t for w in query_words):
        return False
    # word-boundary matching: "box" must not nuke "boombox"
    return not any(re.search(r"\b" + re.escape(x.lower()) + r"\b", t)
                   for x in exclude)


def fetch_comps(find, api_key):
    """Returns (listings, credits_charged)."""
    q = find["query"]
    params = {"query": q, "max_pages": find.get("max_pages", 2)}
    for k in ("condition", "date_from", "date_to", "marketplace"):
        if k in find:
            params[k] = find[k]
    if "exclude" in find and find["exclude"]:
        params["exclude"] = " ".join(find["exclude"])
    if "category" in find:
        params["category"] = find["category"]

    data, _ = api_get("/sold", params, api_key)
    credits = data.get("credits_charged", 0)
    words = q.lower().split()
    exclude = find.get("exclude", [])
    listings = [
        r for r in data.get("results", [])
        if same_item(r.get("title", ""), words, exclude)
    ]
    return listings, credits


def verify_listing(item_id, api_key):
    """Ben's rule: sold-state verification before any comp publishes."""
    try:
        data, _ = api_get("/item", {"item_id": item_id}, api_key)
    except Exception as exc:
        return {"item_id": item_id, "verified": False, "note": str(exc)[:120]}
    details = data.get("details", data)
    state = details.get("listing_state")
    sales = details.get("sales") or []
    return {
        "item_id": item_id,
        "verified": True,
        "listing_state": state,          # "removed" = real sale, no details
        "sale_price": data.get("sale_price"),
        "best_offer": bool(details.get("best_offer_available")),
        "multi_quantity_sales": len(sales) if state == "active" else 0,
    }


def summarize(find, listings):
    prices = sorted(r["sale_price"] for r in listings
                    if isinstance(r.get("sale_price"), (int, float)))
    n = len(prices)
    if n == 0:
        return {"id": find["id"], "query": find["query"], "n": 0}
    mid = n // 2
    median = (prices[mid - 1] + prices[mid]) / 2 if n % 2 == 0 else prices[mid]
    best_offer = [r for r in listings if r.get("best_offer_available")]
    flags = []
    if n < THIN_N:
        flags.append(f"thin: only {n} same-item sold{'s' if n != 1 else ''} in the dataset")
    if best_offer:
        flags.append(f"{len(best_offer)} of {n} comp(s) were Best-Offer acceptances "
                     "-- flag sold-state verification before publishing")
    return {
        "id": find["id"],
        "query": find["query"],
        "n": n,
        "min": prices[0],
        "max": prices[-1],
        "median": round(median, 2),
        "best_offer_ids": [r.get("item_id") for r in best_offer],
        "flags": flags,
        "comps": [
            {"item_id": r.get("item_id"), "title": r.get("title"),
             "sale_price": r.get("sale_price"),
             "date_sold": r.get("date_sold"),
             "buying_format": r.get("buying_format"),
             "best_offer": bool(r.get("best_offer_available")),
             "item_link": r.get("item_link")}
            for r in listings
        ],
    }


def anchor_comps(comps):
    """The comps behind the published numbers: lowest, closest-to-median,
    highest (deduped). These are the ones the newsletter links to, so these
    are the ones worth spending /item verification credits on."""
    priced = [c for c in comps
              if isinstance(c.get("sale_price"), (int, float))]
    if not priced:
        return []
    by_price = sorted(priced, key=lambda c: c["sale_price"])
    prices = [c["sale_price"] for c in by_price]
    n = len(prices)
    median = (prices[n // 2] if n % 2
              else (prices[n // 2 - 1] + prices[n // 2]) / 2)
    rep = min(by_price, key=lambda c: abs(c["sale_price"] - median))
    anchors, seen = [], set()
    for c in (by_price[0], rep, by_price[-1]):
        key = c.get("item_id") or id(c)
        if key not in seen:
            seen.add(key)
            anchors.append(c)
    return anchors


def summarize_verification(vresults, anchors):
    """Attach sold-state outcomes to the summary so Ben's writer pass can
    see which published comps actually confirmed as sold."""
    by_id = {v["item_id"]: v for v in vresults}
    passed, failed = [], []
    for c in anchors:
        v = by_id.get(c["item_id"], {})
        entry = {"item_id": c["item_id"],
                 "sale_price": c["sale_price"],
                 "title": (c.get("title") or "")[:90]}
        if v.get("verified"):
            entry["listing_state"] = v.get("listing_state")
            entry["best_offer"] = v.get("best_offer")
            passed.append(entry)
        else:
            entry["note"] = v.get("note", "unverified")[:120]
            failed.append(entry)
    return {"checked": len(anchors), "passed": passed, "failed": failed}


def brief_line(s):
    if s["n"] == 0:
        return (f"### {s['id']} (`{s['query']}`)\n"
                "Sold comps: none — query too narrow or no market on eBay. "
                "Widen the query (brand + model + noun) or drop it from the issue.\n")
    flag_txt = f" ({'; '.join(s['flags'])})" if s["flags"] else ""
    fmt = lambda v: f"${v:,.0f}"
    # Suggested starting points for the writer; the published threshold stays
    # an editorial call, never an automated number.
    strong = round(s["median"] * 0.85, -1)
    buy = round(s["median"] * 0.70, -1)
    out = (f"### {s['id']} (`{s['query']}`)\n"
           f"Sold comps {fmt(s['min'])} · {fmt(s['max'])} — median ≈ {fmt(s['median'])} "
           f"(n={s['n']}{flag_txt})\n")
    v = s.get("verification") or {}
    if v.get("checked"):
        npass = len(v.get("passed", []))
        out += (f"Sold-state check: {npass}/{v['checked']} anchor comps "
                f"confirmed sold")
        failed = v.get("failed", [])
        if failed:
            desc = ", ".join(
                f"{fmt(f['sale_price'])} ({f['note']})" for f in failed)
            out += (f" — FAILED: {desc}. "
                    f"(writer: these did NOT confirm as sold; "
                    f"cut or replace before publishing)")
        out += "\n"
    elif s["n"] and not s.get("error"):
        out += ("Sold-state check: not run for this find "
                "(writer: treat comps as unconfirmed)\n")
    out += (f"Deal-threshold starting point (writer adjusts): "
            f"under ~{fmt(strong)} is strong; under ~{fmt(buy)}, buy it on the spot.\n")
    return out


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
        print(__doc__)
        return 2
    finds_path, rest = sys.argv[1], sys.argv[2:]
    out_dir = "."
    verify_cap = VERIFY_CAP
    for i, a in enumerate(rest):
        if a == "--out" and i + 1 < len(rest):
            out_dir = rest[i + 1]
        if a == "--verify-cap" and i + 1 < len(rest):
            verify_cap = int(rest[i + 1])
    verify = "--verify" in rest

    api_key = os.environ.get("TRAWL_API_KEY")
    if not api_key:
        print("TRAWL_API_KEY env var is required (GitHub secret on Actions).")
        return 2

    finds = json.load(open(finds_path))
    os.makedirs(out_dir, exist_ok=True)
    total_credits = 0
    summaries, verifications = [], []
    quota_spent = False

    for idx, find in enumerate(finds):
        if quota_spent:
            summaries.append({"id": find.get("id"), "query": find.get("query"),
                              "n": 0, "error": "trawl quota spent earlier in run"})
            continue
        try:
            listings, credits = fetch_comps(find, api_key)
        except QuotaSpent as exc:
            # Graceful degradation: don't hard-fail the daily build.
            # Remaining finds get n=0 summaries so issue.py renders its
            # "no comps" path and the draft still commits.
            print(f"STOPPING: {exc} — remaining finds get n=0")
            quota_spent = True
            summaries.append({"id": find.get("id"), "query": find.get("query"),
                              "n": 0, "error": f"quota spent: {exc}"})
            continue
        except Exception as exc:
            print(f"find '{find.get('id')}' failed (free, skipped): {exc}")
            summaries.append({"id": find.get("id"), "query": find.get("query"),
                              "n": 0, "error": str(exc)[:200]})
            continue
        total_credits += credits or 0
        s = summarize(find, listings)
        summaries.append(s)
        print(f"{s['id']}: n={s['n']} (credits so far: {total_credits})")
        if verify and s.get("comps"):
            # Verify the anchors — the comps behind the published
            # min/median/max and the sold-listing links — not an arbitrary
            # slice. Ben's rule is about what's published.
            anchors = anchor_comps(s["comps"])[:verify_cap]
            vresults = []
            for c in anchors:
                v = verify_listing(c["item_id"], api_key)
                total_credits += 0 if not v["verified"] else 1
                vresults.append({"find": s["id"], **v})
                verifications.append({"find": s["id"], **v})
            s["verification"] = summarize_verification(vresults, anchors)
            npass = len(s["verification"]["passed"])
            print(f"  verification: {npass}/{len(anchors)} anchors confirmed")

    brief = ("# Comp brief — Alamo Estate Deals\n\n"
             f"Total trawl credits charged this run: ~{total_credits}\n\n"
             + "\n".join(brief_line(s) for s in summaries))
    json.dump({"credits_charged": total_credits, "summaries": summaries,
               "verifications": verifications},
              open(f"{out_dir}/comp-brief.json", "w"), indent=2)
    open(f"{out_dir}/comp-brief.md", "w").write(brief)
    print(f"\nWrote {out_dir}/comp-brief.json + comp-brief.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
