#!/usr/bin/env python3
"""Candidate-find extraction for Alamo Estate Deals.

Reads sales.json (from scrape.py), mines listing descriptions for high-value
signals (brand/model names, collectible categories), and emits finds.json:
one short conjunctive trawl.dev query per candidate find.

This only PROPOSES. The writer curates: the daily issue draft shows each
candidate with its evidence quote and comp brief, and the send step is a
manual review + dispatch. Nothing publishes without a human.

Usage:
    python3 identify.py sales.json [--out finds.json] [--max-finds 4]

UPGRADE PATH: photo-based discovery (vision model over listing photos to
spot valuable items the description never names) slots in here as a second
signal source emitting the same finds.json shape. The rest of the pipeline
does not care where candidates came from.
"""
import json
import re
import sys

# (signal phrase, trawl query, weight). Queries stay short: trawl.dev matches
# every word conjunctively, so brand + model + noun. Extend freely — one line
# per addition.
SIGNALS = [
    # -- furniture brands with real secondary markets --
    ("henredon", "henredon leather sofa", 3),
    ("bernhardt", "bernhardt chair", 3),
    ("lane acclaim", "lane acclaim table", 3),
    ("drexel", "drexel furniture", 2),
    ("thomasville", "thomasville furniture", 2),
    ("baker furniture", "baker furniture", 3),
    ("stickley", "stickley furniture", 3),
    ("heywood-wakefield", "heywood wakefield", 3),
    ("milo baughman", "milo baughman", 3),
    ("adrian pearsall", "adrian pearsall", 3),
    ("danish modern", "danish modern teak", 2),
    ("ethan allen", "ethan allen", 1),
    # -- art glass / pottery --
    ("blenko", "blenko glass", 3),
    ("hull pottery", "hull pottery vase", 3),
    ("fenton", "fenton glass", 2),
    ("murano", "murano glass", 2),
    ("waterford", "waterford crystal", 2),
    ("lalique", "lalique", 3),
    ("fiesta ware", "fiesta ware", 2),
    # -- silver / jewelry --
    ("james avery", "james avery", 3),
    ("lunt sterling", "lunt sterling", 3),
    ("gorham sterling", "gorham sterling", 3),
    ("tiffany", "tiffany sterling", 3),
    ("sterling silver", "sterling silver", 2),
    # -- vehicles / boats --
    ("mazda miata", "mazda miata convertible", 3),
    ("corvette", "corvette", 2),
    ("ford bronco", "ford bronco", 2),
    ("bass tracker", "bass tracker boat", 3),
    ("boston whaler", "boston whaler", 3),
    # -- collectibles / tools / instruments --
    ("tonka", "vintage tonka truck", 3),
    ("lionel", "lionel train", 2),
    ("rolex", "rolex", 3),
    ("gibson", "gibson guitar", 2),
    ("fender", "fender guitar", 2),
    ("snap-on", "snap on tools", 2),
    ("stihl", "stihl chainsaw", 2),
    ("le creuset", "le creuset", 2),
    ("hermes", "hermes", 3),
    ("louis vuitton", "louis vuitton", 2),
    ("oil painting", "oil painting", 1),
    ("persian rug", "persian rug", 2),
    ("slot machine", "slot machine", 2),
    ("jukebox", "jukebox", 2),
]


def find_evidence(description, phrase):
    """Return the sentence containing the signal phrase, for the writer."""
    for sent in re.split(r"(?<=[.!?])\s+", description or ""):
        if phrase.lower() in sent.lower():
            return sent.strip()[:280]
    return ""


def extract(sales, max_finds=4):
    candidates = []
    seen_queries = set()
    for sale in sales:
        desc = sale.get("description", "") or ""
        for phrase, query, weight in SIGNALS:
            if phrase.lower() not in desc.lower():
                continue
            if query in seen_queries:
                continue
            seen_queries.add(query)
            candidates.append({
                "id": re.sub(r"[^a-z0-9]+", "-",
                             query.strip().lower()).strip("-")[:40],
                "query": query,
                "sale_id": sale.get("id"),
                "sale_title": sale.get("title"),
                "weight": weight,
                "evidence": find_evidence(desc, phrase),
                "exclude": ["parts", "manual", "brochure", "model", "print",
                            "poster", "book",
                            # accessories, not the item itself
                            "band", "strap", "box", "clasp"],
                "source": "keyword",
            })
    candidates.sort(key=lambda c: -c["weight"])
    return candidates[:max_finds]


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
        print(__doc__)
        return 2
    sales_path, rest = sys.argv[1], sys.argv[2:]
    out = "finds.json"
    max_finds = 4
    for i, a in enumerate(rest):
        if a == "--out" and i + 1 < len(rest):
            out = rest[i + 1]
        if a == "--max-finds" and i + 1 < len(rest):
            max_finds = int(rest[i + 1])
    sales = json.load(open(sales_path))
    finds = extract(sales, max_finds)
    json.dump(finds, open(out, "w"), indent=2)
    for f in finds:
        print(f"- {f['id']}: `{f['query']}` <- {f['sale_title'][:60]}")
    print(f"Wrote {out} ({len(finds)} candidates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
