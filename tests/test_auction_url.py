#!/usr/bin/env python3
"""Regression tests for extract_auction_url in pipeline/scrape.py.

Covers the accept/reject shapes: plain http(s), JSON-escaped slashes,
protocol-relative URLs, and garbage/missing inputs.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
from scrape import extract_auction_url

CASES = [
    # (description, page_content, expected)
    ("plain https",
     '{"auctionUrl":"https://example.com/auction/123"}',
     "https://example.com/auction/123"),
    ("plain http",
     '{"auctionUrl":"http://example.com/auction/123"}',
     "http://example.com/auction/123"),
    ("JSON-escaped slashes",
     '{"auctionUrl":"https:\\/\\/example.com\\/auction\\/123"}',
     "https://example.com/auction/123"),
    ("protocol-relative",
     '{"auctionUrl":"//example.com/auction/123"}',
     "https://example.com/auction/123"),
    ("escaped protocol-relative",
     '{"auctionUrl":"\\/\\/example.com\\/auction"}',
     "https://example.com/auction"),
    ("garbage rejected",
     '{"auctionUrl":"not-a-url"}',
     ""),
    ("empty value",
     '{"auctionUrl":""}',
     ""),
    ("missing key",
     '{"other":"https://example.com"}',
     ""),
    ("empty page",
     "",
     ""),
]


def main():
    failures = 0
    for desc, content, expected in CASES:
        got = extract_auction_url(content)
        status = "OK " if got == expected else "FAIL"
        if got != expected:
            failures += 1
        print(f"{status} {desc}: got {got!r}, expected {expected!r}")
    if failures:
        print(f"{failures} failure(s)")
        return 1
    print(f"all {len(CASES)} cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
