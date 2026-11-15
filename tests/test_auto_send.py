#!/usr/bin/env python3
"""Regression tests for pipeline/auto_send.py gating.

Covers the Nov 2026 FULL review fix: after a successful send POST, the
manifest is marked sent immediately — a confirm GET that still shows
"draft" (Buttondown propagation lag) must not cause a re-POST and a
double-send on the next hourly run. Also covers the standing gates:
0-verified never sends, deleted drafts cancel, fresh/stale drafts skip.
"""
import datetime
import json
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import auto_send


def manifest(**kw):
    m = {"email_id": "em_1", "created_at": (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(hours=5)).isoformat(),
         "verified_finds": 2, "total_finds": 3,
         "subject": "Alamo Estate Deals — test", "sent": False}
    m.update(kw)
    return m


class FakeBD:
    """bd_request stand-in: plays back (status, body) per method+path."""

    def __init__(self, script):
        self.script = script  # {(method, path): [(status, body), ...]}
        self.calls = []

    def __call__(self, method, path, api_key, body=None):
        self.calls.append((method, path))
        queue = self.script.get((method, path), [])
        if queue:
            return queue.pop(0)
        return 404, None


def run_with(manifest_obj, bd):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(manifest_obj, tmp)
    tmp.close()
    with mock.patch.object(auto_send, "bd_request", bd), \
            mock.patch.dict(os.environ, {"BUTTONDOWN_API_KEY": "test"}), \
            mock.patch.object(sys, "argv", ["auto_send.py", tmp.name]):
        code = auto_send.main()
    saved = json.load(open(tmp.name))
    os.unlink(tmp.name)
    posts = [c for c in bd.calls if c[0] == "POST"]
    return code, saved, posts


def check(desc, got, expected):
    ok = got == expected
    print(f"{'OK ' if ok else 'FAIL'} {desc}: got {got!r}, expected {expected!r}")
    return 0 if ok else 1


def main():
    failures = 0

    # --- POST success + confirm GET still "draft": sent once, exit 2 ---
    bd = FakeBD({("GET", "/emails/em_1"): [(200, {"status": "draft"}),
                                            (200, {"status": "draft"})],
                 ("POST", "/emails/em_1/send"): [(200, {})]})
    code, saved, posts = run_with(manifest(), bd)
    failures += check("lagging confirm GET still exits 2 (sent)",
                      code, 2)
    failures += check("manifest marked sent from POST acceptance",
                      saved.get("sent"), True)
    failures += check("POST issued exactly once",
                      len(posts), 1)

    # --- confirm GET shows "sent": same outcome ---
    bd = FakeBD({("GET", "/emails/em_1"): [(200, {"status": "draft"}),
                                            (200, {"status": "sent"})],
                 ("POST", "/emails/em_1/send"): [(200, {})]})
    code, saved, posts = run_with(manifest(), bd)
    failures += check("confirm GET 'sent' exits 2",
                      (code, saved.get("sent")), (2, True))

    # --- 0 verified: never sends ---
    bd = FakeBD({("GET", "/emails/em_1"): [(200, {"status": "draft"})]})
    code, saved, posts = run_with(manifest(verified_finds=0), bd)
    failures += check("0-verified skips without POST",
                      (code, len(posts), saved.get("sent")), (0, 0, False))

    # --- already sent in manifest: skip ---
    bd = FakeBD({})
    code, saved, posts = run_with(manifest(sent=True), bd)
    failures += check("already-sent manifest skips",
                      (code, len(posts)), (0, 0))

    # --- GET 404 (Ben deleted the draft): cancelled, skip ---
    bd = FakeBD({("GET", "/emails/em_1"): [(404, None)]})
    code, saved, posts = run_with(manifest(), bd)
    failures += check("deleted draft marked cancelled",
                      (code, saved.get("cancelled"), len(posts)), (0, True, 0))

    # --- too fresh (1h old): skip, no POST ---
    fresh = manifest(created_at=(
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(hours=1)).isoformat())
    bd = FakeBD({})
    code, saved, posts = run_with(fresh, bd)
    failures += check("fresh draft skips without POST",
                      (code, len(posts)), (0, 0))

    # --- stale (25h old): skip, no POST ---
    stale = manifest(created_at=(
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(hours=25)).isoformat())
    bd = FakeBD({})
    code, saved, posts = run_with(stale, bd)
    failures += check("stale draft skips without POST",
                      (code, len(posts)), (0, 0))

    # --- GET error: exit 1, no POST, not marked sent ---
    bd = FakeBD({("GET", "/emails/em_1"): [(500, None)]})
    code, saved, posts = run_with(manifest(), bd)
    failures += check("GET error exits 1 without POST",
                      (code, len(posts), saved.get("sent")), (1, 0, False))

    # --- POST error: exit 1, not marked sent ---
    bd = FakeBD({("GET", "/emails/em_1"): [(200, {"status": "draft"})],
                 ("POST", "/emails/em_1/send"): [(500, None)]})
    code, saved, posts = run_with(manifest(), bd)
    failures += check("POST error exits 1, manifest not sent",
                      (code, saved.get("sent")), (1, False))

    if failures:
        print(f"{failures} failure(s)")
        return 1
    print("all auto_send cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
