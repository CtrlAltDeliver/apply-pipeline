"""Tests for the discovery-engine filters.

Runnable two ways:
    pytest
    python3 tests/test_filters.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discover  # noqa: E402
import title_filter  # noqa: E402
from normalize import normalize_title, normalize_company  # noqa: E402


def test_title_filter_positive():
    for t in title_filter.POSITIVE_CASES:
        ok, reason = title_filter.matches(t)
        assert ok, f"{t!r} should match but did not ({reason})"


def test_title_filter_negative():
    for t in title_filter.NEGATIVE_CASES:
        ok, reason = title_filter.matches(t)
        assert not ok, f"{t!r} should NOT match but did ({reason})"


def test_location_tiers():
    cases = {
        "Calgary, AB, Canada": "preferred",
        "Remote - Canada": "remote_home",
        "Toronto, ON": "conditional",
        "Montreal, QC": "reject",          # other home city, onsite only
        "San Francisco, CA": "reject",     # foreign
        "Remote": "maybe",                 # unspecified remote
        "New York; Toronto, Canada": "conditional",  # multi-loc, home wins
    }
    for loc, expected in cases.items():
        assert discover.classify_location(loc) == expected, loc


def test_salary_parsing():
    s = discover._parse_salary("$120,000 - $180,000", location="Toronto, ON")
    assert s and s["min"] == 120_000 and s["max"] == 180_000

    # USD converts up into CAD.
    s = discover._parse_salary("USD 100,000 to 150,000", location="Remote - US")
    assert s and s["currency"] == "USD" and s["max"] > 150_000

    # Junk numbers (too small / too large) are rejected.
    assert discover._parse_salary("call 555-0100 for 5 openings") is None


def test_normalize():
    assert normalize_title("Sr. Technical Program Manager II") == "senior technical program manager"
    assert normalize_company("Scribd Inc") == normalize_company("Scribd")
    assert normalize_company("CNN (Warner Bros. Discovery)") == "cnn"


def test_seen_dedup(tmp_path=None):
    import json
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "seen.json")
    with open(p, "w") as f:
        json.dump({
            "applied_companies": ["Acme Corp"],
            "declined_roles": [{"company": "Foo", "title": "Senior TPM"}],
        }, f)
    applied, declined = discover.load_seen(p)
    assert normalize_company("Acme") in applied
    assert (normalize_company("Foo"), normalize_title("Senior TPM")) in declined
    # Missing file → empty sets, no crash.
    assert discover.load_seen(os.path.join(d, "nope.json")) == (set(), set())


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed} / {len(fns)} passed")
    sys.exit(1 if failed else 0)
