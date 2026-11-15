#!/usr/bin/env python3
"""Shared URL helpers for the Alamo pipeline (stdlib only).

safe_url percent-encodes unsafe characters (raw spaces, etc.) in a URL
while leaving valid URL structure intact, so a listing URL containing a
city name with spaces still works as a link.
"""
import urllib.parse

# Characters that are legal in a URL and must not be encoded. Includes "%"
# so an already-encoded sequence (%20) is left alone, not double-encoded.
_SAFE = "/:?&=#@!$'()*+,;~%"


def safe_url(url):
    """Return url with unsafe characters percent-encoded. Empty/None passes
    through unchanged; a non-string is returned as-is."""
    if not url or not isinstance(url, str):
        return url
    return urllib.parse.quote(url, safe=_SAFE)
