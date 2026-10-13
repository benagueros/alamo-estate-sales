#!/usr/bin/env python3
"""Publish a draft issue to Buttondown as a DRAFT email.

Two callers:
- "Send issue" (manual): Ben runs it for a draft file, reviews the
  Buttondown draft, and hits send himself.
- "Build daily issue" (automatic): creates the draft right after assembly
  so the auto-send workflow can publish it after a review window. Pass
  --manifest <path> to record the draft's email ID + creation time for
  the auto-send workflow to pick up.

Ben's controls on an auto draft: edit it in the Buttondown dashboard
(auto-send ships the edited version), or delete it (auto-send treats a
404 as cancelled and skips). Nothing auto-sends a 0-verified draft.

Usage:
    BUTTONDOWN_API_KEY=... python3 send.py drafts/2026-10-05.html
    BUTTONDOWN_API_KEY=... python3 send.py drafts/2026-10-05.html --manifest drafts/manifest.json

API: POST https://api.buttondown.com/v1/emails
Auth: Authorization: Token $BUTTONDOWN_API_KEY
Docs: https://docs.buttondown.com/api-emails-create

email_type is "public" (goes to every subscriber) because the subscriber
list IS the paying list — paid status is managed through Paddle, and the
Paddle webhook (Cloudflare Worker, ops/worker.js) adds buyers as
subscribers via the API. If Buttondown-native paid tiers are ever used
instead, switch this to "premium".
"""
import datetime
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
    manifest_path = None
    if "--manifest" in sys.argv:
        i = sys.argv.index("--manifest")
        if i + 1 < len(sys.argv):
            manifest_path = sys.argv[i + 1]
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
    if manifest_path:
        # The auto-send workflow reads this to find today's draft.
        # verified_finds / sent are filled in by the caller (build-issue)
        # and by auto_send.py; send.py only records what it knows.
        manifest = {
            "email_id": data.get("id"),
            "created_at": datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
            "draft_file": path,
            "subject": payload["subject"],
            "sent": False,
        }
        json.dump(manifest, open(manifest_path, "w"), indent=2)
        print(f"Manifest written: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
