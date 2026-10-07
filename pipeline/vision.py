#!/usr/bin/env python3
"""Vision-based find identification for Alamo Estate Deals.

Looks at listing photos (collected by scrape.py) with a vision model and
identifies SPECIFIC valuable items — brand + model, not just brand. A bare
"rolex" trawl query spans $3 to $41,000 and its median is noise; "rolex
datejust 36" is a real comp set. This is the specificity upgrade over
keyword mining (identify.py), which stays as the cheap breadth fallback.

Usage:
    python3 vision.py sales.json --out finds_vision.json
    python3 vision.py sales.json --merge finds_kw.json --out finds.json

Auth: GEMINI_API_KEY env (free Google AI Studio key). If missing, vision is
skipped and --merge output is just the keyword finds — the pipeline never
breaks for lack of a key.
Model chain: GEMINI_MODELS env (comma-separated) wins when set; otherwise
models are discovered live from the API, flash models first, so a renamed
or deprecated default can't 404 forever.

Output: finds.json-shaped list, each with "source": "vision". With --merge,
keyword finds are folded in: vision outranks keyword, and for the same sale
a more specific query subsumes a vaguer one ("rolex datejust 36" drops bare
"rolex").

Free-tier math: ~13 sales/day x 1 request (up to 4 photos each) = ~13
requests/day, far under Gemini's free 1,500/day. 2s between requests.

UPGRADE PATH: per-photo region notes (which photo shows the item) already
flow into the evidence string; a future pass could crop to the item for a
tighter ID.
"""
import base64
import json
import os
import re
import sys
import time
import urllib.request

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
# Explicit chain wins when set; otherwise models are discovered live from the
# API (flash models first) so a renamed/deprecated default can't 404 forever.
GEMINI_MODELS_ENV = [m.strip() for m in os.environ.get(
    "GEMINI_MODELS", "").split(",") if m.strip()]

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

MAX_PHOTOS = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
REQUEST_SLEEP = 2

EXCLUDE = ["parts", "manual", "brochure", "model", "print",
           "poster", "book"]

PROMPT_TEMPLATE = """You are curating finds for an estate-sale newsletter. These {n} photos are from one estate sale listing titled "{title}".

Identify DISTINCT physical items of notable resale value visible in the photos. Focus on items where you can name a specific brand AND model: wristwatches, handbags, designer furniture, musical instruments, collectible toys/trains/cards, coins/bullion, original art, vehicles/boats, pro power tools, sterling silver.

For each item, provide:
- "label": specific identification, e.g. "Rolex Datejust 36mm, stainless steel, fluted bezel"
- "query": 2-4 word eBay-style search for sold comps, e.g. "rolex datejust 36"
- "confidence": "high" if brand AND model are clearly identifiable from the photos, "medium" if the brand is clear but the exact model is an educated inference
- "detail": one short line naming what you see that supports the ID, referencing photo numbers, e.g. "Photo 2: dial reads DATEJUST, fluted bezel, jubilee bracelet"

Rules:
- Only list items where you can name a real brand. Skip generic household goods, ordinary furniture, and decor.
- List at most 4 items, best first. If nothing of clear resale value is identifiable, return {{"items": []}}.
- Respond with JSON only, no other text.
"""


def download_image(url):
    """Return (mime_type, bytes) or None. Size-capped, UA-spoofed."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            mime = (r.headers.get_content_type() or "image/jpeg").split(";")[0]
            if not mime.startswith("image/"):
                return None
            data = r.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                return None
            return mime, data
    except Exception as exc:
        print(f"    photo download failed: {exc}", file=sys.stderr)
        return None


def list_models():
    """Model names available to this key, via the API. Raises on error."""
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": GEMINI_API_KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    return [m["name"].replace("models/", "")
            for m in data.get("models", []) if "name" in m]


def pick_models(available):
    """Flash models first (cheap, fast, vision-capable), newest-looking first.
    Skips embedding/specialist models."""
    cands = [m for m in available
             if "embedding" not in m.lower() and "tts" not in m.lower()]
    flash = sorted((m for m in cands if "flash" in m.lower()),
                   key=lambda m: m.lower(), reverse=True)
    rest = [m for m in cands if "flash" not in m.lower()]
    return flash + rest


def resolve_models():
    """Ordered model names to try: explicit GEMINI_MODELS env first, then
    live discovery (flash first)."""
    if GEMINI_MODELS_ENV:
        return list(GEMINI_MODELS_ENV)
    try:
        models = pick_models(list_models())
        if models:
            print(f"  discovered models: {', '.join(models[:4])}"
                  + ("..." if len(models) > 4 else ""))
            return models
    except Exception as exc:
        print(f"  model discovery failed: {exc}", file=sys.stderr)
    # last resort: recently valid flash names
    return ["gemini-3.8-flash", "gemini-3-flash-preview",
            "gemini-2.5-flash", "gemini-2.0-flash"]


def call_gemini(model, prompt, images):
    """One generateContent call. Returns parsed JSON dict. Raises on error."""
    parts = [{"text": prompt}]
    for i, (mime, data) in enumerate(images):
        parts.append({"text": f"Photo {i + 1}:"})
        parts.append({"inline_data": {
            "mime_type": mime,
            "data": base64.b64encode(data).decode("ascii"),
        }})
    body = json.dumps({
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.2,
            "maxOutputTokens": 1024,
        },
    }).encode()
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent",
        data=body,
        headers={"Content-Type": "application/json",
                 "x-goog-api-key": GEMINI_API_KEY},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def parse_items(resp):
    """Extract the item list from a generateContent response. Never raises."""
    try:
        text = resp["candidates"][0]["content"]["parts"][0]["text"]
        items = json.loads(text).get("items", [])
    except Exception:
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label", ""))[:160].strip()
        query = re.sub(r"[^a-z0-9 ]+", "",
                       str(it.get("query", "")).lower()).strip()
        query = re.sub(r"\s+", " ", query)
        if not label or len(query.split()) < 2:
            continue
        conf = str(it.get("confidence", "")).lower().strip()
        if conf not in ("high", "medium"):
            continue
        out.append({
            "label": label,
            "query": query,
            "weight": 3 if conf == "high" else 2,
            "detail": str(it.get("detail", ""))[:280].strip(),
        })
    return out[:4]


def slugify(query):
    return re.sub(r"[^a-z0-9]+", "-", query.strip().lower()).strip("-")[:40]


def to_find(item, sale):
    return {
        "id": slugify(item["query"]),
        "query": item["query"],
        "sale_id": sale.get("id"),
        "sale_title": sale.get("title"),
        "weight": item["weight"],
        "evidence": f"Vision: {item['label']}. {item['detail']}".strip(),
        "exclude": list(EXCLUDE),
        "source": "vision",
    }


def drop_subsumed(finds):
    """For each sale, drop finds whose query is strictly vaguer than another
    find's query for the same sale ("rolex" loses to "rolex datejust 36")."""
    by_sale = {}
    for f in finds:
        by_sale.setdefault(f.get("sale_id"), []).append(f)
    out = []
    for fs in by_sale.values():
        for f in fs:
            ft = set(f["query"].split())
            if any(set(g["query"].split()) > ft for g in fs if g is not f):
                continue
            out.append(f)
    return out


def merge_finds(keyword_finds, vision_finds, max_finds=4):
    """Vision outranks keyword; vaguer same-sale queries are dropped."""
    cands = drop_subsumed(list(keyword_finds) + list(vision_finds))
    ranked = sorted(
        ((f.get("weight", 0), 0 if f.get("source") == "vision" else 1, f)
         for f in cands),
        key=lambda t: (-t[0], t[1]),
    )
    out, seen = [], set()
    for _, _, f in ranked:
        key = (f.get("sale_id"), f["query"])
        if key in seen or len(out) >= max_finds:
            continue
        seen.add(key)
        out.append(f)
    return out


def main():
    if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
        print(__doc__)
        return 2
    sales_path, rest = sys.argv[1], sys.argv[2:]
    out, merge_path, max_finds, max_photos = "finds_vision.json", None, 4, MAX_PHOTOS
    for i, a in enumerate(rest):
        if a == "--out" and i + 1 < len(rest):
            out = rest[i + 1]
        if a == "--merge" and i + 1 < len(rest):
            merge_path = rest[i + 1]
        if a == "--max-finds" and i + 1 < len(rest):
            max_finds = int(rest[i + 1])
        if a == "--max-photos" and i + 1 < len(rest):
            max_photos = int(rest[i + 1])

    sales = json.load(open(sales_path))
    keyword_finds = json.load(open(merge_path)) if merge_path else []

    vision_finds = []
    if not GEMINI_API_KEY:
        print("vision skipped: GEMINI_API_KEY not set (keyword finds only)")
    else:
        models = resolve_models()
        working = None
        quota_dead, waited_429 = False, False
        for sale in sales:
            if quota_dead:
                break
            images = []
            for u in (sale.get("photo_urls") or [])[:max_photos]:
                img = download_image(u)
                if img:
                    images.append(img)
            if not images:
                continue
            prompt = PROMPT_TEMPLATE.format(
                n=len(images), title=(sale.get("title") or "")[:120])
            resp, err, tried = None, None, []
            for m in models:
                if working and m != working:
                    continue
                while True:  # inner loop so a 429 can retry the SAME model
                    try:
                        resp = call_gemini(m, prompt, images)
                        working = m
                        break
                    except urllib.error.HTTPError as e:
                        body = e.read().decode("utf-8", "replace")[:300]
                        if e.code == 404:
                            tried.append(f"{m} (HTTP 404)")
                            print(f"  model {m} not found, trying next",
                                  file=sys.stderr)
                            break
                        if e.code == 429 and not waited_429:
                            # Could be per-minute rate limiting rather than
                            # the daily quota — wait once and retry the same
                            # model before giving up.
                            print("  quota hit (429): waiting 60s, "
                                  "retrying once", file=sys.stderr)
                            time.sleep(60)
                            waited_429 = True
                            continue
                        tried.append(f"{m} (HTTP {e.code})")
                        err = f"HTTP {e.code}: {body}"
                        if e.code == 429:
                            quota_dead = True
                        break
                    except Exception as e:
                        tried.append(f"{m} (error)")
                        err = str(e)[:200]
                        break
                if resp is not None or quota_dead:
                    break
            if quota_dead:
                print("  vision stopping for this run: quota exhausted "
                      "(keyword finds carry the issue)", file=sys.stderr)
                break
            if resp is None and err is None:
                err = ("no model answered; tried: " + ", ".join(tried)
                       if tried else "no models to try")
            if resp is None:
                print(f"  vision failed for {sale.get('id')}: {err}",
                      file=sys.stderr)
                continue
            if working:
                print(f"  vision model: {working}")
            for item in parse_items(resp):
                vision_finds.append(to_find(item, sale))
            print(f"  {sale.get('id')}: vision found "
                  f"{len([f for f in vision_finds if f['sale_id'] == sale.get('id')])} item(s)")
            time.sleep(REQUEST_SLEEP)

    finds = (merge_finds(keyword_finds, vision_finds, max_finds)
             if merge_path else vision_finds[:max_finds])
    json.dump(finds, open(out, "w"), indent=2)
    for f in finds:
        print(f"- {f['id']}: `{f['query']}` [{f.get('source')}] <- "
              f"{(f.get('sale_title') or '')[:50]}")
    print(f"Wrote {out} ({len(finds)} finds, "
          f"{sum(1 for f in finds if f.get('source') == 'vision')} vision)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
