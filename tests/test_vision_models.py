#!/usr/bin/env python3
"""Regression tests for pipeline/vision.py model resolution.

Covers the Nov 2026 FULL review fixes: the GEMINI_MODELS env chain is
additive (discovered models still follow it as fallback — a dead env
entry must not kill the run), and the last-resort fallback list contains
no nonexistent model names.
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pipeline"))
import vision


def check(desc, got, expected=True):
    ok = got == expected
    print(f"{'OK ' if ok else 'FAIL'} {desc}: got {got!r}")
    return 0 if ok else 1


def main():
    failures = 0

    failures += check("fallback list has no gemini-3.8-flash",
                      "gemini-3.8-flash" not in vision.FALLBACK_MODELS)
    failures += check("fallback list is non-empty",
                      len(vision.FALLBACK_MODELS) > 0)

    # Env chain + discovery: env first, discovered as fallback, deduped.
    # (GEMINI_MODELS_ENV is read at call time, so patch the global.)
    with mock.patch.object(vision, "GEMINI_MODELS_ENV", ["gemini-2.5-flash"]), \
            mock.patch.object(vision, "list_models",
                              return_value=["gemini-2.5-flash",
                                            "gemini-2.0-flash"]):
        models = vision.resolve_models()
    failures += check("env model stays first",
                      models[0], "gemini-2.5-flash")
    failures += check("discovered model follows env as fallback",
                      "gemini-2.0-flash" in models)
    failures += check("no duplicates in resolved chain",
                      len(models) == len(set(models)))

    # Discovery failure + no env: falls back to FALLBACK_MODELS.
    with mock.patch.object(vision, "GEMINI_MODELS_ENV", []), \
            mock.patch.object(vision, "list_models",
                              side_effect=RuntimeError("down")):
        models = vision.resolve_models()
    failures += check("discovery failure returns fallback list",
                      models, vision.FALLBACK_MODELS)

    # Env with a dead name + working discovery: dead name first, live
    # models still present behind it (the 404 path in main() skips on).
    with mock.patch.object(vision, "GEMINI_MODELS_ENV", ["gemini-9.9-fake"]), \
            mock.patch.object(vision, "list_models",
                              return_value=["gemini-2.5-flash"]):
        models = vision.resolve_models()
    failures += check("dead env entry does not drop discovered fallback",
                      "gemini-2.5-flash" in models)

    if failures:
        print(f"{failures} failure(s)")
        return 1
    print("all vision model cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
