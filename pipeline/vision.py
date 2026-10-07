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
models are discovered live from the API, stable flash first. A 429 tries the
next model (quotas can be per-model); only when every usable model 429s does
the run stop. A single 60s wait-and-retry covers per-minute blips.

Output: finds.json-shaped list, each with "source": "vision". With --merge,
keyword finds are folded in: vision outranks keyword, and for the same sale
a more specific query subsumes a vaguer one ("rolex datejust 36" drops bare
"rolex").

Photos: the model reports the clearest photo number per item ("photo"), and
--merge keyword queries go along as a watchlist so the model locates those
too — each find carries "photo_index" and the issue shows the photo of the
actual item, not the sale cover.

Free-tier math: ~13 sales/day x 1 request (up to 4 photos each) = ~13
requests/day, far under Gemini's free 1,500/day. 8s between requests keeps
us under the ~15 RPM per-minute ceiling even when sibling projects on the
same GCP project (TripSplit OCR, Unspun) are active — quota pools per
project, not per key.

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
# Free tier is ~15 RPM shared across everything on the key's project
# (TripSplit OCR, Unspun deep-dives...). 8s spacing keeps us well under
# even when other projects are active.
REQUEST_SLEEP = 8

EXCLUDE = ["parts", "manual", "brochure", "model", "print",
           "poster", "book"]

PROMPT_TEMPLATE = """You are curating finds for an estate-sale newsletter. These {n} photos are from one estate sale listing titled "{title}".

Identify DISTINCT physical items of notable resale value visible in the photos. Focus on items where you can name a specific brand AND model: wristwatches, handbags, designer furniture, musical instruments, collectible toys/trains/cards, coins/bullion, original art, vehicles/boats, pro power tools, sterling silver.

For each item, provide:
- "label": specific identification, e.g. "Rolex Datejust 36mm, stainless steel, fluted bezel"
- "query": 2-4 word eBay-style search for sold comps, e.g. "rolex datejust 36"
- "confidence": "high" if brand AND model are clearly identifiable from the photos, "medium" if the brand is clear but the exact model is an educated inference
- "detail": one short line naming what you see that supports the ID, referencing photo numbers, e.g. "Photo 2: dial reads DATEJUST, fluted bezel, jubilee bracelet"
- "photo": the number of the photo showing this item most clearly (1-{n})

Rules:
- Only list items where you can name a real brand. Skip generic household goods, ordinary furniture, and decor.
- List at most 4 items, best first. If nothing of clear resale value is identifiable, return {{"items": []}}.
- Respond with JSON only, no other text: {{"items": [{{"label": ..., "query": ..., "confidence": ..., "detail": ..., "photo": 1}}]}}
{watchlist_block}"""


WATCHLIST_INSTRUCTIONS = """Also locate these watchlist items: return "watchlist", an object mapping each query to the number of the photo showing that item most clearly, or null if not clearly visible in any photo.
WATCHLIST: {watchlist_json}
Response shape: {{"items": [...], "watchlist": {{"<query>": 2}}}}
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
    """Stable flash models first, then preview flash, then everything else.
    Preview builds can carry separate, tighter quotas — a 429 on one
    shouldn't block trying a stable model. Skips embedding/TTS models."""
    cands = [m for m in available
             if "embedding" not in m.lower() and "tts" not in m.lower()]

    def rank(m):
        ml = m.lower()
        is_flash = "flash" in ml
        is_preview = "preview" in ml or "-exp" in ml
        tier = (0 if (is_flash and not is_preview)
                else 1 if is_flash else 2)
        return (tier, ml)

    return sorted(cands, key=rank)


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
        try:
            photo = int(it.get("photo"))
            photo_index = photo if photo >= 1 else None
        except (TypeError, ValueError):
            photo_index = None
        out.append({
            "label": label,
            "query": query,
            "weight": 3 if conf == "high" else 2,
            "detail": str(it.get("detail", ""))[:280].strip(),
            "photo_index": photo_index,
        })
    return out[:4]


def parse_watchlist(resp, queries):
    """{query: photo_index or None} for the watchlist. Never raises."""
    try:
        text = resp["candidates"][0]["content"]["parts"][0]["text"]
        wl = json.loads(text).get("watchlist", {})
    except Exception:
        return {}
    out = {}
    for q in queries:
        ph = wl.get(q) if isinstance(wl, dict) else None
        try:
            ph = int(ph)
            out[q] = ph if ph >= 1 else None
        except (TypeError, ValueError):
            out[q] = None
    return out


def slugify(query):
    return re.sub(r"[^a-z0-9]+", "-", query.strip().lower()).strip("-")[:40]


def to_find(item, sale):
    find = {
        "id": slugify(item["query"]),
        "query": item["query"],
        "sale_id": sale.get("id"),
        "sale_title": sale.get("title"),
        "weight": item["weight"],
        "evidence": f"Vision: {item['label']}. {item['detail']}".strip(),
        "exclude": list(EXCLUDE),
        "source": "vision",
    }
    if item.get("photo_index"):
        find["photo_index"] = item["photo_index"]
    return find


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
    kw_by_sale = {}
    for f in keyword_finds:
        kw_by_sale.setdefault(f.get("sale_id"), []).append(f["query"])

    vision_finds = []
    watchlist_photos = {}  # (sale_id, query) -> photo_index
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
            watchlist = kw_by_sale.get(sale.get("id"), [])
            watchlist_block = (WATCHLIST_INSTRUCTIONS.format(
                watchlist_json=json.dumps(watchlist)) if watchlist else "")
            prompt = PROMPT_TEMPLATE.format(
                n=len(images), title=(sale.get("title") or "")[:120],
                watchlist_block=watchlist_block)
            resp, err, tried = None, None, []
            saw_429, saw_other_error = False, False
            # working model first, then the rest — no skip logic needed
            ordered = ([working] if working else []) + \
                      [m for m in models if m != working]
            for m in ordered:
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
                        if e.code == 429:
                            if not waited_429:
                                # Could be per-minute rate limiting rather
                                # than a dead bucket — wait once and retry
                                # the same model before moving on.
                                print("  quota hit (429): waiting 60s, "
                                      "retrying once", file=sys.stderr)
                                time.sleep(60)
                                waited_429 = True
                                continue
                            tried.append(f"{m} (HTTP 429)")
                            print(f"  model {m} quota-exhausted, trying next",
                                  file=sys.stderr)
                            saw_429 = True
                            break
                        tried.append(f"{m} (HTTP {e.code})")
                        saw_other_error = True
                        err = f"HTTP {e.code}: {body}"
                        break
                    except Exception as e:
                        tried.append(f"{m} (error)")
                        saw_other_error = True
                        err = str(e)[:200]
                        break
                if resp is not None:
                    break
            if resp is None:
                if saw_429 and not saw_other_error and tried:
                    # every usable model answered 429: the project's quota
                    # is dead — stop the run instead of hammering it
                    quota_dead = True
                    err = ("project quota exhausted (all models 429): " +
                           ", ".join(tried))
                elif err is None:
                    err = ("no model answered; tried: " + ", ".join(tried)
                           if tried else "no models to try")
            if quota_dead:
                print(f"  vision stopping for this run: quota exhausted: "
                      f"{err} (keyword finds carry the issue)",
                      file=sys.stderr)
                break
            if resp is None:
                print(f"  vision failed for {sale.get('id')}: {err}",
                      file=sys.stderr)
                continue
            if working:
                print(f"  vision model: {working}")
            for item in parse_items(resp):
                vision_finds.append(to_find(item, sale))
            for q, ph in parse_watchlist(resp, watchlist).items():
                if ph:
                    watchlist_photos[(sale.get("id"), q)] = ph
            print(f"  {sale.get('id')}: vision found "
                  f"{len([f for f in vision_finds if f['sale_id'] == sale.get('id')])} item(s)")
            time.sleep(REQUEST_SLEEP)

    finds = (merge_finds(keyword_finds, vision_finds, max_finds)
             if merge_path else vision_finds[:max_finds])
    # keyword finds that survived the merge get their watchlist photo, if any
    for f in finds:
        if f.get("source") == "keyword" and "photo_index" not in f:
            ph = watchlist_photos.get((f.get("sale_id"), f["query"]))
            if ph:
                f["photo_index"] = ph
    json.dump(finds, open(out, "w"), indent=2)
    for f in finds:
        print(f"- {f['id']}: `{f['query']}` [{f.get('source')}] <- "
              f"{(f.get('sale_title') or '')[:50]}")
    print(f"Wrote {out} ({len(finds)} finds, "
          f"{sum(1 for f in finds if f.get('source') == 'vision')} vision)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
