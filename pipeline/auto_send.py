#!/usr/bin/env python3
"""Auto-send today's Buttondown draft after a review window.

Called hourly by the "Auto-send" workflow. Sends ONLY when all hold:
  (a) the manifest says the draft hasn't been sent or cancelled,
  (b) verified_finds >= 1 (hard rule: never auto-send a 0-verified draft),
  (c) the draft is at least REVIEW_WINDOW_HOURS old (Ben's adjustment
      window — the clock starts at draft creation, not at 6am, so late
      builds still leave him the full window),
  (d) the draft is less than 24h old (a stale manifest from a failed
      build must not send yesterday's draft today),
  (e) Buttondown still reports the email as a draft (not sent, not
      deleted — a 404 means Ben deleted it, which is the cancel switch).

Ben's controls: edit the draft in Buttondown (this ships the edited
version), or delete it (this skips). If the draft is too fresh, this run
exits quietly and a later hourly run sends it.

Usage:
    BUTTONDOWN_API_KEY=... python3 pipeline/auto_send.py [manifest]
    (default manifest: drafts/manifest.json)

Exit 0 in every expected case (sent / skipped / cancelled). Exit 1 only
on an unexpected error (Buttondown API failure, corrupt manifest) so the
workflow logs it and a later run retries.
"""
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://api.buttondown.com/v1"
REVIEW_WINDOW_HOURS = 4
MAX_AGE_HOURS = 24


def bd_request(method, path, api_key, body=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Token {api_key}",
                 "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, None


def main():
    manifest_path = sys.argv[1] if len(sys.argv) > 1 else "drafts/manifest.json"
    api_key = os.environ.get("BUTTONDOWN_API_KEY")
    if not api_key:
        print("BUTTONDOWN_API_KEY env var is required (GitHub secret on Actions).")
        return 1
    try:
        manifest = json.load(open(manifest_path))
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"No manifest at {manifest_path} — nothing to send.")
        return 0

    email_id = manifest.get("email_id")
    if not email_id:
        print("Manifest has no email_id — nothing to send.")
        return 0
    if manifest.get("sent"):
        print(f"Draft {email_id} already sent — skipping.")
        return 0
    if manifest.get("cancelled"):
        print(f"Draft {email_id} already cancelled — skipping.")
        return 0

    # Hard rule: never auto-send a draft with zero verified finds.
    if manifest.get("verified_finds", 0) < 1:
        print(f"Draft {email_id}: 0 verified finds — never auto-sends. "
              f"Ben decides manually.")
        return 0

    # Review window: age from draft creation, not from the 6am schedule.
    try:
        created = datetime.datetime.fromisoformat(manifest["created_at"])
    except (KeyError, ValueError):
        print(f"Manifest has no usable created_at — refusing to send.")
        return 1
    now = datetime.datetime.now(datetime.timezone.utc)
    age_hours = (now - created).total_seconds() / 3600
    if age_hours < REVIEW_WINDOW_HOURS:
        print(f"Draft {email_id} is {age_hours:.1f}h old — review window "
              f"({REVIEW_WINDOW_HOURS}h) not elapsed. Skipping.")
        return 0
    if age_hours >= MAX_AGE_HOURS:
        print(f"Draft {email_id} is {age_hours:.1f}h old — stale "
              f"(>{MAX_AGE_HOURS}h). Not sending yesterday's draft today.")
        return 0

    # Buttondown is the source of truth: confirm it's still a draft.
    status, email = bd_request("GET", f"/emails/{email_id}", api_key)
    if status == 404:
        # Ben deleted the draft — that's the cancel switch.
        manifest["cancelled"] = True
        json.dump(manifest, open(manifest_path, "w"), indent=2)
        print(f"Draft {email_id} not found in Buttondown (deleted) — "
              f"marked cancelled. Nothing sent.")
        return 0
    if status != 200:
        print(f"Buttondown GET /emails/{email_id} returned {status} — "
              f"retrying next hour.")
        return 1
    if email.get("status") != "draft":
        # Already sent (e.g. Ben sent it manually) — record and move on.
        manifest["sent"] = True
        json.dump(manifest, open(manifest_path, "w"), indent=2)
        print(f"Draft {email_id} already {email.get('status')} — "
              f"marked sent. Nothing to do.")
        return 0

    # All gates pass — send Ben's version (including any edits he made).
    status, _ = bd_request("POST", f"/emails/{email_id}/send", api_key)
    if status not in (200, 201, 202):
        print(f"Buttondown POST /emails/{email_id}/send returned {status} — "
              f"retrying next hour.")
        return 1

    # Confirm the send landed before marking it sent, so a flaky response
    # can't cause a double-send on the next run.
    status, email = bd_request("GET", f"/emails/{email_id}", api_key)
    if status == 200 and email.get("status") == "sent":
        manifest["sent"] = True
        json.dump(manifest, open(manifest_path, "w"), indent=2)
        print(f"Sent draft {email_id} ({manifest.get('subject')}).")
        return 0
    print(f"Send issued for {email_id} but status is "
          f"{email.get('status') if email else 'unknown'} — "
          f"will re-check next hour.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
