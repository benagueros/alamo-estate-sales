#!/usr/bin/env python3
"""Publish a draft issue to Buttondown as a DRAFT email.

Ben reviews the draft in the Buttondown dashboard and hits send himself —
the human gate stays in front of every issue. (One-click upgrade path:
POST /v1/emails/{id}/send after creation; see Buttondown docs.)

Usage:
    BUTTONDOWN_API_KEY=... python3 send.py drafts/2026-10-05.html

API: POST https://api.buttondown.com/v1/emails
Auth: Authorization: Token $BUTTONDOWN_API_KEY
Docs: https://docs.buttondown.com/api-emails-create

email_type is "public" (goes to every subscriber) because the subscriber
list IS the paying list — paid status is managed through Paddle, and the
Paddle webhook (Cloudflare Worker, ops/worker.js) adds buyers as
subscribers via the API. If Buttondown-native paid tiers are ever used
instead, switch this to "premium".
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = "https://api.buttondown.com/v1"


def subject_for(path):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path)
    from datetime import date
    d = m.group(1) if m else date.today().isoformat()
    try:
        pretty = date.fromisoformat(d).strftime("%A, %B %-d")
    except ValueError:
        pretty = d
    return f"Alamo Estate Deals — {pretty}"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    api_key = os.environ.get("BUTTONDOWN_API_KEY")
    if not api_key:
        print("BUTTONDOWN_API_KEY env var is required (GitHub secret on Actions).")
        return 2
    path = sys.argv[1]
    body = open(path).read()
    # Tell Buttondown the body is already rich HTML (fancy mode).
    payload = {
        "subject": subject_for(path),
        "body": "<!-- buttondown-editor-mode: fancy -->\n" + body,
        "email_type": "public",
        "status": "draft",
    }
    req = urllib.request.Request(
        BASE + "/emails",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Token {api_key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        print(f"Buttondown HTTP {exc.code}: "
              f"{exc.read(500).decode('utf-8', 'replace')}")
        return 1
    print(f"Draft created: {data.get('id')} — review and send it from the "
          f"Buttondown dashboard: https://buttondown.com/emails")
    return 0


if __name__ == "__main__":
    sys.exit(main())
